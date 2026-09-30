"""
Unit tests for vcf_import_pipeline helper functions.
No files, no DB, no network — pure function tests.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from vcf_import_pipeline import norm_chrom, resolve_genotype, is_snp


# ── norm_chrom ───────────────────────────────────────────────────────────

class TestNormChrom:
    def test_autosomal(self):
        for i in range(1, 23):
            assert norm_chrom(f"chr{i}") == str(i)

    def test_sex_chromosomes(self):
        assert norm_chrom("chrX") == "X"
        assert norm_chrom("chrY") == "Y"

    def test_mitochondrial(self):
        # VCF: chrM  →  23andMe: MT
        assert norm_chrom("chrM") == "MT"

    def test_already_normalised(self):
        # Если prefix уже убран — не ломаемся
        assert norm_chrom("1")  == "1"
        assert norm_chrom("MT") == "MT"


# ── is_snp ───────────────────────────────────────────────────────────────

class TestIsSnp:
    def test_snp(self):
        assert is_snp("G", "A") is True
        assert is_snp("C", "T") is True

    def test_insertion(self):
        assert is_snp("A", "AT") is False

    def test_deletion(self):
        assert is_snp("AT", "A") is False

    def test_star_allele(self):
        # "*" = spanning deletion — не SNP
        assert is_snp("G", "*") is False

    def test_dot_allele(self):
        assert is_snp("G", ".") is False

    def test_multiallelic_snp(self):
        # is_snp проверяет только один ALT; multi-allelic обрабатывает resolve_genotype
        assert is_snp("G", "A") is True


# ── resolve_genotype ─────────────────────────────────────────────────────

class TestResolveGenotype:
    # --- базовые случаи ---

    def test_het(self):
        assert resolve_genotype("0/1", "G", "A") == "GA"

    def test_hom_ref(self):
        assert resolve_genotype("0/0", "G", "A") == "GG"

    def test_hom_alt(self):
        assert resolve_genotype("1/1", "G", "A") == "AA"

    # --- phased ---

    def test_phased_het(self):
        assert resolve_genotype("0|1", "G", "A") == "GA"

    def test_phased_hom(self):
        assert resolve_genotype("1|1", "G", "A") == "AA"

    # --- no-call ---

    def test_full_no_call(self):
        assert resolve_genotype("./.", "G", "A") is None

    def test_partial_no_call(self):
        # один аллель NoCall
        assert resolve_genotype("./1", "G", "A") is None
        assert resolve_genotype("0/.", "G", "A") is None

    # --- multi-allelic ALT ---

    def test_multiallelic_ref_and_second_alt(self):
        # GT=0/2 → ref + alt[1]
        assert resolve_genotype("0/2", "G", "A,C") == "GC"

    def test_multiallelic_both_alts(self):
        # GT=1/2 → alt[0] + alt[1]
        assert resolve_genotype("1/2", "G", "A,C") == "AC"

    # --- индел → None ---

    def test_insertion_returns_none(self):
        assert resolve_genotype("0/1", "A", "AT") is None

    def test_deletion_returns_none(self):
        assert resolve_genotype("0/1", "AT", "A") is None

    def test_multibase_ref_returns_none(self):
        assert resolve_genotype("0/0", "AT", "GC") is None

    # --- edge ---

    def test_invalid_index_returns_none(self):
        # GT=0/3 но только 1 ALT → IndexError → None
        assert resolve_genotype("0/3", "G", "A") is None

    def test_non_numeric_gt_returns_none(self):
        assert resolve_genotype("./X", "G", "A") is None


# ── conflict detection logic ──────────────────────────────────────────────

class TestConflictDetection:
    """
    Проверяет, что order-insensitive сравнение правильно
    различает ложные (порядковые) и настоящие конфликты.
    """

    def _is_real_conflict(self, old_gt: str, new_gt: str) -> bool:
        return sorted(old_gt) != sorted(new_gt)

    # Ложные конфликты (один и тот же набор аллелей)
    def test_order_reversal_ag_ga(self):
        assert self._is_real_conflict("AG", "GA") is False

    def test_order_reversal_ct_tc(self):
        assert self._is_real_conflict("CT", "TC") is False

    def test_order_reversal_gt_tg(self):
        assert self._is_real_conflict("GT", "TG") is False

    def test_same_homozygous(self):
        assert self._is_real_conflict("AA", "AA") is False

    # Настоящие конфликты (разные аллели)
    def test_hom_to_het(self):
        assert self._is_real_conflict("CC", "AC") is True

    def test_hom_swap(self):
        assert self._is_real_conflict("GG", "TT") is True

    def test_het_to_hom(self):
        assert self._is_real_conflict("AG", "AA") is True

    def test_nocall_to_call(self):
        # "--" → "CC": настоящая новая информация
        assert self._is_real_conflict("--", "CC") is True


# ── 28.09: сжатый VCF, значимость по всем RCV, drug response ────────────────

def test_gz_vcf_reads_same_records_as_plain(tmp_path):
    """Сжатый файл давал 0 записей молча: читался как текст."""
    import gzip
    from vcf_import_pipeline import open_vcf
    body = "##fileformat=VCFv4.2\n#CHROM\tPOS\n1\t100\n"
    plain = tmp_path / "a.vcf"
    plain.write_text(body)
    packed = tmp_path / "a.vcf.gz"
    with gzip.open(packed, "wt") as fh:
        fh.write(body)
    renamed = tmp_path / "a_no_suffix"          # сжатие узнаётся по сигнатуре, не по имени
    renamed.write_bytes(packed.read_bytes())
    for p in (plain, packed, renamed):
        with open_vcf(p) as fh:
            assert fh.read() == body, p.name


def test_significance_from_every_rcv_not_first():
    """Значимость, стоящая не первой RCV, терялась (F5 Leiden выходил с None)."""
    from vcf_import_pipeline import clinvar_summary, significance_kept
    cv = {"rcv": [{"clinical_significance": "Benign", "conditions": {"name": "not specified"}},
                  {"clinical_significance": "drug response", "conditions": [{"name": "warfarin response"}]},
                  {"clinical_significance": "Benign"}]}
    sig, conds = clinvar_summary(cv)
    assert sig == "Benign; drug response"
    assert conds == "not specified; warfarin response"
    assert significance_kept(sig)
    assert not significance_kept("Benign; Likely benign")
    assert significance_kept("Pathogenic/Likely pathogenic")
    assert clinvar_summary({}) == (None, None)
    assert clinvar_summary({"rcv": {"clinical_significance": "Risk factor"}})[0] == "Risk factor"
