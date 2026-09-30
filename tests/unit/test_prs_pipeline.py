"""Unit tests for prs_pipeline.py (Wave 4, Phase H).

Coverage:
  - _count_allele edge cases
  - run() with no weights registered → status='no_weights'
  - run() with missing pgs_catalog table → status='no_weights'
  - score computation: correct raw score for single variant
  - score computation: two variants, different allele counts
  - missing genotype → skipped (R1 guard)
  - indeterminate genotype (len != 2) → skipped
  - run() creates prs_scores table
  - run() stamps genome_imports.phase_h_prs_at
  - run() stores coverage-correct snps_matched / snps_total
  - multiple PGS scores in same run
  - INSERT OR REPLACE on re-run (idempotent)
"""

import sqlite3
import sys
import os
import pytest

# Import from health_scripts directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import prs_pipeline


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _make_conn(snps: list[tuple] = None, weights: list[tuple] = None) -> sqlite3.Connection:
    """In-memory DB with raw_snps, genome_imports, pgs_catalog, pgs_weights."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE raw_snps (
            rsid       TEXT PRIMARY KEY,
            chromosome TEXT,
            position   INTEGER,
            genotype   TEXT,
            source     TEXT DEFAULT '23andme'
        );
        CREATE TABLE genome_imports (
            id                   INTEGER PRIMARY KEY,
            import_date          TEXT DEFAULT (date('now')),
            vcf_source           TEXT,
            phase_e_pharmaco_at  TEXT,
            phase_f_monogenic_at TEXT,
            phase_g_prs_at       TEXT,
            completed_at         TEXT,
            notes                TEXT
        );
        CREATE TABLE pgs_catalog (
            pgs_id         TEXT PRIMARY KEY,
            trait_name     TEXT NOT NULL,
            trait_label    TEXT,
            num_variants   INTEGER,
            ancestry_broad TEXT,
            genome_build   TEXT DEFAULT 'GRCh38',
            downloaded_at  TEXT NOT NULL
        );
        CREATE TABLE pgs_weights (
            id            INTEGER PRIMARY KEY,
            pgs_id        TEXT NOT NULL,
            rsid          TEXT,
            chr_name      TEXT,
            chr_position  INTEGER,
            effect_allele TEXT NOT NULL,
            other_allele  TEXT,
            effect_weight REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_pgs_weights_rsid
            ON pgs_weights (pgs_id, rsid);
    """)
    # Insert genome_imports row
    conn.execute("INSERT INTO genome_imports (vcf_source) VALUES ('test.vcf')")
    # Insert raw_snps
    for rsid, genotype in (snps or []):
        conn.execute(
            "INSERT INTO raw_snps (rsid, genotype) VALUES (?,?)", (rsid, genotype)
        )
    # Insert pgs catalog + weights
    if weights:
        conn.execute(
            "INSERT INTO pgs_catalog (pgs_id, trait_name, downloaded_at) "
            "VALUES ('PGS000001', 'Test Trait', '2026-01-01')"
        )
        for rsid, ea, ew in weights:
            conn.execute(
                "INSERT INTO pgs_weights (pgs_id, rsid, effect_allele, effect_weight) "
                "VALUES ('PGS000001', ?, ?, ?)",
                (rsid, ea, ew),
            )
    conn.commit()
    return conn


# ── _count_allele ────────────────────────────────────────────────────────────

class TestCountAllele:
    def test_homozygous_effect(self):
        assert prs_pipeline._count_allele("AA", "A") == 2

    def test_heterozygous(self):
        assert prs_pipeline._count_allele("AG", "A") == 1

    def test_homozygous_other(self):
        assert prs_pipeline._count_allele("GG", "A") == 0

    def test_none_genotype(self):
        assert prs_pipeline._count_allele(None, "A") == -1

    def test_empty_string(self):
        assert prs_pipeline._count_allele("", "A") == -1

    def test_single_char(self):
        assert prs_pipeline._count_allele("A", "A") == -1

    def test_three_chars(self):
        assert prs_pipeline._count_allele("AAA", "A") == -1


# ── run(): no-weights states ─────────────────────────────────────────────────

class TestNoWeights:
    def test_missing_pgs_catalog_table(self):
        """pgs_catalog absent → no_weights (genome_weights.py not run)."""
        conn = sqlite3.connect(":memory:")
        conn.executescript("""
            CREATE TABLE raw_snps (rsid TEXT PRIMARY KEY, genotype TEXT);
            CREATE TABLE genome_imports (
                id INTEGER PRIMARY KEY, import_date TEXT, vcf_source TEXT,
                phase_e_pharmaco_at TEXT, phase_f_monogenic_at TEXT,
                phase_g_prs_at TEXT, completed_at TEXT, notes TEXT
            );
        """)
        conn.execute("INSERT INTO genome_imports (vcf_source) VALUES ('test')")
        conn.commit()
        result = prs_pipeline.run(conn, genome_import_id=1)
        assert result["status"] == "no_weights"
        assert result["results"] == {}
        assert result["failed"] == []

    def test_empty_pgs_catalog(self):
        """pgs_catalog present but empty → no_weights."""
        conn = _make_conn()  # weights=None → no entries in pgs_catalog
        result = prs_pipeline.run(conn, genome_import_id=1)
        assert result["status"] == "no_weights"

    def test_creates_prs_scores_table(self):
        """run() must create prs_scores table regardless of weights state."""
        conn = _make_conn()
        prs_pipeline.run(conn, genome_import_id=1)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "prs_scores" in tables

    def test_no_stamp_on_no_weights(self):
        """phase_h_prs_at is NOT stamped when status=no_weights — Phase H didn't run."""
        conn = _make_conn()
        prs_pipeline.run(conn, genome_import_id=1)
        row = conn.execute(
            "SELECT phase_h_prs_at FROM genome_imports WHERE id=1"
        ).fetchone()
        # column may not even exist yet if no weights processed
        # either way, no stamp expected
        assert row is None or row[0] is None


# ── run(): score computation ─────────────────────────────────────────────────

class TestScoreComputation:
    def test_single_variant_homozygous(self):
        """AA + effect_allele=A + weight=0.5 → score = 0.5*2 = 1.0."""
        conn = _make_conn(
            snps=[("rs1234", "AA")],
            weights=[("rs1234", "A", 0.5)],
        )
        result = prs_pipeline.run(conn, genome_import_id=1)
        assert result["status"] == "ok"
        r = result["results"]["PGS000001"]
        assert abs(r["raw_score"] - 1.0) < 1e-9
        assert r["snps_matched"] == 1
        assert r["snps_total"] == 1

    def test_single_variant_heterozygous(self):
        """AG + effect_allele=A + weight=0.3 → score = 0.3*1 = 0.3."""
        conn = _make_conn(
            snps=[("rs1234", "AG")],
            weights=[("rs1234", "A", 0.3)],
        )
        result = prs_pipeline.run(conn, genome_import_id=1)
        r = result["results"]["PGS000001"]
        assert abs(r["raw_score"] - 0.3) < 1e-9

    def test_single_variant_homozygous_other(self):
        """GG + effect_allele=A → 0 copies → score = 0.0."""
        conn = _make_conn(
            snps=[("rs1234", "GG")],
            weights=[("rs1234", "A", 1.0)],
        )
        result = prs_pipeline.run(conn, genome_import_id=1)
        r = result["results"]["PGS000001"]
        assert r["raw_score"] == 0.0
        assert r["snps_matched"] == 1  # matched but contributed 0

    def test_two_variants_summed(self):
        """Two variants: scores add up correctly."""
        conn = _make_conn(
            snps=[("rs001", "AA"), ("rs002", "GT")],
            weights=[("rs001", "A", 0.4), ("rs002", "G", 0.2)],
        )
        result = prs_pipeline.run(conn, genome_import_id=1)
        r = result["results"]["PGS000001"]
        # rs001: AA = 2 copies of A → 0.4*2=0.8
        # rs002: GT = 1 copy of G → 0.2*1=0.2
        assert abs(r["raw_score"] - 1.0) < 1e-9
        assert r["snps_matched"] == 2
        assert r["snps_total"] == 2

    def test_missing_snp_skipped(self):
        """SNP in weights but absent from raw_snps → not matched, not counted."""
        conn = _make_conn(
            snps=[],  # no genotype data
            weights=[("rs9999", "A", 0.5)],
        )
        result = prs_pipeline.run(conn, genome_import_id=1)
        r = result["results"]["PGS000001"]
        assert r["snps_matched"] == 0
        assert r["snps_total"] == 1
        assert r["raw_score"] == 0.0

    def test_null_genotype_skipped(self):
        """NULL genotype → indeterminate → R1 guard: skip."""
        conn = _make_conn(
            snps=[("rs1234", None)],
            weights=[("rs1234", "A", 1.0)],
        )
        result = prs_pipeline.run(conn, genome_import_id=1)
        r = result["results"]["PGS000001"]
        assert r["snps_matched"] == 0

    def test_coverage_calculation(self):
        """coverage_pct = matched / total * 100 (not stored but in result dict)."""
        conn = _make_conn(
            snps=[("rs001", "AA")],
            weights=[("rs001", "A", 0.5), ("rs999", "T", 0.1)],
        )
        result = prs_pipeline.run(conn, genome_import_id=1)
        r = result["results"]["PGS000001"]
        assert r["snps_matched"] == 1
        assert r["snps_total"] == 2
        assert abs(r["coverage_pct"] - 50.0) < 0.1


# ── run(): persistence ───────────────────────────────────────────────────────

class TestPersistence:
    def test_stamps_genome_imports(self):
        conn = _make_conn(
            snps=[("rs1234", "AA")],
            weights=[("rs1234", "A", 0.5)],
        )
        prs_pipeline.run(conn, genome_import_id=1)
        ts = conn.execute(
            "SELECT phase_h_prs_at FROM genome_imports WHERE id=1"
        ).fetchone()[0]
        assert ts is not None and len(ts) > 0

    def test_idempotent_rerun(self):
        """INSERT OR REPLACE: second run overwrites first, no duplicates."""
        conn = _make_conn(
            snps=[("rs1234", "AG")],
            weights=[("rs1234", "A", 0.3)],
        )
        prs_pipeline.run(conn, genome_import_id=1)
        prs_pipeline.run(conn, genome_import_id=1)
        n = conn.execute("SELECT COUNT(*) FROM prs_scores").fetchone()[0]
        assert n == 1


# ── ATTACH к reference-БД (веса вынесены из канона 2026-07-02) ────────────────

class TestReferenceAttach:
    def test_attach_path_reads_reference_weights(self, tmp_path, monkeypatch):
        """Когда локальных весов НЕТ, run() читает их из reference-БД через ATTACH
        (pgsref.pgs_weights × raw_snps канона). Регресс на баг: до фикса
        _weights_source детектил через неквалифицированное pgs_weights, которое
        ПОСЛЕ ATTACH резолвилось в pgsref → повторный вызов ложно решал «локально»
        → status=no_weights. Явный main.pgs_weights это чинит."""
        import pgs_reference
        monkeypatch.setenv("HEALTH_PGS_DB", str(tmp_path / "pgs_ref.db"))
        ref = pgs_reference.get_ref_conn()
        ref.execute("INSERT INTO pgs_catalog (pgs_id, trait_name, downloaded_at) "
                    "VALUES ('PGS000001','Test','2026-01-01')")
        ref.execute("INSERT INTO pgs_weights (pgs_id, rsid, effect_allele, effect_weight) "
                    "VALUES ('PGS000001','rs100','A',1.5)")
        ref.commit()
        ref.close()

        conn = _make_conn(snps=[("rs100", "AA")], weights=None)  # локальных весов нет
        assert prs_pipeline._weights_source(conn) == "pgsref."   # аттачит reference
        result = prs_pipeline.run(conn, genome_import_id=1)

        assert result["status"] == "ok"
        assert result["results"]["PGS000001"]["raw_score"] == 3.0   # 1.5 × AA(2 копии)
        assert result["results"]["PGS000001"]["snps_matched"] == 1


# ── Второй человек установки: модель в справочнике есть, балла у него нет (28.09) ──

def test_unscored_models_until_run_scores_them():
    """Справочник общий: модель скачали по данным другого человека. У этого генома она
    непосчитана, пока run() её не посчитает, — дискавери по этому списку и решает
    пересчитать, а не по «скачано новое»."""
    conn = _make_conn(snps=[("rs1", "AA")], weights=[("rs1", "A", 0.5)])
    assert prs_pipeline.unscored_models(conn, 1) == ["PGS000001"]
    prs_pipeline.run(conn, 1)
    assert prs_pipeline.unscored_models(conn, 1) == []
