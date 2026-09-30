"""Unit tests for prs_context.py (Wave 4).

Coverage:
  - SENTINEL_MISSING when prs_scores table absent
  - SENTINEL_MISSING when prs_scores table present but empty
  - SENTINEL_STALE when new genome_import since last score computation
  - Output: header present
  - Output: PGS ID and trait label present
  - Output: raw_score formatted
  - Output: coverage note included
  - Output: ancestry warning included
  - Output: timestamp line present
"""

import sqlite3
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import prs_context


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _make_conn_no_scores() -> sqlite3.Connection:
    """In-memory DB with genome_imports but no prs_scores."""
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE genome_imports (
            id INTEGER PRIMARY KEY, import_date TEXT, vcf_source TEXT,
            phase_h_prs_at TEXT
        );
    """)
    conn.execute("INSERT INTO genome_imports (vcf_source) VALUES ('test')")
    conn.commit()
    return conn


def _make_conn_with_scores(n_imports: int = 1) -> sqlite3.Connection:
    """In-memory DB with genome_imports + prs_scores."""
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE genome_imports (
            id INTEGER PRIMARY KEY, import_date TEXT, vcf_source TEXT,
            phase_h_prs_at TEXT
        );
        CREATE TABLE prs_scores (
            id               INTEGER PRIMARY KEY,
            genome_import_id INTEGER NOT NULL,
            pgs_id           TEXT NOT NULL,
            trait_label      TEXT,
            raw_score        REAL NOT NULL,
            snps_matched     INTEGER NOT NULL,
            snps_total       INTEGER NOT NULL,
            computed_at      TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(genome_import_id, pgs_id)
        );
    """)
    for i in range(1, n_imports + 1):
        conn.execute("INSERT INTO genome_imports (vcf_source) VALUES ('test')")
    # Link score to first import only (simulates stale if n_imports > 1)
    conn.execute("""
        INSERT INTO prs_scores
            (genome_import_id, pgs_id, trait_label, raw_score,
             snps_matched, snps_total, computed_at)
        VALUES (1, 'PGS000036', 'Диабет 2 типа', 0.12345, 4800, 6800, '2026-06-27T12:00:00')
    """)
    conn.commit()
    return conn


# ── Sentinel tests ───────────────────────────────────────────────────────────

class TestSentinels:
    def test_missing_when_no_table(self):
        """prs_scores table absent → SENTINEL_MISSING."""
        conn = _make_conn_no_scores()
        result = prs_context.build_prs_block(conn=conn)
        assert result == prs_context.SENTINEL_MISSING

    def test_missing_when_table_empty(self):
        """prs_scores table present but empty → SENTINEL_MISSING."""
        conn = sqlite3.connect(":memory:")
        conn.executescript("""
            CREATE TABLE genome_imports (id INTEGER PRIMARY KEY, vcf_source TEXT);
            CREATE TABLE prs_scores (
                id INTEGER PRIMARY KEY, genome_import_id INTEGER,
                pgs_id TEXT, trait_label TEXT, raw_score REAL,
                snps_matched INTEGER, snps_total INTEGER, computed_at TEXT
            );
        """)
        conn.execute("INSERT INTO genome_imports (vcf_source) VALUES ('test')")
        conn.commit()
        result = prs_context.build_prs_block(conn=conn)
        assert result == prs_context.SENTINEL_MISSING

    def test_stale_when_new_import(self):
        """Score linked to import=1, then import=2 added → SENTINEL_STALE."""
        conn = _make_conn_with_scores(n_imports=2)
        result = prs_context.build_prs_block(conn=conn)
        assert result == prs_context.SENTINEL_STALE


# ── Output format tests ──────────────────────────────────────────────────────

class TestOutput:
    def setup_method(self):
        self.conn = _make_conn_with_scores(n_imports=1)
        self.output = prs_context.build_prs_block(conn=self.conn)

    def test_header_present(self):
        assert "ПОЛИГЕННЫЕ ИНДЕКСЫ РИСКА" in self.output

    def test_pgs_id_present(self):
        assert "PGS000036" in self.output

    def test_trait_label_present(self):
        assert "Диабет 2 типа" in self.output

    def test_raw_score_present(self):
        assert "0.12345" in self.output

    def test_ancestry_warning_present(self):
        # Предупреждение о калибровке по сути: модели обучены на европейской выборке,
        # для другого происхождения — отклонение; формулировка о конкретной группе не пинится.
        assert "происхожден" in self.output.lower()
        assert "15–30%" in self.output

    def test_coverage_note_present(self):
        # 4800/6800 = 70.6% → "покрытие 71%"
        assert "покрытие" in self.output

    def test_timestamp_line_present(self):
        assert "2026-06-27" in self.output
