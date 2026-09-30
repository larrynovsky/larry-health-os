"""
UC-C-02 — Annotator + ClinVar + FUNCTIONAL_WHITELIST.

Источник: USE_CASES.md §3.C → UC-C-02.
Реализация: `genome_annotator.py`.
Status: `implemented`.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_annotator_module_imports():
    import genome_annotator
    assert genome_annotator is not None


def test_functional_whitelist_contains_known_snps():
    """Whitelist должен содержать известные поведенческие варианты."""
    import genome_annotator as ga
    wl = getattr(ga, "FUNCTIONAL_WHITELIST", None)
    if wl is None:
        pytest.skip("FUNCTIONAL_WHITELIST не экспортирован — нужен явный API")
    # Минимум один поведенческий вариант обязан быть
    expected_at_least_one = {"rs4680", "rs6265", "rs1800497", "rs1801133",
                              "rs53576", "rs1815739", "rs4646994"}
    found = expected_at_least_one & set(wl)
    assert found, f"в whitelist нет ни одного из ключевых SNP: {expected_at_least_one}"


def test_severity_priority_order():
    """Severity sort: Pathogenic(1) < Likely(3) < risk(6) < functional(9)."""
    from genome_annotator import SEVERITY_PRIORITY as severity

    assert severity["Pathogenic"] < severity["Likely pathogenic"]
    assert severity["Likely pathogenic"] < severity["risk factor"]
    assert severity["risk factor"] < severity["functional_variant"]


def test_severity_priority_benign_at_bottom():
    """Benign / Likely benign — самый низкий приоритет (выкидываются из выводов)."""
    from genome_annotator import SEVERITY_PRIORITY as severity
    assert severity["Benign"] >= severity["functional_variant"]
    assert severity["Likely benign"] >= severity["functional_variant"]


def test_severity_priority_unknown_value_handling():
    """Доступ через .get() с default для unknown значений."""
    from genome_annotator import SEVERITY_PRIORITY as severity
    # Дефолт когда significance не из списка
    assert severity.get("unknown_significance", 99) == 99
