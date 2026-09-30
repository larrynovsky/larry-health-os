"""Unit tests for wellness_pipeline.py.

Coverage:
  TestMthfr       (6): wildtype/het677/hom677/het1298/compound/missing
  TestComt        (4): AA/GA/GG/missing
  TestFto         (4): AA/TA/TT/missing
  TestHfe         (4): CG/GG/CC/missing
  TestRun         (4): creates table, synthetic profile calls, stamps genome_imports, partial
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import wellness_pipeline as wp


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_conn(snps: dict[str, str]) -> sqlite3.Connection:
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
    # Resolved effect alleles from canonical genetic_variants
    resolved_ea = {
        "rs1801133": "A",   # MTHFR C677T
        "rs1801131": "G",   # MTHFR A1298C
        "rs4680":    "A",   # COMT Val158Met
        "rs9939609": "A",   # FTO
        "rs1799945": "G",   # HFE H63D
    }
    for rsid, genotype in snps.items():
        conn.execute("INSERT INTO raw_snps VALUES (?, ?)", (rsid, genotype))
    for rsid, ea in resolved_ea.items():
        if rsid in snps:
            conn.execute(
                "INSERT INTO genetic_variants VALUES (?, ?, ?)",
                (rsid, ea, "resolved"),
            )
    conn.execute("INSERT INTO genome_imports (vcf_source) VALUES ('test')")
    conn.commit()
    return conn


def _gid(conn) -> int:
    return conn.execute("SELECT MAX(id) FROM genome_imports").fetchone()[0]


# ===========================================================================
# MTHFR
# ===========================================================================

class TestMthfr:
    def _call(self, gt_677: str | None, gt_1298: str | None):
        gts = {}
        if gt_677 is not None:
            gts["rs1801133"] = gt_677
        if gt_1298 is not None:
            gts["rs1801131"] = gt_1298
        db_ea = {"rs1801133": "A", "rs1801131": "G"}
        return wp._call_mthfr(gts, db_ea)

    def test_wildtype(self):
        # CC (no A) + TT (no G)
        r = self._call("CC", "TT")
        assert "Normal" in r.phenotype
        assert r.confidence == "high"

    def test_het677(self):
        r = self._call("GA", "TT")
        assert "Mildly reduced" in r.phenotype
        assert "C677T heterozygous" in r.phenotype
        assert r.confidence == "high"

    def test_hom677(self):
        r = self._call("AA", "TT")
        assert "Moderately reduced" in r.phenotype
        assert "homozygous" in r.phenotype

    def test_het1298(self):
        r = self._call("CC", "TG")
        assert "Mildly reduced" in r.phenotype
        assert "A1298C" in r.phenotype

    def test_compound_het(self):
        r = self._call("GA", "TG")
        assert "compound heterozygous" in r.phenotype.lower() or \
               "compound" in r.phenotype.lower()
        assert "Moderately reduced" in r.phenotype

    def test_missing_both(self):
        r = self._call(None, None)
        assert r.confidence == "indeterminate"


# ===========================================================================
# COMT
# ===========================================================================

class TestComt:
    def _call(self, gt: str | None):
        gts = {"rs4680": gt} if gt else {}
        return wp._call_comt(gts, {"rs4680": "A"})

    def test_low_activity_aa(self):
        # Confirms Met/Met (AA): 2 copies of A
        r = self._call("AA")
        assert "Low COMT" in r.phenotype
        assert "Met/Met" in r.phenotype

    def test_intermediate_ga(self):
        r = self._call("GA")
        assert "Intermediate" in r.phenotype
        assert "Val/Met" in r.phenotype

    def test_high_activity_gg(self):
        r = self._call("GG")
        assert "High COMT" in r.phenotype
        assert "Val/Val" in r.phenotype

    def test_missing(self):
        r = self._call(None)
        assert r.confidence == "indeterminate"


# ===========================================================================
# FTO
# ===========================================================================

class TestFto:
    def _call(self, gt: str | None):
        gts = {"rs9939609": gt} if gt else {}
        return wp._call_fto(gts, {"rs9939609": "A"})

    def test_elevated_aa(self):
        r = self._call("AA")
        assert "Elevated" in r.phenotype

    def test_modest_ta(self):
        r = self._call("TA")
        assert "Modest" in r.phenotype
        assert r.confidence == "medium"

    def test_wildtype_tt(self):
        r = self._call("TT")
        assert "No FTO" in r.phenotype

    def test_missing(self):
        r = self._call(None)
        assert r.confidence == "indeterminate"


# ===========================================================================
# HFE
# ===========================================================================

class TestHfe:
    def _call(self, gt: str | None):
        gts = {"rs1799945": gt} if gt else {}
        return wp._call_hfe(gts, {"rs1799945": "G"})

    def test_het_cg(self):
        # palindromic_het_resolved → G is effect allele
        r = self._call("CG")
        assert "heterozygous" in r.phenotype
        assert r.confidence == "high"

    def test_hom_gg(self):
        r = self._call("GG")
        assert "homozygous" in r.phenotype

    def test_wildtype_cc(self):
        r = self._call("CC")
        assert "wildtype" in r.phenotype.lower() or "No H63D" in r.phenotype

    def test_missing(self):
        r = self._call(None)
        assert r.confidence == "indeterminate"


# ===========================================================================
# run() integration
# ===========================================================================

class TestRun:
    # Синтетический профиль (не генотип какого-либо тенанта; до 2026-09-25 здесь стоял живой).
    _SYNTH_SNPS = {
        "rs1801133": "AA",
        "rs1801131": "TT",
        "rs4680":    "AA",
        "rs9939609": "TT",
        "rs1799945": "CC",
    }

    def test_creates_table(self):
        conn = _make_conn(self._SYNTH_SNPS)
        wp.run(conn, _gid(conn))
        count = conn.execute("SELECT COUNT(*) FROM wellness_phenotypes").fetchone()[0]
        assert count == 4

    def test_synthetic_profile_calls(self):
        conn = _make_conn(self._SYNTH_SNPS)
        result = wp.run(conn, _gid(conn))
        assert result["status"] == "ok"
        r = result["results"]
        assert "Moderately reduced" in r["MTHFR/folate_metabolism"]
        assert "Low COMT" in r["COMT/dopamine_metabolism"]
        assert "No FTO" in r["FTO/obesity_risk"]
        hfe = r["HFE/iron_overload_risk"]
        assert "wildtype" in hfe.lower() or "No H63D" in hfe

    def test_stamps_genome_imports(self):
        conn = _make_conn(self._SYNTH_SNPS)
        wp.run(conn, _gid(conn))
        stamp = conn.execute(
            "SELECT phase_g_prs_at FROM genome_imports WHERE id = 1"
        ).fetchone()[0]
        assert stamp is not None

    def test_partial_on_missing(self):
        # No SNPs at all → all indeterminate
        conn = _make_conn({})
        conn.execute("INSERT INTO genome_imports (vcf_source) VALUES ('test')")
        conn.commit()
        result = wp.run(conn, _gid(conn))
        assert result["status"] == "partial"
        assert len(result["indeterminate_traits"]) == 4
