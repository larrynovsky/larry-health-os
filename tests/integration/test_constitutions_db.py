"""Option 3 (2026-06-18): constitutions DB-as-source.

Нарратив конституций здоровья хранится в БД (таблица constitutions) = источник
правды; дашборд читает из БД с fallback на файл; generate_constitutions._save()
пишет в БД. Цель — снять файлы constitutions/*.md из git (split-brain prevention).

Уровень: integration. Таблицы constitutions нет в тест-схеме health_schema.sql,
поэтому создаём её явно через _migrate_constitutions().

2026-06-26: добавлены тесты carrier-status фильтрации (_get_snp_data, Bug B)
            и теcты _read_previous() из БД (Bug A).
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest

import health_db

pytestmark = pytest.mark.integration


def test_upsert_get_list(db):
    health_db._migrate_constitutions()
    health_db.upsert_constitution("sleep", "# Сон\nтело", title="Сон", source_version="t")
    rec = health_db.get_constitution("sleep")
    assert rec is not None
    assert rec["body_md"] == "# Сон\nтело"
    assert rec["title"] == "Сон"

    # upsert обновляет существующую строку, не плодит дубли
    health_db.upsert_constitution("sleep", "# Сон v2", title="Сон")
    assert health_db.get_constitution("sleep")["body_md"] == "# Сон v2"

    health_db.upsert_constitution("stress", "# Стресс", title="Стресс")
    lst = health_db.list_constitutions()
    domains = {r["domain"] for r in lst}
    assert {"sleep", "stress"} <= domains
    sleep_row = next(r for r in lst if r["domain"] == "sleep")
    assert sleep_row["size_bytes"] == len("# Сон v2")


def test_get_constitution_missing_returns_none(db):
    health_db._migrate_constitutions()
    assert health_db.get_constitution("nonexistent_domain") is None


def test_save_writes_to_db(db, monkeypatch, tmp_path):
    """generate_constitutions._save() пишет нарратив в БД (Option 3)."""
    import generate_constitutions as gc

    health_db._migrate_constitutions()
    const_dir = tmp_path / "const"
    icloud_dir = tmp_path / "icloud"
    const_dir.mkdir()
    icloud_dir.mkdir()
    monkeypatch.setattr(gc, "CONSTITUTIONS_DIR", const_dir)
    import infra_config   # зеркало — настройка установки (constitutions_mirror), не константа модуля
    monkeypatch.setattr(infra_config, "CONSTITUTIONS_MIRROR", icloud_dir)

    gc._save("sleep", "# Сгенерировано\nтекст конституции")

    rec = health_db.get_constitution("sleep")
    assert rec is not None
    assert "Сгенерировано" in rec["body_md"]
    # репо-файл НЕ пишется (constitutions/ вне git); iCloud-export пишется
    assert not (const_dir / "sleep.md").exists()
    assert (icloud_dir / "sleep.md").exists()


def test_dashboard_view_reads_from_db(dashboard_client):
    """Дашборд /constitutions/{slug} рендерит body_md из БД (не из файла)."""
    client, _db = dashboard_client
    health_db._migrate_constitutions()
    health_db.upsert_constitution(
        "sleep", "# Заголовок\nуникальный-маркер-DB-7788", title="Сон"
    )
    r = client.get("/constitutions/sleep")
    assert r.status_code == 200
    assert "уникальный-маркер-DB-7788" in r.text


def test_dashboard_index_lists_from_db(dashboard_client):
    """Дашборд /constitutions перечисляет конституции из БД."""
    client, _db = dashboard_client
    health_db._migrate_constitutions()
    health_db.upsert_constitution("movement", "# Движение\nтекст", title="Движение")
    r = client.get("/constitutions")
    assert r.status_code == 200
    assert "Движение" in r.text


@pytest.mark.host_only
def test_constitutions_not_tracked_in_git():
    """R-правило (Option 3): файлы constitutions/*.md не должны быть в git
    (источник = БД; файлы вне git → нет git-расхождения генератов)."""
    import subprocess
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "constitutions/"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert out == "", f"constitutions/ всё ещё tracked в git: {out!r}"


# ── Bug A: _read_previous() читает из БД, не из файла (2026-06-26) ────────────

def test_read_previous_reads_from_db(db, monkeypatch):
    """_read_previous() возвращает body_md из БД.

    До фикса: читала из локального файла constitutions/<domain>.md, который
    после DB-as-source (2026-06-18) больше не обновлялся → diff-секция исчезла.
    """
    import generate_constitutions as gc

    health_db._migrate_constitutions()
    health_db.upsert_constitution("sleep", "# Старая конституция\nтело-v1", title="Сон")

    result = gc._read_previous("sleep")
    assert result == "# Старая конституция\nтело-v1"


def test_read_previous_returns_empty_if_absent(db):
    """_read_previous() возвращает '' если конституции в БД нет."""
    import generate_constitutions as gc

    health_db._migrate_constitutions()
    # ничего не вставляем
    assert gc._read_previous("sleep") == ""


# ── Bug B: carrier-status фильтрация в _get_snp_data() (2026-06-26) ──────────

def _seed_variant(db, rsid: str, gene: str, genotype: str,
                  significance: str, effect_allele: str | None,
                  effect_allele_status: str) -> None:
    """Вставить вариант в genetic_variants с полями carrier-status."""
    db.add_genetic_variant(
        rsid,
        gene=gene,
        genotype=genotype,
        significance=significance,
        effect_allele=effect_allele,
        effect_allele_status=effect_allele_status,
        clinical_summary=f"Test variant {rsid}",
    )


def test_resolved_carrier_included_in_bad(db):
    """resolved + effect_allele в genotype → вариант попадает в bad-bucket."""
    import generate_constitutions as gc

    _seed_variant(db, "rs_TEST_CARRIER", "CRY1", "AG",
                  significance="Pathogenic",
                  effect_allele="G",
                  effect_allele_status="resolved")

    domain_cfg = {"genes": ["CRY1"]}
    result = gc._get_snp_data(domain_cfg)

    rsids_bad = [v["rsid"] for v in result["bad"]]
    assert "rs_TEST_CARRIER" in rsids_bad, "Носитель патогенного аллеля должен быть в bad"


def test_resolved_non_carrier_excluded(db):
    """resolved + effect_allele НЕ в genotype → вариант пропускается целиком.

    Именно этот случай был источником ложных патологий в конституциях (гены,
    чей genotype не содержал effect_allele, попадали в bad-bucket).
    """
    import generate_constitutions as gc

    _seed_variant(db, "rs_TEST_NONCARRIER", "CRY2", "TT",
                  significance="Pathogenic",
                  effect_allele="C",   # C не входит в TT → не носитель
                  effect_allele_status="resolved")

    domain_cfg = {"genes": ["CRY2"]}
    result = gc._get_snp_data(domain_cfg)

    all_rsids = (
        [v["rsid"] for v in result["bad"]] +
        [v["rsid"] for v in result["good"]] +
        [v["rsid"] for v in result["unknown"]]
    )
    assert "rs_TEST_NONCARRIER" not in all_rsids, \
        "Не-носитель не должен появляться ни в одном bucket"


def test_palindromic_het_resolved_carrier_included(db):
    """palindromic_het_resolved + effect_allele в genotype → попадает в bad."""
    import generate_constitutions as gc

    _seed_variant(db, "rs_TEST_PAL_HET", "FTO", "AT",
                  significance="risk factor",
                  effect_allele="A",
                  effect_allele_status="palindromic_het_resolved")

    domain_cfg = {"genes": ["FTO"]}
    result = gc._get_snp_data(domain_cfg)

    rsids_bad = [v["rsid"] for v in result["bad"]]
    assert "rs_TEST_PAL_HET" in rsids_bad


def test_palindromic_unresolved_goes_to_unknown(db):
    """palindromic (нерешённый) → принудительно в unknown, не в bad.

    Носительство неопределимо из-за strand-амбигуации — лечим как 'неизвестно',
    не как 'патологично'.
    """
    import generate_constitutions as gc

    _seed_variant(db, "rs_TEST_PAL", "CRY1", "GG",
                  significance="Pathogenic",
                  effect_allele=None,
                  effect_allele_status="palindromic")

    domain_cfg = {"genes": ["CRY1"]}
    result = gc._get_snp_data(domain_cfg)

    assert "rs_TEST_PAL" not in [v["rsid"] for v in result["bad"]], \
        "Нерешённый palindromic не должен быть в bad"
    assert "rs_TEST_PAL" in [v["rsid"] for v in result["unknown"]], \
        "Нерешённый palindromic должен быть в unknown"


def test_multiallelic_ambiguous_goes_to_unknown(db):
    """multiallelic_ambiguous → в unknown (carrier неопределим)."""
    import generate_constitutions as gc

    _seed_variant(db, "rs_TEST_MULTI", "BRCA1", "AC",
                  significance="Pathogenic",
                  effect_allele=None,
                  effect_allele_status="multiallelic_ambiguous")

    domain_cfg = {"genes": ["BRCA1"]}
    result = gc._get_snp_data(domain_cfg)

    assert "rs_TEST_MULTI" not in [v["rsid"] for v in result["bad"]]
    assert "rs_TEST_MULTI" in [v["rsid"] for v in result["unknown"]]


def test_no_call_excluded(db):
    """no_call → пропускается (нет генотипа, нельзя определить носительство)."""
    import generate_constitutions as gc

    _seed_variant(db, "rs_TEST_NOCALL", "CLOCK", "--",
                  significance="Pathogenic",
                  effect_allele=None,
                  effect_allele_status="no_call")

    domain_cfg = {"genes": ["CLOCK"]}
    result = gc._get_snp_data(domain_cfg)

    all_rsids = (
        [v["rsid"] for v in result["bad"]] +
        [v["rsid"] for v in result["good"]] +
        [v["rsid"] for v in result["unknown"]]
    )
    assert "rs_TEST_NOCALL" not in all_rsids


def test_protective_carrier_goes_to_good(db):
    """resolved + protective significance + effect_allele в genotype → good-bucket."""
    import generate_constitutions as gc

    _seed_variant(db, "rs_TEST_PROT", "APOE", "TT",
                  significance="protective",
                  effect_allele="T",
                  effect_allele_status="resolved")

    domain_cfg = {"genes": ["APOE"]}
    result = gc._get_snp_data(domain_cfg)

    assert "rs_TEST_PROT" in [v["rsid"] for v in result["good"]]
