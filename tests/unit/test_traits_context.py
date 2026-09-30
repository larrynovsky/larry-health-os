"""Unit tests for traits_context.py.

Coverage:
  TestSentinels   (2): MISSING when no table/no rows, STALE when import_id mismatch
  TestOutput      (4): header present, categories, phenotypes in text, timestamp
  TestStaleness   (1): stale detection logic
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import traits_context as tc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn_with_traits() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE genome_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_date TEXT,
            vcf_source TEXT,
            phase_e_pharmaco_at TEXT,
            phase_f_monogenic_at TEXT,
            phase_g_prs_at TEXT,
            completed_at TEXT,
            notes TEXT
        );
        CREATE TABLE trait_phenotypes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trait TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '',
            phenotype TEXT NOT NULL,
            confidence TEXT NOT NULL DEFAULT 'indeterminate',
            supporting_rsids TEXT,
            genotypes_json TEXT,
            notes TEXT,
            genome_import_id INTEGER,
            computed_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX uq_trait ON trait_phenotypes(trait);
    """)
    conn.execute("INSERT INTO genome_imports (vcf_source) VALUES ('test')")
    # Профиль целиком вымышлен: по одной черте на категорию, значения не чьи-то.
    rows = [
        ("lactase_persistence", "nutrition", "Persistent (lactose tolerant)",
         "high", None, None, "Синтетический генотип.", 1),
        ("eye_color_tendency", "appearance", "Blue eyes (probabilistic)",
         "medium", None, None, "Синтетический генотип.", 1),
        ("apoe_genotype", "disease_risk", "ε2/ε3",
         "high", None, None, "Синтетический генотип.", 1),
    ]
    conn.executemany(
        "INSERT INTO trait_phenotypes "
        "(trait, category, phenotype, confidence, supporting_rsids, genotypes_json, notes, genome_import_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return conn


# ===========================================================================
# Sentinels
# ===========================================================================

class TestSentinels:
    def test_missing_no_table(self):
        conn = sqlite3.connect(":memory:")
        result = tc.build_traits_block(conn)
        assert result == tc.SENTINEL_MISSING

    def test_missing_empty_table(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE trait_phenotypes (
                id INTEGER PRIMARY KEY, trait TEXT, category TEXT,
                phenotype TEXT, confidence TEXT, supporting_rsids TEXT,
                genotypes_json TEXT, notes TEXT, genome_import_id INTEGER,
                computed_at TEXT
            )
        """)
        conn.commit()
        result = tc.build_traits_block(conn)
        assert result == tc.SENTINEL_MISSING

    def test_stale_when_import_mismatch(self):
        conn = _make_conn_with_traits()
        # Add a new genome_imports row → MAX(id)=2 but trait_phenotypes still has id=1
        conn.execute("INSERT INTO genome_imports (vcf_source) VALUES ('new_vcf')")
        conn.commit()
        result = tc.build_traits_block(conn)
        assert result == tc.SENTINEL_STALE


# ===========================================================================
# Output format
# ===========================================================================

class TestOutput:
    def test_header_present(self):
        conn = _make_conn_with_traits()
        result = tc.build_traits_block(conn)
        assert "ДЕТЕРМИНИРОВАННЫЕ ЧЕРТЫ" in result

    def test_categories_present(self):
        conn = _make_conn_with_traits()
        result = tc.build_traits_block(conn)
        assert "ПИТАНИЕ" in result
        assert "ВНЕШНОСТЬ" in result
        assert "ГЕНЕТИЧЕСКИЙ РИСК" in result

    def test_phenotypes_in_output(self):
        conn = _make_conn_with_traits()
        result = tc.build_traits_block(conn)
        assert "Persistent (lactose tolerant)" in result
        assert "ε2/ε3" in result
        assert "Blue eyes" in result

    def test_timestamp_present(self):
        conn = _make_conn_with_traits()
        result = tc.build_traits_block(conn)
        assert "Данные рассчитаны" in result
