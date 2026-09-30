"""tests/unit/test_genome_null_not_clean.py — инвариант null_is_unknown_not_clean.

Ниже по потоку NULL effect_allele обязан читаться как «неизвестно», а не «нет
риска». Для онкопациента ложная чистота дороже пустоты. Здесь три позитивных
контроля (RST — датчик обязан краснеть на нарочно сломанном):

  1. Сторож сцепления carrier-статус ⟹ effect_allele≠NULL ловит нарушителя.
  2. Читатель constitution_analysis.analyze_oncology СЮРФЕЙСИТ NULL-вариант как
     «не верифицировано», а не роняет молча в «нет риска» (был баг C1).
  3. Классификатор zygosity никогда не превращает NULL в «wildtype».
"""
from __future__ import annotations

import sqlite3

import pytest

import health_db  # noqa: F401 — грузим первым: health_db↔genome_db цикличны,
#                   genome_db.import health_db работает только когда health_db уже в sys.modules

pytestmark = pytest.mark.unit


def _mem_variants(rows: list[tuple]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE genetic_variants (rsid TEXT, effect_allele_status TEXT, effect_allele TEXT)")
    conn.executemany("INSERT INTO genetic_variants VALUES (?,?,?)", rows)
    conn.commit()
    return conn


def test_coupling_sensor_flags_carrier_status_with_null():
    """Позитивный контроль: carrier-статус + NULL/'' → нарушение; прочее — нет."""
    import genome_db
    conn = _mem_variants([
        ("rs_good", "resolved", "A"),                    # ок
        ("rs_bad_null", "resolved", None),               # НАРУШЕНИЕ: resolved + NULL
        ("rs_bad_empty", "palindromic_het_resolved", ""),  # НАРУШЕНИЕ: carrier + ''
        ("rs_amb", "palindromic", None),                 # ок (не carrier-статус)
    ])
    bad = {r["rsid"] for r in genome_db.carrier_status_null_allele_violations(conn)}
    assert bad == {"rs_bad_null", "rs_bad_empty"}, f"ждали 2 нарушения, получили {bad}"


def test_coupling_sensor_silent_on_clean_db():
    """Негативный контроль: здоровое сцепление → пусто (датчик не ложно-красный)."""
    import genome_db
    conn = _mem_variants([
        ("rs1", "resolved", "G"),
        ("rs2", "palindromic", None),
        ("rs3", "multiallelic_ambiguous", None),
    ])
    assert genome_db.carrier_status_null_allele_violations(conn) == []


def test_zygosity_null_never_wildtype():
    """Классификатор: NULL/'' effect_allele → 'unknown', НИКОГДА не 'wildtype'."""
    from constitution_analysis import zygosity
    assert zygosity("AG", None) == "unknown"
    assert zygosity("AA", "") == "unknown"
    assert zygosity("GG", None) == "unknown"
    # sanity: с известным аллелем классификатор всё ещё различает
    assert zygosity("AA", "A") == "homo_risk"
    assert zygosity("AG", "A") == "hetero"
    assert zygosity("GG", "A") == "wildtype"


def test_analyze_oncology_surfaces_null_as_unverified(db):
    """Поведенческий позитивный контроль фикса C1: онко-вариант Pathogenic со
    strand-неразрешённым (NULL) аллелем ВСПЛЫВАЕТ как unverified, а НЕ роняется
    молча в «нет риска» (неотличимо от verified wildtype — исходный баг)."""
    import constitution_analysis as ca
    db.execute(
        "INSERT INTO genetic_variants "
        "(rsid, gene, genotype, significance, effect_allele, clinical_summary, "
        " domain_tags, effect_allele_status) "
        "VALUES ('rs_onco_null','TESTGENE','AG','Pathogenic',NULL,"
        "'strand не разрешён','oncology','palindromic')")
    conn = db.conn()
    try:
        out = ca.analyze_oncology(conn)
    finally:
        conn.close()
    assert out["genome_unverified_count"] == 1, \
        "NULL онко-вариант обязан всплыть как unverified"
    assert out["genome_oncology_risk_count"] == 0, \
        "NULL не должен попасть в риск-носители (ложная чистота)"
