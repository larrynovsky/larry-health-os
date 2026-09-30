"""tests/unit/test_pharmaco_context.py

Покрывает pharmaco_context.py:
  - SENTINEL_MISSING (пустая таблица)
  - SENTINEL_STALE (новый импорт после pharmaco)
  - build_pharmacogenomic_block() основной вывод
  - relevant_drugs фильтр
  - _normalize_drug (RU → EN)
  - _get_implications
  - _genes_for_drugs
  - _check_staleness

Примечание по иконкам в _IMPLICATIONS:
  🚨 = DPYD Poor, HLA-B *57:01 → попадают в critical_lines
  ⚠️ = CYP2C9 Poor, CYP2C19 Poor, CYP2D6 Poor/UR, SLCO1B1 Poor Function,
        DPYD Intermediate, TPMT Poor → попадают в warning_lines
  ⚡ = CYP2C9 Intermediate, SLCO1B1 Decreased Function, TPMT Intermediate … → info_lines
"""

from __future__ import annotations

import sqlite3
import pytest

import pharmaco_context as pc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_conn(
    phenotypes: list[dict] | None = None,
    n_imports: int = 0,
    pharmaco_import_id: int | None = None,
) -> sqlite3.Connection:
    """In-memory DB с нужными таблицами."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE genome_imports (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            import_date          TEXT DEFAULT (date('now')),
            vcf_source           TEXT,
            phase_e_pharmaco_at  TEXT
        );
        CREATE TABLE pharmaco_phenotypes (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            gene               TEXT NOT NULL,
            star_allele_1      TEXT,
            star_allele_2      TEXT,
            phenotype          TEXT NOT NULL,
            confidence         TEXT NOT NULL DEFAULT 'indeterminate',
            coverage_snp_count INTEGER DEFAULT 0,
            genome_import_id   INTEGER,
            computed_at        TEXT DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX uq_pharmaco_gene ON pharmaco_phenotypes(gene);
    """)
    for _ in range(n_imports):
        conn.execute("INSERT INTO genome_imports (vcf_source) VALUES ('test.vcf')")
    if phenotypes:
        imp_id = pharmaco_import_id if pharmaco_import_id is not None else (
            conn.execute("SELECT MAX(id) FROM genome_imports").fetchone()[0] or 1
        )
        for p in phenotypes:
            conn.execute(
                """INSERT INTO pharmaco_phenotypes
                   (gene, star_allele_1, star_allele_2, phenotype, confidence,
                    coverage_snp_count, genome_import_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (p["gene"], p.get("s1", "*1"), p.get("s2", "*1"),
                 p["phenotype"], p.get("conf", "high"),
                 p.get("cov", 1), imp_id),
            )
    import cpic_reference_db as _cpic
    _cpic.seed(conn)  # A2-full: канон CPIC читается из БД
    conn.commit()
    return conn


# Синтетический набор фенотипов (не профиль какого-либо тенанта; до 2026-09-25 здесь стоял
# живой профиль — вычищено перед публикацией, нить genotype-scrub).
_SYNTH_PHENOTYPES = [
    {"gene": "CYP2C9",  "s1": "*1",    "s2": "*3",    "phenotype": "Intermediate Metabolizer"},
    {"gene": "CYP2C19", "s1": "*2",    "s2": "*17",   "phenotype": "Intermediate Metabolizer"},
    {"gene": "CYP2D6",  "s1": "unknown", "s2": "unknown", "phenotype": "indeterminate", "conf": "indeterminate"},
    {"gene": "SLCO1B1", "s1": "*5",    "s2": "*5",    "phenotype": "Poor Function"},
    {"gene": "DPYD",    "s1": "*1",    "s2": "*1",    "phenotype": "Normal Metabolizer"},
    {"gene": "TPMT",    "s1": "*1",    "s2": "*1",    "phenotype": "Normal Metabolizer"},
    {"gene": "UGT1A1",  "s1": "*1",    "s2": "*28",   "phenotype": "Intermediate Metabolizer"},
    {"gene": "HLA-B",   "s1": "*57:01", "s2": "*57:01", "phenotype": "HLA-B*57:01 Carrier (hom)"},
]


# ---------------------------------------------------------------------------
# Sentinel tests
# ---------------------------------------------------------------------------

class TestSentinels:
    def test_missing_returns_sentinel(self):
        """Пустая pharmaco_phenotypes → SENTINEL_MISSING."""
        conn = _make_conn(n_imports=0)
        result = pc.build_pharmacogenomic_block(conn=conn)
        assert result == pc.SENTINEL_MISSING, result

    def test_stale_returns_sentinel(self):
        """genome_imports.max(id) != pharmaco.genome_import_id → SENTINEL_STALE."""
        # import_id=1 → pharmaco computed, then new import (id=2) arrives
        conn = _make_conn(
            phenotypes=[{"gene": "CYP2C9", "s1": "*1", "s2": "*1",
                         "phenotype": "Normal Metabolizer"}],
            n_imports=2,
            pharmaco_import_id=1,   # pharmaco от старого импорта
        )
        result = pc.build_pharmacogenomic_block(conn=conn)
        assert result == pc.SENTINEL_STALE, result

    def test_fresh_data_not_stale(self):
        """pharmaco_import_id == max(genome_imports.id) → нормальный вывод."""
        conn = _make_conn(
            phenotypes=[{"gene": "CYP2C9", "s1": "*1", "s2": "*1",
                         "phenotype": "Normal Metabolizer"}],
            n_imports=1,
            pharmaco_import_id=1,
        )
        result = pc.build_pharmacogenomic_block(conn=conn)
        assert result != pc.SENTINEL_STALE
        assert result != pc.SENTINEL_MISSING

    def test_no_imports_not_stale(self):
        """pharmaco есть, но genome_imports пуста — не считается stale."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE genome_imports (id INTEGER PRIMARY KEY);
            CREATE TABLE pharmaco_phenotypes (
                gene TEXT, star_allele_1 TEXT, star_allele_2 TEXT,
                phenotype TEXT, confidence TEXT DEFAULT 'high',
                coverage_snp_count INTEGER DEFAULT 0, genome_import_id INTEGER,
                computed_at TEXT DEFAULT (datetime('now'))
            );
            CREATE UNIQUE INDEX uq_pg ON pharmaco_phenotypes(gene);
            INSERT INTO pharmaco_phenotypes (gene, phenotype, genome_import_id)
            VALUES ('CYP2C9', 'Normal Metabolizer', NULL);
        """)
        assert not pc._check_staleness(conn)


# ---------------------------------------------------------------------------
# Output format tests
# ---------------------------------------------------------------------------

class TestOutputFormat:
    def test_header_present(self):
        conn = _make_conn(phenotypes=_SYNTH_PHENOTYPES, n_imports=1)
        result = pc.build_pharmacogenomic_block(conn=conn)
        assert "=== ФАРМАКОГЕНОМИКА (CPIC) ===" in result

    def test_hla_b_critical_marker(self):
        """HLA-B*57:01 carrier → 🚨 в critical_lines, текст про абакавир."""
        conn = _make_conn(
            phenotypes=[{"gene": "HLA-B", "s1": "*57:01", "s2": "*other",
                         "phenotype": "HLA-B*57:01 Carrier (het)"}],
            n_imports=1,
        )
        result = pc.build_pharmacogenomic_block(conn=conn)
        assert "🚨" in result
        # Текст имплекации содержит "АБАКАВИР"
        assert "абакавир" in result.lower()

    def test_slco1b1_decreased_in_output(self):
        """SLCO1B1 Decreased Function → ⚡ в info_lines, текст про симвастатин."""
        conn = _make_conn(
            phenotypes=[{"gene": "SLCO1B1", "s1": "*1a", "s2": "*5",
                         "phenotype": "Decreased Function"}],
            n_imports=1,
        )
        result = pc.build_pharmacogenomic_block(conn=conn)
        # Decreased Function имплекация — ⚡ (не ⚠️), попадает в ℹ️ ПРОЧИЕ НАХОДКИ
        assert "SLCO1B1" in result
        assert "симвастатин" in result.lower()
        assert "ℹ️" in result

    def test_cyp2c9_poor_metabolizer_in_warnings(self):
        """CYP2C9 Poor Metabolizer → ⚠️ в warning_lines."""
        conn = _make_conn(
            phenotypes=[{"gene": "CYP2C9", "s1": "*3", "s2": "*3",
                         "phenotype": "Poor Metabolizer"}],
            n_imports=1,
        )
        result = pc.build_pharmacogenomic_block(conn=conn)
        assert "⚠️" in result
        assert "CYP2C9" in result

    def test_indeterminate_in_info_section(self):
        """Indeterminate genes попадают в ℹ️ секцию с меткой 'неопределённо'."""
        conn = _make_conn(
            phenotypes=[{"gene": "CYP2D6", "s1": "unknown", "s2": "unknown",
                         "phenotype": "indeterminate", "conf": "indeterminate"}],
            n_imports=1,
        )
        result = pc.build_pharmacogenomic_block(conn=conn)
        assert "CYP2D6" in result
        assert "неопределённо" in result

    def test_timestamp_present(self):
        """computed_at должен быть в выводе."""
        conn = _make_conn(
            phenotypes=[{"gene": "CYP2C9", "s1": "*1", "s2": "*1",
                         "phenotype": "Normal Metabolizer"}],
            n_imports=1,
        )
        result = pc.build_pharmacogenomic_block(conn=conn)
        assert "Данные рассчитаны" in result

    def test_full_synthetic_phenotypes_no_crash(self):
        """Полный синтетический набор фенотипов → функция завершается без исключений."""
        conn = _make_conn(phenotypes=_SYNTH_PHENOTYPES, n_imports=1)
        result = pc.build_pharmacogenomic_block(conn=conn)
        assert len(result) > 100
        assert "CYP2C9" in result
        assert "SLCO1B1" in result
        assert "HLA-B" in result
        # HLA-B*57:01 → critical
        assert "🚨" in result


# ---------------------------------------------------------------------------
# relevant_drugs filter
# ---------------------------------------------------------------------------

class TestRelevantDrugsFilter:
    def test_filter_by_en_drug(self):
        """Фильтр по EN-имени оставляет только нужные гены."""
        conn = _make_conn(phenotypes=_SYNTH_PHENOTYPES, n_imports=1)
        result = pc.build_pharmacogenomic_block(conn=conn, relevant_drugs=["warfarin"])
        assert "CYP2C9" in result
        # Warfarin не затрагивает SLCO1B1
        assert "SLCO1B1" not in result

    def test_filter_by_ru_drug(self):
        """Фильтр работает через RU-алиас: варфарин → warfarin → CYP2C9."""
        conn = _make_conn(phenotypes=_SYNTH_PHENOTYPES, n_imports=1)
        result = pc.build_pharmacogenomic_block(conn=conn, relevant_drugs=["варфарин"])
        assert "CYP2C9" in result

    def test_filter_unknown_drug_returns_no_data(self):
        """Препарат не в маппинге → сообщение с [PHARMACO...]."""
        conn = _make_conn(phenotypes=_SYNTH_PHENOTYPES, n_imports=1)
        result = pc.build_pharmacogenomic_block(conn=conn, relevant_drugs=["аспирин"])
        assert "PHARMACO" in result

    def test_filter_statin_hits_slco1b1(self):
        """симвастатин → только SLCO1B1, без CYP2C9."""
        conn = _make_conn(phenotypes=_SYNTH_PHENOTYPES, n_imports=1)
        result = pc.build_pharmacogenomic_block(conn=conn, relevant_drugs=["симвастатин"])
        assert "SLCO1B1" in result
        assert "CYP2C9" not in result

    def test_filter_abacavir_hits_hla_b(self):
        """абакавир → только HLA-B."""
        conn = _make_conn(phenotypes=_SYNTH_PHENOTYPES, n_imports=1)
        result = pc.build_pharmacogenomic_block(conn=conn, relevant_drugs=["абакавир"])
        assert "HLA-B" in result
        assert "CYP2C9" not in result


# ---------------------------------------------------------------------------
# _normalize_drug
# ---------------------------------------------------------------------------

class TestNormalizeDrug:
    def test_ru_to_en(self):
        assert pc._normalize_drug("варфарин") == "warfarin"
        assert pc._normalize_drug("симвастатин") == "simvastatin"
        assert pc._normalize_drug("клопидогрел") == "clopidogrel"
        assert pc._normalize_drug("абакавир") == "abacavir"
        assert pc._normalize_drug("5-фторурацил") == "fluorouracil"

    def test_case_insensitive(self):
        assert pc._normalize_drug("Варфарин") == "warfarin"
        assert pc._normalize_drug("ВАРФАРИН") == "warfarin"

    def test_unknown_returns_original(self):
        assert pc._normalize_drug("аспирин") == "аспирин"

    def test_strips_whitespace(self):
        assert pc._normalize_drug("  варфарин  ") == "warfarin"


# ---------------------------------------------------------------------------
# _genes_for_drugs
# ---------------------------------------------------------------------------

class TestGenesForDrugs:
    def test_single_drug(self):
        genes = pc._genes_for_drugs(["warfarin"])
        assert "CYP2C9" in genes

    def test_multi_drug(self):
        genes = pc._genes_for_drugs(["warfarin", "clopidogrel"])
        assert "CYP2C9" in genes
        assert "CYP2C19" in genes

    def test_ru_drug(self):
        genes = pc._genes_for_drugs(["варфарин"])
        assert "CYP2C9" in genes

    def test_unknown_drug(self):
        genes = pc._genes_for_drugs(["аспирин"])
        assert len(genes) == 0


# ---------------------------------------------------------------------------
# _get_implications
# ---------------------------------------------------------------------------


# A2-full: _get_implications теперь читает cpic_gene_implication из БД → нужен conn.
_IMPL_CONN = None
def _impl_conn():
    global _IMPL_CONN
    if _IMPL_CONN is None:
        import cpic_reference_db as _cpic
        _IMPL_CONN = sqlite3.connect(":memory:")
        _cpic.seed(_IMPL_CONN)
    return _IMPL_CONN


class TestGetImplications:
    def test_hla_b_critical(self):
        """HLA-B*57:01 carrier → 🚨 имплекация."""
        impl = pc._get_implications("HLA-B", "HLA-B*57:01 Carrier (het)", _impl_conn())
        assert len(impl) > 0
        assert any("🚨" in i for i in impl)

    def test_slco1b1_decreased(self):
        """SLCO1B1 Decreased Function → ⚡ имплекация (не ⚠️)."""
        impl = pc._get_implications("SLCO1B1", "Decreased Function", _impl_conn())
        assert len(impl) > 0
        # Implication text uses ⚡, not ⚠️ (that's Poor Function)
        assert any("⚡" in i for i in impl)

    def test_slco1b1_poor_function_warning(self):
        """SLCO1B1 Poor Function → ⚠️ имплекация."""
        impl = pc._get_implications("SLCO1B1", "Poor Function", _impl_conn())
        assert len(impl) > 0
        assert any("⚠️" in i for i in impl)

    def test_normal_no_critical_implications(self):
        """CYP2C9 Normal Metabolizer → нет имплекаций."""
        impl = pc._get_implications("CYP2C9", "Normal Metabolizer", _impl_conn())
        assert impl == []

    def test_unknown_gene_no_implications(self):
        impl = pc._get_implications("FAKE_GENE", "Poor Metabolizer", _impl_conn())
        assert impl == []

    def test_dpyd_poor_metabolizer_critical(self):
        """DPYD Poor Metabolizer → 🚨 имплекация."""
        impl = pc._get_implications("DPYD", "Poor Metabolizer", _impl_conn())
        assert len(impl) > 0
        assert any("🚨" in i for i in impl)

    def test_cyp2c9_intermediate(self):
        """CYP2C9 Intermediate Metabolizer → ⚡ имплекация."""
        impl = pc._get_implications("CYP2C9", "Intermediate Metabolizer", _impl_conn())
        assert len(impl) > 0
        assert any("⚡" in i for i in impl)
