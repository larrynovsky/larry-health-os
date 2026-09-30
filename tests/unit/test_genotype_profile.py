"""Профиль генотипа тенанта в публичной зоне (2026-09-25, нить genotype-scrub).

Судья: pii_census.genotype_profiles (dict-литералы {rsid: генотип}) + profile_leaks (различающие
совпадения с тенантами). Все генотипы здесь — синтетика двух вымышленных тенантов; ни один
не взят из живой базы.
"""
import pii_census as pc

T = {
    "tenant_a": {"rs900001": "CT", "rs900002": "CT", "rs900003": "GT", "rs900004": "CC", "rs900005": "AA"},
    "tenant_b": {"rs900001": "CC", "rs900002": "TT", "rs900003": "TT", "rs900004": "CC", "rs900005": "AA"},
}

LEAKY = '''
def _snps():
    return {"rs900001": "CT", "rs900002": "TC", "rs900003": "TG", "rs900004": "CC", "rs900005": "AA"}
'''


def test_profile_extracted_and_normalized():
    ps = pc.genotype_profiles(LEAKY)
    assert len(ps) == 1
    _, d = ps[0]
    assert d == {"rs900001": "CT", "rs900002": "CT", "rs900003": "GT", "rs900004": "CC", "rs900005": "AA"}


def test_distinguishing_matches_flag_the_profile():
    leaks = pc.profile_leaks({"f.py": pc.genotype_profiles(LEAKY)}, T, min_hits=3)
    assert leaks == [("f.py", 3, "tenant_a", 3)]


def test_common_genotypes_do_not_count():
    # rs4 CC и rs5 AA совпадают у обоих — человека не выдают, профиль не судится
    src = 'X = {"rs900004": "CC", "rs900005": "AA"}'
    assert pc.profile_leaks({"f.py": pc.genotype_profiles(src)}, T, min_hits=1) == []


def test_enumeration_table_is_not_a_profile():
    # одиночные вызовы с одним rsid — знание «генотип → фенотип», не профиль
    src = 'a = f({"rs900001": "CT"})\nb = f({"rs900001": "CC"})\nc = f({"rs900001": "TT"})'
    assert pc.genotype_profiles(src) == []


def test_single_tenant_is_blind_by_construction():
    one = {"tenant_a": T["tenant_a"]}
    assert pc.profile_leaks({"f.py": pc.genotype_profiles(LEAKY)}, one, min_hits=1) == []
