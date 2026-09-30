"""Тесты для /pharmacogenetics роута и справочника CPIC.

Справочник CPIC хранится отдельно от результатов тенанта. Роут строит
контекст по фенотипу смотрящего. Все профили ниже независимо придуманы:
проверяются критический риск, нейтральный справочник и отсутствие переноса
результата между тенантами.
"""
from __future__ import annotations

import sqlite3

import pytest

import dashboard_routers.views as views_module
import cpic_reference_db as cr


# ── helpers ───────────────────────────────────────────────────────────────────

class _FakeRequest:
    def __init__(self, path: str = "/pharmacogenetics"):
        from types import SimpleNamespace
        self.url = SimpleNamespace(path=path)


class _FakeTemplates:
    def __init__(self):
        self.last_call: dict | None = None

    def TemplateResponse(self, request, template, context):
        self.last_call = {"template": template, "context": context}
        return f"rendered:{template}"


_PHENO_DDL = """
    CREATE TABLE pharmaco_phenotypes (
        gene TEXT, star_allele_1 TEXT, star_allele_2 TEXT, phenotype TEXT,
        confidence TEXT DEFAULT 'high', coverage_snp_count INTEGER DEFAULT 0,
        computed_at TEXT DEFAULT '2026-07-17'
    );
"""


def _seeded_conn(phenotypes: list[dict] | None = None) -> sqlite3.Connection:
    """Реальная in-memory БД: pharmaco_phenotypes тенанта + канон CPIC."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_PHENO_DDL)
    for p in (phenotypes or []):
        conn.execute(
            "INSERT INTO pharmaco_phenotypes (gene, star_allele_1, star_allele_2, "
            "phenotype, confidence, coverage_snp_count) VALUES (?, ?, ?, ?, ?, ?)",
            (p["gene"], p.get("s1", "*1"), p.get("s2", "*1"), p["phenotype"],
             p.get("conf", "high"), p.get("cov", 5)),
        )
    cr.seed(conn)
    conn.commit()
    return conn


def _run_route(monkeypatch, conn_or_exc):
    import health_db as _hdb
    if isinstance(conn_or_exc, Exception):
        def _boom():
            raise conn_or_exc
        monkeypatch.setattr(_hdb, "get_conn", _boom)
    else:
        monkeypatch.setattr(_hdb, "get_conn", lambda: conn_or_exc)
    tmpl = _FakeTemplates()
    monkeypatch.setattr(views_module, "templates", tmpl)
    result = views_module.pharmacogenetics(_FakeRequest())
    return result, tmpl


# ── route tests ───────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_route_renders_template(monkeypatch):
    conn = _seeded_conn([{"gene": "CYP2C9", "s1": "*2", "s2": "*1",
                          "phenotype": "Intermediate Metabolizer"}])
    result, _ = _run_route(monkeypatch, conn)
    assert "pharmacogenetics.html" in result


@pytest.mark.unit
def test_route_passes_genes_and_interactions(monkeypatch):
    conn = _seeded_conn([{"gene": "CYP2C19", "s1": "*2", "s2": "*2",
                          "phenotype": "Poor Metabolizer"}])
    _, tmpl = _run_route(monkeypatch, conn)
    ctx = tmpl.last_call["context"]
    assert "genes" in ctx and "drug_interactions" in ctx
    assert len(ctx["genes"]) == 1
    assert len(ctx["drug_interactions"]) == 21  # весь нейтральный справочник


@pytest.mark.unit
def test_route_tenant_phenotype_drives_critical(monkeypatch):
    """Придуманный CYP2C19 poor metabolizer задаёт риск в собственном контексте."""
    conn = _seeded_conn([{"gene": "CYP2C19", "s1": "*2", "s2": "*2",
                          "phenotype": "Poor Metabolizer"}])
    _, tmpl = _run_route(monkeypatch, conn)
    di = tmpl.last_call["context"]["drug_interactions"]
    clopi = [d for d in di if d["drug"].startswith("Клопидогрел")][0]
    assert clopi["risk_level"] == "critical"
    assert clopi["phenotype_context"] == "Poor Metabolizer"


@pytest.mark.unit
def test_route_empty_genome_neutral_catalog(monkeypatch):
    """Пустой геном → genes=[], справочник виден, но всё «не определён», ноль criticals."""
    conn = _seeded_conn([])
    _, tmpl = _run_route(monkeypatch, conn)
    ctx = tmpl.last_call["context"]
    assert ctx["genes"] == []
    di = ctx["drug_interactions"]
    assert len(di) == 21
    assert all(d["phenotype_context"] == "не определён" for d in di)
    assert not any(d["risk_level"] == "critical" for d in di)


@pytest.mark.unit
def test_route_no_cross_tenant_leak(monkeypatch):
    """Независимый синтетический тенант без CYP2C19 не получает его риск."""
    conn = _seeded_conn([{"gene": "CYP2C9", "s1": "*1", "s2": "*1",
                          "phenotype": "Normal Metabolizer"}])
    _, tmpl = _run_route(monkeypatch, conn)
    di = tmpl.last_call["context"]["drug_interactions"]
    assert not any("*2/*2" in d["phenotype_context"] for d in di)
    clopi = [d for d in di if d["drug"].startswith("Клопидогрел")][0]
    assert clopi["risk_level"] == "unknown"
    assert clopi["phenotype_context"] == "не определён"


@pytest.mark.unit
def test_route_db_error_returns_empty(monkeypatch):
    _, tmpl = _run_route(monkeypatch, RuntimeError("db error"))
    ctx = tmpl.last_call["context"]
    assert ctx["genes"] == []
    assert ctx["drug_interactions"] == []


# ── CPIC canon integrity (через build_drug_interactions/канон) ─────────────────

@pytest.mark.unit
def test_abacavir_critical_under_carrier():
    """Абакавир = critical при HLA-B*57:01 Carrier (het) у тенанта."""
    conn = _seeded_conn()
    rows = cr.build_drug_interactions({"HLA-B": "HLA-B*57:01 Carrier (het)"}, conn)
    aba = [r for r in rows if "abacavir" in r["drug_search"].lower()][0]
    assert aba["risk_level"] == "critical"


@pytest.mark.unit
def test_simvastatin_critical_under_decreased():
    conn = _seeded_conn()
    rows = cr.build_drug_interactions({"SLCO1B1": "Decreased Function"}, conn)
    clopi = [r for r in rows if "simvastatin" in r["drug_search"].lower()][0]
    assert clopi["risk_level"] == "critical"


@pytest.mark.unit
def test_all_rows_have_required_fields():
    conn = _seeded_conn()
    rows = cr.build_drug_interactions({"SLCO1B1": "Decreased Function"}, conn)
    required = {"drug", "drug_search", "gene", "phenotype_context", "risk_level", "recommendation"}
    for r in rows:
        assert required <= set(r.keys()), f"{r.get('drug')}: не хватает {required - set(r.keys())}"


@pytest.mark.unit
def test_risk_levels_valid():
    conn = _seeded_conn()
    allowed = {"critical", "warning", "ok", "unknown"}
    for pheno in ({}, {"SLCO1B1": "Decreased Function"}, {"CYP2C9": "Poor Metabolizer"}):
        for r in cr.build_drug_interactions(pheno, conn):
            assert r["risk_level"] in allowed, f"{r['drug']}: {r['risk_level']}"
