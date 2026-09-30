"""Unit tests for traits_pipeline.py.

Coverage:
  TestLactase        (4): GG/AG/AA/missing
  TestEyeColor       (4): GG/AG/AA/missing
  TestApoe           (6): e3e3/e2e3/e3e4/e4e4/e2e4/missing
  TestRun            (5): creates table, upserts, stamps genome_imports,
                          partial on missing, db_ea override
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import traits_pipeline as tp


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_conn(snps: dict[str, str]) -> sqlite3.Connection:
    """In-memory DB with raw_snps, genetic_variants, genome_imports."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE raw_snps (rsid TEXT PRIMARY KEY, genotype TEXT);
        CREATE TABLE genetic_variants (
            rsid TEXT PRIMARY KEY,
            effect_allele TEXT,
            effect_allele_status TEXT
        );
        CREATE TABLE genome_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_date TEXT DEFAULT (date('now')),
            vcf_source TEXT,
            phase_e_pharmaco_at TEXT,
            phase_f_monogenic_at TEXT,
            phase_g_prs_at TEXT,
            completed_at TEXT,
            notes TEXT
        );
    """)
    for rsid, genotype in snps.items():
        conn.execute("INSERT INTO raw_snps VALUES (?, ?)", (rsid, genotype))
    # Add resolved effect alleles from the reference DB values for each SNP
    resolved_ea = {
        "rs4988235": "A",
        "rs12913832": "G",
        "rs7412": "T",
        "rs429358": "C",
    }
    for rsid, ea in resolved_ea.items():
        if rsid in snps:
            conn.execute(
                "INSERT INTO genetic_variants VALUES (?, ?, ?)",
                (rsid, ea, "resolved"),
            )
    conn.execute("INSERT INTO genome_imports (vcf_source) VALUES ('test')")
    conn.commit()
    return conn


def _genome_import_id(conn) -> int:
    return conn.execute("SELECT MAX(id) FROM genome_imports").fetchone()[0]


# ===========================================================================
# Lactase persistence
# ===========================================================================

class TestLactase:
    def test_gg_nonpersistent(self):
        r = tp._call_lactase({"rs4988235": "GG"}, {"rs4988235": "A"})
        assert r.trait == "lactase_persistence"
        assert "Non-persistent" in r.phenotype
        assert r.confidence == "high"

    def test_ag_partial(self):
        r = tp._call_lactase({"rs4988235": "AG"}, {"rs4988235": "A"})
        assert "Partially persistent" in r.phenotype
        assert r.confidence == "high"

    def test_aa_persistent(self):
        r = tp._call_lactase({"rs4988235": "AA"}, {"rs4988235": "A"})
        assert "Persistent" in r.phenotype
        assert "Non" not in r.phenotype
        assert r.confidence == "high"

    def test_missing_snp(self):
        r = tp._call_lactase({}, {})
        assert r.confidence == "indeterminate"
        assert r.phenotype == "indeterminate"


# ===========================================================================
# Eye color tendency
# ===========================================================================

class TestEyeColor:
    def test_gg_light(self):
        r = tp._call_eye_color({"rs12913832": "GG"}, {"rs12913832": "G"})
        assert "Light eyes" in r.phenotype
        assert r.confidence == "high"

    def test_ag_mixed(self):
        r = tp._call_eye_color({"rs12913832": "AG"}, {"rs12913832": "G"})
        assert "Mixed" in r.phenotype
        assert r.confidence == "medium"

    def test_aa_dark(self):
        r = tp._call_eye_color({"rs12913832": "AA"}, {"rs12913832": "G"})
        assert "Dark" in r.phenotype or "brown" in r.phenotype.lower()
        assert r.confidence == "high"

    def test_missing_snp(self):
        r = tp._call_eye_color({}, {})
        assert r.confidence == "indeterminate"


# ===========================================================================
# APOE genotype
# ===========================================================================

class TestApoe:
    def _call(self, gt_7412: str, gt_429358: str):
        gts = {"rs7412": gt_7412, "rs429358": gt_429358}
        ea = {"rs7412": "T", "rs429358": "C"}
        return tp._call_apoe(gts, ea)

    def test_e3e3(self):
        # rs7412=CC (no T), rs429358=TT (no C) → ε3/ε3
        r = self._call("CC", "TT")
        assert r.phenotype == "ε3/ε3"
        assert r.confidence == "high"

    def test_e2e3(self):
        # rs7412=CT (one T), rs429358=TT (no C) → ε2/ε3
        r = self._call("CT", "TT")
        assert r.phenotype == "ε2/ε3"

    def test_e3e4(self):
        # rs7412=CC (no T), rs429358=TC (one C) → ε3/ε4
        r = self._call("CC", "TC")
        assert r.phenotype == "ε3/ε4"

    def test_e4e4(self):
        r = self._call("CC", "CC")
        assert r.phenotype == "ε4/ε4"

    def test_e2e4_ambiguous(self):
        r = self._call("CT", "TC")
        assert "ε2/ε4" in r.phenotype
        assert r.confidence == "medium"

    def test_missing_snp(self):
        r = tp._call_apoe({}, {})
        assert r.confidence == "indeterminate"


# ===========================================================================
# run() integration
# ===========================================================================

class TestRun:
    def _run(self, snps: dict) -> tuple[dict, sqlite3.Connection]:
        conn = _make_conn(snps)
        gid = _genome_import_id(conn)
        result = tp.run(conn, gid)
        return result, conn

    def test_creates_table(self):
        result, conn = self._run({
            "rs4988235": "AG", "rs12913832": "GG",
            "rs7412": "CT", "rs429358": "TT",
        })
        count = conn.execute("SELECT COUNT(*) FROM trait_phenotypes").fetchone()[0]
        assert count == 3

    def test_synthetic_profile_calls(self):
        # Синтетический профиль (не генотип какого-либо тенанта): AG / GG / ε2ε3.
        result, _ = self._run({
            "rs4988235": "AG", "rs12913832": "GG",
            "rs7412": "CT", "rs429358": "TT",
        })
        assert result["status"] == "ok"
        assert "Partially persistent" in result["results"]["lactase_persistence"]
        assert "Light eyes" in result["results"]["eye_color_tendency"]
        assert result["results"]["apoe_genotype"] == "ε2/ε3"

    def test_stamps_genome_imports(self):
        result, conn = self._run({
            "rs4988235": "AG", "rs12913832": "GG",
            "rs7412": "CT", "rs429358": "TT",
        })
        stamp = conn.execute(
            "SELECT phase_f_monogenic_at FROM genome_imports WHERE id = 1"
        ).fetchone()[0]
        assert stamp is not None

    def test_partial_on_missing_snp(self):
        # Only one SNP present → some indeterminate
        result, _ = self._run({"rs4988235": "GG"})
        assert result["status"] == "partial"
        assert len(result["indeterminate_traits"]) > 0

    def test_db_ea_overrides_hardcoded(self):
        """Strand-flip guard: if db_ea differs, it wins over hardcoded."""
        conn = _make_conn({"rs4988235": "AA"})
        # Override effect allele to G (flipped) via genetic_variants
        conn.execute(
            "UPDATE genetic_variants SET effect_allele = 'G' WHERE rsid = 'rs4988235'"
        )
        conn.commit()
        gid = _genome_import_id(conn)
        result = tp.run(conn, gid)
        # With ea=G and genotype=AA, count(G in AA)=0 → non-persistent
        row = conn.execute(
            "SELECT phenotype FROM trait_phenotypes WHERE trait='lactase_persistence'"
        ).fetchone()
        assert "Non-persistent" in row[0]
