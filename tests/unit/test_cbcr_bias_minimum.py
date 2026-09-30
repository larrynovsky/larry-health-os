"""W5A-D3: tests для _check_bias_minimum."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest
import cbcr_hypothesis as ch

pytestmark = pytest.mark.unit


def test_empty_bias_risks_flagged():
    """Пустой bias_risks → critical_issue."""
    hyp = {"bias_risks": []}
    issues = ch._check_bias_minimum(hyp, required=4)
    assert len(issues) == 1
    assert "пустой" in issues[0].lower()


def test_missing_field_flagged():
    """Нет поля bias_risks → critical_issue."""
    issues = ch._check_bias_minimum({}, required=4)
    assert len(issues) == 1


def test_only_two_biases_flagged_at_threshold_4():
    """2 биaса при пороге 4 → critical_issue."""
    hyp = {"bias_risks": [
        {"bias_type": "Availability Bias", "description": "post-chemo как очевидно", "mitigation": "альтернативы"},
        {"bias_type": "Anchoring Bias", "description": "trigger от drift", "mitigation": "decision_threshold"},
    ]}
    issues = ch._check_bias_minimum(hyp, required=4)
    assert len(issues) == 1
    assert "2/4+" in issues[0] or "2/" in issues[0]


def test_three_biases_flagged():
    """3 биaса всё ещё недостаточно для порога 4."""
    hyp = {"bias_risks": [
        {"bias_type": "Availability", "description": "x", "mitigation": "y"},
        {"bias_type": "Anchoring", "description": "x", "mitigation": "y"},
        {"bias_type": "Confirmation", "description": "x", "mitigation": "y"},
    ]}
    issues = ch._check_bias_minimum(hyp, required=4)
    assert len(issues) == 1


def test_four_biases_ok():
    """4 канонических биaса → нет issues."""
    hyp = {"bias_risks": [
        {"bias_type": "Availability Bias", "description": "x", "mitigation": "y"},
        {"bias_type": "Representative Bias", "description": "x", "mitigation": "y"},
        {"bias_type": "Anchoring Bias", "description": "x", "mitigation": "y"},
        {"bias_type": "Confirmation Bias", "description": "x", "mitigation": "y"},
    ]}
    assert ch._check_bias_minimum(hyp, required=4) == []


def test_all_six_biases_ok():
    """Полный набор Kempainen."""
    hyp = {"bias_risks": [
        {"bias_type": "Availability", "description": "x", "mitigation": "y"},
        {"bias_type": "Representative", "description": "x", "mitigation": "y"},
        {"bias_type": "Confirmation", "description": "x", "mitigation": "y"},
        {"bias_type": "Anchoring", "description": "x", "mitigation": "y"},
        {"bias_type": "Search Satisficing", "description": "x", "mitigation": "y"},
        {"bias_type": "Outcome", "description": "x", "mitigation": "y"},
    ]}
    assert ch._check_bias_minimum(hyp, required=4) == []


def test_bias_recognized_in_description():
    """Каноническое имя в description тоже считается (не только bias_type)."""
    hyp = {"bias_risks": [
        {"bias_type": "Cognitive trap A", "description": "availability эффект", "mitigation": "y"},
        {"bias_type": "Cognitive trap B", "description": "представительность", "mitigation": "y"},
        {"bias_type": "Cognitive trap C", "description": "confirmation", "mitigation": "y"},
        {"bias_type": "Cognitive trap D", "description": "anchoring", "mitigation": "y"},
    ]}
    assert ch._check_bias_minimum(hyp, required=4) == []
