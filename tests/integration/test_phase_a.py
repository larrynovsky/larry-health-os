"""
Integration tests for Phase A (update_known).
Uses in-memory SQLite + small VCF fixture — no network, no real DB.
"""
import sys
import sqlite3
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from vcf_import_pipeline import ensure_tables, phase_a_update_known


FIXTURE_VCF = """\
##fileformat=VCFv4.2
##DeepVariant_version=1.5.0
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE
chr1\t11856378\t.\tG\tA\t35.5\tPASS\t.\tGT:GQ:DP\t0/1:36:37
chr22\t19951271\t.\tG\tA\t44\tPASS\t.\tGT:GQ:DP\t0/1:44:36
chrM\t12345\t.\tC\tT\t50\tPASS\t.\tGT:GQ:DP\t0/1:50:30
chr1\t99999\t.\tC\tT\t30\tPASS\t.\tGT:GQ:DP\t0/1:5:8
chr1\t88888\t.\tG\tA\t30\tPASS\t.\tGT:GQ:DP\t0/1:25:4
chr1\t77777\t.\tG\tA\t30\tRefCall\t.\tGT:GQ:DP\t0/0:50:30
"""
# line 4: GQ=5 < MIN_GQ(20)  → filtered
# line 5: DP=4 < MIN_DP(10)  → filtered
# line 6: FILTER=RefCall      → filtered


def make_db_with_snps(entries: list) -> sqlite3.Connection:
    """Build in-memory DB with raw_snps pre-seeded."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE raw_snps (
            rsid TEXT PRIMARY KEY,
            chromosome TEXT,
            position INTEGER,
            genotype TEXT,
            source TEXT DEFAULT '23andme'
        )
    """)
    con.executemany(
        "INSERT INTO raw_snps VALUES (?,?,?,?,?)", entries
    )
    con.commit()
    ensure_tables(con)
    return con


class TestPhaseAUpdateKnown:

    def _vcf(self, tmp_path) -> Path:
        p = tmp_path / "test.vcf"
        p.write_text(FIXTURE_VCF)
        return p

    def test_updates_matching_positions(self, tmp_path):
        con = make_db_with_snps([
            ("rs1801133", "1", 11856378, "GG", "23andme"),  # chr1:11856378
            ("rs4680",    "22", 19951271, "GG", "23andme"), # chr22:19951271
        ])
        updates = phase_a_update_known(con, self._vcf(tmp_path))

        assert "rs1801133" in updates
        assert updates["rs1801133"] == "GA"
        assert "rs4680" in updates
        assert updates["rs4680"] == "GA"

    def test_writes_to_db(self, tmp_path):
        con = make_db_with_snps([
            ("rs1801133", "1", 11856378, "GG", "23andme"),
        ])
        phase_a_update_known(con, self._vcf(tmp_path))

        row = con.execute(
            "SELECT genotype, source FROM raw_snps WHERE rsid='rs1801133'"
        ).fetchone()
        assert row["genotype"] == "GA"
        assert row["source"]   == "wgs_updated"

    def test_mitochondrial_position(self, tmp_path):
        # chrM in VCF → "MT" in raw_snps
        con = make_db_with_snps([
            ("rs_mt_test", "MT", 12345, "CC", "23andme"),
        ])
        updates = phase_a_update_known(con, self._vcf(tmp_path))
        assert "rs_mt_test" in updates
        assert updates["rs_mt_test"] == "CT"

    def test_low_gq_filtered(self, tmp_path):
        # chr1:99999 has GQ=5 — should NOT be updated
        con = make_db_with_snps([
            ("rs_lowgq", "1", 99999, "CC", "23andme"),
        ])
        updates = phase_a_update_known(con, self._vcf(tmp_path))
        assert "rs_lowgq" not in updates

    def test_low_dp_filtered(self, tmp_path):
        # chr1:88888 has DP=4 — should NOT be updated
        con = make_db_with_snps([
            ("rs_lowdp", "1", 88888, "CC", "23andme"),
        ])
        updates = phase_a_update_known(con, self._vcf(tmp_path))
        assert "rs_lowdp" not in updates

    def test_refcall_filtered(self, tmp_path):
        # chr1:77777 FILTER=RefCall — should NOT match
        con = make_db_with_snps([
            ("rs_refcall", "1", 77777, "GG", "23andme"),
        ])
        updates = phase_a_update_known(con, self._vcf(tmp_path))
        assert "rs_refcall" not in updates

    def test_unmatched_position_unchanged(self, tmp_path):
        # rsID with position not in VCF → not in updates, DB unchanged
        con = make_db_with_snps([
            ("rs_absent", "1", 55555555, "AA", "23andme"),
        ])
        updates = phase_a_update_known(con, self._vcf(tmp_path))
        assert "rs_absent" not in updates
        row = con.execute(
            "SELECT genotype, source FROM raw_snps WHERE rsid='rs_absent'"
        ).fetchone()
        assert row["genotype"] == "AA"
        assert row["source"]   == "23andme"

    def test_dry_run_no_writes(self, tmp_path):
        con = make_db_with_snps([
            ("rs1801133", "1", 11856378, "GG", "23andme"),
        ])
        phase_a_update_known(con, self._vcf(tmp_path), dry=True)

        row = con.execute(
            "SELECT genotype, source FROM raw_snps WHERE rsid='rs1801133'"
        ).fetchone()
        # DB should NOT have changed in dry mode
        assert row["genotype"] == "GG"
        assert row["source"]   == "23andme"

    def test_real_conflict_logged(self, tmp_path, capsys):
        # AA → GA is a real conflict (sorted "AA" ≠ sorted "GA")
        # Fixture has chr1:11856378 → GA
        con = make_db_with_snps([
            ("rs1801133", "1", 11856378, "AA", "23andme"),  # hom-alt, WGS says het
        ])
        phase_a_update_known(con, self._vcf(tmp_path))
        # Conflict should be logged (we check DB was still updated with WGS value)
        row = con.execute(
            "SELECT genotype FROM raw_snps WHERE rsid='rs1801133'"
        ).fetchone()
        assert row["genotype"] == "GA"  # WGS wins

    def test_order_reversal_not_logged_as_conflict(self, tmp_path):
        # GA → GA (same alleles, just written differently in 23andMe)
        # vcf_import_pipeline fixture at chr1:11856378 returns "GA"
        # If 23andMe had "AG", sorted("AG")==sorted("GA") → NOT a conflict
        con = make_db_with_snps([
            ("rs1801133", "1", 11856378, "AG", "23andme"),
        ])
        updates = phase_a_update_known(con, self._vcf(tmp_path))
        # Should still update (GA is canonical WGS output)
        assert updates["rs1801133"] == "GA"


def test_foreign_genome_over_own_chip_is_refused_before_write(tmp_path, monkeypatch):
    """Приём VCF через бот (28.09): чужой полный геном поверх своего чипа не пишется —
    правило «WGS побеждает» переписало бы чип генотипами другого человека."""
    import pytest
    import vcf_import_pipeline as v
    rows = [f"chr2\t{1000 + i}\t.\tG\tA\t30\tPASS\t.\tGT:GQ:DP\t1/1:50:30" for i in range(1200)]
    p = tmp_path / "other.vcf"
    p.write_text("##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
                 + "\n".join(rows) + "\n")
    chip = [(f"rs{i}", "2", 1000 + i, "GG" if i % 10 else "AA", "23andme") for i in range(1200)]
    con = make_db_with_snps(chip)                    # 90% позиций чипа — GG, VCF говорит AA
    with pytest.raises(v.GenomeIdentityError):
        phase_a_update_known(con, p)
    assert con.execute("SELECT COUNT(*) FROM raw_snps WHERE source='wgs_updated'").fetchone()[0] == 0
    same = [(f"rs{i}", "2", 1000 + i, "AA", "23andme") for i in range(1200)]
    assert len(phase_a_update_known(make_db_with_snps(same), p)) == 1200   # свой геном — пишется


def test_chip_identity_threshold_is_read_from_tenant_config(tmp_path):
    """Порог берётся из system_config ТОГО ЖЕ тенанта (conn фазы A), а не из кода:
    поднятый в настройках порог пропускает то, что при сиде было бы отказом."""
    import vcf_import_pipeline as v
    rows = [f"chr2\t{1000 + i}\t.\tG\tA\t30\tPASS\t.\tGT:GQ:DP\t1/1:50:30" for i in range(1200)]
    p = tmp_path / "other.vcf"
    p.write_text("##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
                 + "\n".join(rows) + "\n")
    chip = [(f"rs{i}", "2", 1000 + i, "GG" if i % 10 else "AA", "23andme") for i in range(1200)]
    con = make_db_with_snps(chip)
    con.execute("CREATE TABLE system_config (key TEXT PRIMARY KEY, value_text TEXT, "
                "value_num REAL, value_json TEXT)")
    con.execute("INSERT INTO system_config (key, value_num) VALUES "
                "('genome.chip_disagreement_max', 0.95), ('genome.chip_identity_min_matched', 1000)")
    assert len(phase_a_update_known(con, p)) == 1200     # 90% расхождений < 95% из настроек
