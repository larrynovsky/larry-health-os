"""W5A-D4: tests для compute_structural_confidence mitigation-aware штрафа."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest
import cbcr_hypothesis as ch

pytestmark = pytest.mark.unit


def _full_strong_hyp(biases):
    """Базовая гипотеза с максимум баллов кроме bias-секции."""
    return {
        "evidence_for": [
            {"source": "oura", "fact": "x", "weight": "strong"},
            {"source": "labs", "fact": "y", "weight": "moderate"},
            {"source": "labs", "fact": "z", "weight": "moderate"},
        ],
        "evidence_against": [{"source": "oura", "fact": "counter", "weight": "moderate"}],
        "alternative_explanations": [
            {"diagnosis": "A", "rule_out_by": "test A"},
            {"diagnosis": "B", "rule_out_by": "test B"},
        ],
        "falsification": {
            "etiological_confirmation": "ok",
            "etiological_refutation": "ok",
            "therapeutic_response": "ok",
            "decision_threshold": "ok",
        },
        "bias_risks": biases,
        "illness_script": {"fault": {"description": "mechanism described"}},
    }


def test_three_biases_all_with_mitigation_no_penalty():
    """3 биasа все с непустым mitigation → без штрафа D4."""
    biases = [
        {"bias_type": "Availability", "description": "x", "mitigation": "альтернативы"},
        {"bias_type": "Anchoring", "description": "x", "mitigation": "decision_threshold"},
        {"bias_type": "Confirmation", "description": "x", "mitigation": "evidence_against"},
    ]
    sc = ch.compute_structural_confidence(_full_strong_hyp(biases))
    # +2 ev_for + +1 against + +1 alts + +1 falsif + +1 biases_analysed = 6
    assert sc["score"] == 6, sc["breakdown"]
    assert sc["confidence"] == "high"


def test_two_biases_no_mitigation_penalty():
    """2 биasа без mitigation → штраф −2."""
    biases = [
        {"bias_type": "Availability", "description": "x"},  # mitigation отсутствует
        {"bias_type": "Anchoring", "description": "x", "mitigation": ""},  # пустой
        {"bias_type": "Confirmation", "description": "x", "mitigation": "ok"},
    ]
    sc = ch.compute_structural_confidence(_full_strong_hyp(biases))
    # 6 − 2 = 4
    assert sc["score"] == 4, sc["breakdown"]
    assert sc["confidence"] == "medium"


def test_one_bias_no_mitigation_no_penalty():
    """1 биaс без mitigation — в пределах, штрафа нет (порог 2)."""
    biases = [
        {"bias_type": "Availability", "description": "x", "mitigation": "ok"},
        {"bias_type": "Anchoring", "description": "x", "mitigation": "ok"},
        {"bias_type": "Confirmation", "description": "x"},  # без mitigation
    ]
    sc = ch.compute_structural_confidence(_full_strong_hyp(biases))
    assert sc["score"] == 6, sc["breakdown"]


def test_old_inversion_not_active():
    """Регрессия старой логики: 3 биaса с mitigation НЕ должны давать −2."""
    biases = [
        {"bias_type": "A", "description": "x", "mitigation": "m1"},
        {"bias_type": "B", "description": "x", "mitigation": "m2"},
        {"bias_type": "C", "description": "x", "mitigation": "m3"},
        {"bias_type": "D", "description": "x", "mitigation": "m4"},
    ]
    sc = ch.compute_structural_confidence(_full_strong_hyp(biases))
    # До D4: 6 − 2 = 4 (medium). После D4: 6 (high).
    assert sc["score"] == 6
    assert sc["confidence"] == "high"
    # Старого ключа bias_risks_count_penalty быть не должно
    assert "bias_risks_count_penalty" not in sc["breakdown"]
    assert "bias_no_mitigation_penalty" in sc["breakdown"]


def test_empty_bias_risks_no_penalty_but_no_bonus():
    """Без биasов — нет +1, нет штрафа."""
    sc = ch.compute_structural_confidence(_full_strong_hyp([]))
    # +2 +1 +1 +1 +0 = 5
    assert sc["score"] == 5
