"""L1 unit: effect_allele.resolve_effect_allele (genome strand-fix Ф1).

Золотой набор + property-тест strand-симметрии. Чистый, без сети и без БД.
«Золотые» ожидания биологически проверяются человеком на charter-шаге
(якорь — вариант, который 23andMe отдаёт на минус-strand); здесь проверяется только логика.
"""
from __future__ import annotations

import itertools
from pathlib import Path
import sys

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest

from effect_allele import resolve_effect_allele

pytestmark = pytest.mark.unit

_COMP = {"A": "T", "T": "A", "C": "G", "G": "C"}

# name, genotype, ref, alts, exp_allele, exp_status, exp_carrier_count
#   exp_carrier_count = gt.count(effect_allele) для resolved (0=wildtype,1=hetero,2=homo)
GOLDEN = [
    # Противоположный strand (как C677T у 23andMe: минус-strand, ClinVar ref C / alt T).
    ("opposite_strand_homo_alt",     "AA", "C", ["T"], "A", "resolved", 2),
    ("hetero_opposite_strand",       "TC", "G", ["A"], "T", "resolved", 1),
    ("homo_ref_nonpalindromic",      "GG", "G", ["A"], "A", "resolved", 0),
    ("homo_alt_same_strand",         "AA", "G", ["A"], "A", "resolved", 2),
    ("opposite_strand_homo_ref",     "GG", "C", ["T"], "A", "resolved", 0),
    ("hetero_same_strand",           "GA", "G", ["A"], "A", "resolved", 1),
    # Палиндромы — неразрешимы из одного генотипа.
    ("palindromic_cg_homo",          "CC", "C", ["G"], None, "palindromic", None),
    ("palindromic_at_hetero",        "AT", "A", ["T"], None, "palindromic", None),
    # Строгая консервативность.
    ("multiallelic_strict_null",     "CC", "C", ["G", "T"], None, "multiallelic_ambiguous", None),
    # Инделы / no-call.
    ("nocall_dashes",                "--", "C", ["T"], None, "no_call", None),
    ("indel_II",                     "II", "C", ["T"], None, "no_call", None),
    ("indel_DD",                     "DD", "C", ["T"], None, "no_call", None),
    ("single_base_genotype",         "A",  "C", ["T"], None, "no_call", None),
    # Нет данных.
    ("no_ref",                       "AG", "",  ["T"], None, "no_data", None),
    ("no_alt",                       "AG", "C", [],    None, "no_data", None),
    ("alt_equals_ref_only",          "AG", "C", ["C"], None, "no_data", None),
    # Генотип вне пары {ref,alt} ни на одном strand (третий аллель G).
    ("genotype_mismatch_third",      "AG", "A", ["C"], None, "genotype_mismatch", None),
]


@pytest.mark.parametrize(
    "name,gt,ref,alts,exp_allele,exp_status,exp_count",
    GOLDEN, ids=[g[0] for g in GOLDEN],
)
def test_golden(name, gt, ref, alts, exp_allele, exp_status, exp_count):
    allele, status = resolve_effect_allele(gt, ref, alts)
    assert (allele, status) == (exp_allele, exp_status), name
    if exp_count is not None:
        # downstream-смысл: сколько раз effect_allele встречается в генотипе
        assert gt.upper().count(allele) == exp_count, name


def test_strand_symmetry_exhaustive():
    """Инвариант: переворот strand генотипа даёт тот же status, а для resolved —
    комплементарный аллель. Ловит асимметричные strand-баги в целом."""
    bases = "ACGT"
    for g1, g2, ref, alt in itertools.product(bases, bases, bases, bases):
        gt = g1 + g2
        a1, s1 = resolve_effect_allele(gt, ref, [alt])
        gt_c = _COMP[g1] + _COMP[g2]
        a2, s2 = resolve_effect_allele(gt_c, ref, [alt])
        assert s1 == s2, (gt, ref, alt, s1, s2)
        if s1 == "resolved":
            assert a1 is not None and a2 is not None
            assert _COMP[a1] == a2, (gt, ref, alt, a1, a2)


def test_resolved_allele_never_invents_carrier():
    """Безопасность: если resolved и effect_allele встречается в генотипе
    (носитель), то на ИСХОДНОМ strand хотя бы один аллель генотипа обязан
    соответствовать паре {ref,alt}. Не должно быть носителя «из воздуха»."""
    bases = "ACGT"
    for g1, g2, ref, alt in itertools.product(bases, bases, bases, bases):
        gt = g1 + g2
        allele, status = resolve_effect_allele(gt, ref, [alt])
        if status == "resolved" and allele in gt:
            # effect_allele на strand генотипа → он же должен быть валидным
            # членом резолюции: либо alt (тот же strand), либо comp(alt) (обратный)
            assert allele in (alt, _COMP[alt]), (gt, ref, alt, allele)
