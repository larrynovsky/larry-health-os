"""Unit tests for wellness_context.py.

Coverage:
  TestSentinels  (2): MISSING / STALE
  TestOutput     (4): header, implications, severity grouping, timestamp
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import wellness_context as wc


def _make_conn() -> sqlite3.Connection:
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
        CREATE TABLE wellness_phenotypes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gene TEXT NOT NULL,
            trait TEXT NOT NULL,
            phenotype TEXT NOT NULL,
            confidence TEXT NOT NULL DEFAULT 'indeterminate',
            supporting_rsids TEXT,
            genotypes_json TEXT,
            notes TEXT,
            genome_import_id INTEGER,
            computed_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX uq_wg ON wellness_phenotypes(gene, trait);
    """)
    conn.execute("INSERT INTO genome_imports (vcf_source) VALUES ('test')")
    # Синтетический профиль (не чей-то геном): по одному фенотипу на ген, чтобы сработала
    # каждая ветка таблицы следствий.
    rows = [
        ("MTHFR", "folate_metabolism",
         "Severely reduced (C677T homozygous)", "high",
         None, None, "Гомозигота C677T.", 1),
        ("COMT", "dopamine_metabolism",
         "High COMT activity (Val/Val)", "high",
         None, None, "Высокая активность.", 1),
        ("FTO", "obesity_risk",
         "Elevated obesity risk (AA homozygous)", "medium",
         None, None, "Повышенный риск.", 1),
        ("HFE", "iron_overload_risk",
         "C282Y homozygous", "high",
         None, None, "Гомозигота C282Y.", 1),
    ]
    conn.executemany(
        "INSERT INTO wellness_phenotypes "
        "(gene, trait, phenotype, confidence, supporting_rsids, genotypes_json, notes, genome_import_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return conn


class TestSentinels:
    def test_missing_no_table(self):
        conn = sqlite3.connect(":memory:")
        assert wc.build_wellness_block(conn) == wc.SENTINEL_MISSING

    def test_stale(self):
        conn = _make_conn()
        conn.execute("INSERT INTO genome_imports (vcf_source) VALUES ('new')")
        conn.commit()
        assert wc.build_wellness_block(conn) == wc.SENTINEL_STALE


class TestOutput:
    def test_header(self):
        conn = _make_conn()
        result = wc.build_wellness_block(conn)
        assert "WELLNESS ГЕНОМИКА" in result

    def test_genes_present(self):
        conn = _make_conn()
        result = wc.build_wellness_block(conn)
        assert "MTHFR" in result
        assert "COMT" in result
        assert "FTO" in result
        assert "HFE" in result

    def test_mthfr_action_note(self):
        conn = _make_conn()
        result = wc.build_wellness_block(conn)
        # Severely reduced MTHFR → ⚠️ action note about метилфолат
        assert "метилфолат" in result.lower() or "фолат" in result.lower()

    def test_timestamp_present(self):
        conn = _make_conn()
        result = wc.build_wellness_block(conn)
        assert "Данные рассчитаны" in result
