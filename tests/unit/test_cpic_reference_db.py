"""tests/unit/test_cpic_reference_db.py — канон CPIC в БД (нить diagnosis-hardcode A2-full).

Покрывает cpic_reference_db:
  - seed идемпотентность + пересев при смене SEED_VERSION;
  - 3 проекции засеяны + референц-полнота (гены/препараты проекций ⊆ каталог);
  - build_drug_interactions ОБА направления:
      • тенант с синтетическим профилем видит СВОИ criticals (curated: абакавир;
        ген-импликация: 5-фторурацил);
      • тенант без генома → всё «не определён», ноль criticals, НИ одного чужого диплотипа;
      • другой фенотип → fallback на ген-импликацию, без фабрикации;
      • R4: при одном SLCO1B1 Decreased симва=critical, права=ok (риск = пара препарат×фенотип);
  - LEAK-guard: ни один тенант не получает диплотип (вида *N/*M) — только класс фенотипа;
  - get_gene_implications substring-семантика;
  - GOLDEN-паритет: pharmaco_context._get_implications(conn) == get_gene_implications
    (побайтово — консилиум не должен «поехать» при переносе _IMPLICATIONS в БД).
"""
from __future__ import annotations

import re
import sqlite3
import pytest

import cpic_reference_db as cr
import pharmaco_context as pc


@pytest.fixture(autouse=True)
def _no_install_overlay(monkeypatch, tmp_path):
    """Публичное поведение: без приватного слоя кураторских текстов установки."""
    monkeypatch.setattr(cr, "CURATED_OVERLAY", tmp_path / "absent.json")


def _seeded() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    cr.seed(conn)
    return conn


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------

class TestSeed:
    def test_three_projections_seeded(self):
        c = _seeded()
        assert c.execute("SELECT COUNT(*) FROM cpic_drug_catalog").fetchone()[0] == 21
        assert c.execute("SELECT COUNT(*) FROM cpic_drug_risk").fetchone()[0] == len(cr._snapshot_rows())
        assert c.execute("SELECT COUNT(*) FROM cpic_gene_implication").fetchone()[0] == 18

    def test_single_seed_version(self):
        c = _seeded()
        vers = set()
        for t in ("cpic_drug_catalog", "cpic_drug_risk", "cpic_gene_implication"):
            vers |= {r[0] for r in c.execute(f"SELECT DISTINCT seed_version FROM {t}")}
        assert len(vers) == 1 and next(iter(vers)).startswith(cr.SEED_VERSION + "+")

    def test_idempotent(self):
        c = _seeded()
        cr.seed(c)  # второй прогон — no-op
        assert c.execute("SELECT COUNT(*) FROM cpic_drug_catalog").fetchone()[0] == 21

    def test_reseed_on_version_bump(self, monkeypatch):
        c = _seeded()
        c.execute("DELETE FROM cpic_drug_catalog WHERE drug_id='abacavir'")
        c.commit()
        assert c.execute("SELECT COUNT(*) FROM cpic_drug_catalog").fetchone()[0] == 20
        monkeypatch.setattr(cr, "SEED_VERSION", "test-bump-v2")
        cr.seed(c)  # версия сменилась → полный пересев
        assert c.execute("SELECT COUNT(*) FROM cpic_drug_catalog").fetchone()[0] == 21

    def test_referential_completeness(self):
        c = _seeded()
        cat_drugs = {r[0] for r in c.execute("SELECT drug_id FROM cpic_drug_catalog")}
        cat_genes = {r[0] for r in c.execute("SELECT gene FROM cpic_drug_catalog")}
        risk_drugs = {r[0] for r in c.execute("SELECT drug_id FROM cpic_drug_risk")}
        risk_genes = {r[0] for r in c.execute("SELECT gene FROM cpic_drug_risk")}
        impl_genes = {r[0] for r in c.execute("SELECT gene FROM cpic_gene_implication")}
        assert risk_drugs <= cat_drugs
        assert risk_genes <= cat_genes
        assert impl_genes <= cat_genes


# ---------------------------------------------------------------------------
# build_drug_interactions — оба направления + утечка
# ---------------------------------------------------------------------------

# Синтетический профиль: покрывает обе ветки
# critical — curated drug_risk (HLA-B → абакавир) и fallback по 🚨-импликации
# (DPYD Poor → 5-фторурацил); SLCO1B1 не задан → симвастатин «не определён».
_SYNTH = {
    "CYP2C9": "Poor Metabolizer",
    "CYP2C19": "Intermediate Metabolizer",
    "DPYD": "Poor Metabolizer",
    "HLA-B": "HLA-B*57:01 Carrier (het)",
}


def _criticals(rows):
    return {r["drug"].split("/")[0].strip() for r in rows if r["risk_level"] == "critical"}


_DIPLOTYPE_RE = re.compile(r"\*\d+[A-Za-z]?\s*/\s*\*\d+")


def _has_diplotype_leak(rows):
    return [r for r in rows if _DIPLOTYPE_RE.search(r["phenotype_context"] or "")]


class TestBuildProfiled:
    def test_profiled_tenant_sees_own_criticals(self):
        c = _seeded()
        rows = cr.build_drug_interactions(_SYNTH, c)
        crit = _criticals(rows)
        assert "Абакавир" in crit          # curated drug_risk
        assert "5-Фторурацил" in crit      # fallback по 🚨-импликации
        assert "Симвастатин" not in crit   # гена нет в профиле → не фабрикуем

    def test_profiled_no_diplotype_leak_in_labels(self):
        """Диплотип НЕ утекает даже в собственный вид тенанта —
        показываем класс, не аллели."""
        c = _seeded()
        rows = cr.build_drug_interactions(_SYNTH, c)
        assert _has_diplotype_leak(rows) == []

    def test_r4_same_phenotype_different_drug_risk(self):
        """R4: при одном SLCO1B1 Decreased симвастатин=critical, правастатин=ok."""
        c = _seeded()
        rows = cr.build_drug_interactions({"SLCO1B1": "Decreased Function"}, c)
        by = {r["drug"].split("/")[0].strip(): r["risk_level"] for r in rows if r["gene"] == "SLCO1B1"}
        assert by["Симвастатин"] == "critical"
        assert by["Правастатин"] == "ok"
        assert by["Аторвастатин"] == "warning"


class TestBuildEmptyTenant:
    """Вымышленный тенант без генома — choice 2: нейтральный справочник."""

    def test_all_undetermined(self):
        c = _seeded()
        rows = cr.build_drug_interactions({}, c)
        assert len(rows) == 21
        assert all(r["phenotype_context"] == "не определён" for r in rows)

    def test_zero_criticals(self):
        """Пустой геном → баннер «персональные риски» скрыт (ноль critical)."""
        c = _seeded()
        rows = cr.build_drug_interactions({}, c)
        assert _criticals(rows) == set()

    def test_never_sees_foreign_data(self):
        """Ключевой LEAK-тест: тенант без генома НЕ видит чужой риск/диплотип."""
        c = _seeded()
        rows = cr.build_drug_interactions({}, c)
        assert _has_diplotype_leak(rows) == []
        simva = [r for r in rows if r["drug"].startswith("Симвастатин")][0]
        assert simva["risk_level"] == "unknown"
        assert "SLCO1B1" not in simva["recommendation"] or "не определён" in simva["phenotype_context"]


class TestBuildOtherGenotype:
    def test_fallback_to_gene_implication(self):
        """Фенотип, для которого нет curated drug_risk, но есть ген-импликация →
        ген-уровневая заметка, не фабрикация и не unknown."""
        c = _seeded()
        rows = cr.build_drug_interactions({"CYP2C9": "Poor Metabolizer"}, c)
        warf = [r for r in rows if r["drug"].startswith("Варфарин")][0]
        assert warf["phenotype_context"] == "Poor Metabolizer"
        assert warf["risk_level"] in ("warning", "critical")
        assert "⚠️" in warf["recommendation"] or "🚨" in warf["recommendation"]

    def test_no_foreign_critical_leaks_to_other(self):
        c = _seeded()
        rows = cr.build_drug_interactions({"CYP2C9": "Poor Metabolizer"}, c)
        # у этого тенанта нет SLCO1B1 → симвастатин не критичен и «не определён»
        simva = [r for r in rows if r["drug"].startswith("Симвастатин")][0]
        assert simva["risk_level"] == "unknown"
        assert simva["phenotype_context"] == "не определён"


# ---------------------------------------------------------------------------
# get_gene_implications
# ---------------------------------------------------------------------------

class TestGeneImplications:
    def test_substring_match(self):
        c = _seeded()
        impl = cr.get_gene_implications("HLA-B", "HLA-B*57:01 Carrier (het)", c)
        assert impl and any("🚨" in i for i in impl)

    def test_normal_no_implications(self):
        c = _seeded()
        assert cr.get_gene_implications("CYP2C9", "Normal Metabolizer", c) == []

    def test_unknown_gene(self):
        c = _seeded()
        assert cr.get_gene_implications("FAKE", "Poor Metabolizer", c) == []


# ---------------------------------------------------------------------------
# GOLDEN: паритет с прежним литералом _IMPLICATIONS (через pharmaco_context)
# ---------------------------------------------------------------------------

_GENES = ["CYP2C9", "CYP2C19", "CYP2D6", "SLCO1B1", "DPYD", "TPMT", "UGT1A1", "HLA-B"]
_PHENOS = [
    "Normal Metabolizer", "Intermediate Metabolizer", "Poor Metabolizer",
    "Ultrarapid Metabolizer", "Rapid Metabolizer", "Normal Function",
    "Decreased Function", "Poor Function", "HLA-B*57:01 Carrier (het)",
    "HLA-B*57:01 Negative", "indeterminate",
]


class TestConsiliumParity:
    def test_byte_parity_all_combos(self):
        """pharmaco_context._get_implications (делегат в БД) == прямой чтение канона,
        побайтово по всем ген×фенотип — консилиум не «поехал» при переносе."""
        c = _seeded()
        mismatches = []
        for g in _GENES:
            for ph in _PHENOS:
                a = pc._get_implications(g, ph, c)
                b = cr.get_gene_implications(g, ph, c)
                if a != b:
                    mismatches.append((g, ph))
        assert not mismatches, f"расхождение делегата и канона: {mismatches}"


# ---------------------------------------------------------------------------
# BL-PUB-16 (в), 27.09: риск — из снимка CPIC по всем классам, не профиль одного человека
# ---------------------------------------------------------------------------

def test_drug_risk_covers_classes_not_one_profile():
    """Утечка, которую закрывает снимок: раньше у каждого препарата был ровно ОДИН класс —
    набор классов по генам и был чьим-то профилем. Теперь класс на препарат — не один."""
    c = _seeded()
    per_drug = dict(c.execute("SELECT drug_id, COUNT(*) FROM cpic_drug_risk GROUP BY drug_id"))
    assert per_drug and min(per_drug.values()) >= 3


def test_risk_rule_pinned_on_known_texts():
    c = _seeded()
    lvl = {(d, p): r for d, p, r in c.execute(
        "SELECT drug_id, phenotype_class, risk_level FROM cpic_drug_risk")}
    assert lvl[("abacavir", "HLA-B*57:01 Carrier (het)")] == "critical"
    assert lvl[("abacavir", "HLA-B*57:01 Negative")] == "ok"
    assert lvl[("atazanavir", "Intermediate Metabolizer")] != "critical"   # «no need to avoid»
    assert lvl[("tamoxifen", "Normal Metabolizer")] == "ok"                 # «avoid … inhibitors»
    assert lvl[("celecoxib", "Poor Metabolizer")] == "warning"              # 25–50% дозы
    assert lvl[("metoprolol", "Ultrarapid Metabolizer")] == "unknown"       # «no recommendation»


def test_install_overlay_replaces_snapshot_text(monkeypatch, tmp_path):
    import json
    ov = tmp_path / "ov.json"
    ov.write_text(json.dumps({"rows": [{"drug_id": "abacavir", "phenotype_class": "HLA-B*57:01 Negative",
                                         "risk_level": "ok", "recommendation": "локальный текст"}]}),
                  encoding="utf-8")
    monkeypatch.setattr(cr, "CURATED_OVERLAY", ov)
    c = _seeded()
    rec = c.execute("SELECT recommendation FROM cpic_drug_risk WHERE drug_id='abacavir' "
                    "AND phenotype_class='HLA-B*57:01 Negative'").fetchone()[0]
    assert rec == "локальный текст"


def test_without_snapshot_phenotype_is_unknown_not_clean(monkeypatch, tmp_path):
    """null_is_unknown_not_clean: нет данных CPIC — «свериться с врачом», а не «ok»."""
    monkeypatch.setattr(cr, "CPIC_SNAPSHOT", tmp_path / "absent.json")
    c = _seeded()
    rows = cr.build_drug_interactions({"CYP2C9": "Normal Metabolizer"}, c)
    cele = [r for r in rows if r["drug"].startswith("Целекоксиб")][0]
    assert cele["risk_level"] == "unknown"
