"""#138: tests на расширенный _check_bias_minimum (scan по всем полям)."""
from __future__ import annotations
from pathlib import Path
import sys

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest
import cbcr_hypothesis as ch

pytestmark = pytest.mark.unit


def test_bias_key_variant_bias_not_bias_type():
    """LLM иногда использует 'bias' вместо 'bias_type' (smoke 2026-05-13)."""
    hyp = {"bias_risks": [
        {"bias": "Availability Bias", "description": "x", "mitigation": "y"},
        {"bias": "Representative Bias", "description": "x", "mitigation": "y"},
        {"bias": "Anchoring Bias", "description": "x", "mitigation": "y"},
        {"bias": "Confirmation Bias", "description": "x", "mitigation": "y"},
    ]}
    assert ch._check_bias_minimum(hyp, required=4) == []


def test_bias_key_variant_name():
    """Вариант: 'name' вместо bias_type."""
    hyp = {"bias_risks": [
        {"name": "availability", "rationale": "x", "mitigation": "y"},
        {"name": "representative", "rationale": "x", "mitigation": "y"},
        {"name": "anchoring", "rationale": "x", "mitigation": "y"},
        {"name": "satisficing", "rationale": "x", "mitigation": "y"},
    ]}
    assert ch._check_bias_minimum(hyp, required=4) == []


def test_bias_russian_keywords():
    """Русские формулировки тоже распознаются."""
    hyp = {"bias_risks": [
        {"bias": "Эвристика доступности", "description": "x", "mitigation": "y"},
        {"bias": "Якорная зависимость", "description": "x", "mitigation": "y"},
        {"bias": "Биас представительности через сходство с прототипом", "mitigation": "y"},
        {"bias": "Поиск удовлетворяющего объяснения — search satisficing", "mitigation": "y"},
    ]}
    assert ch._check_bias_minimum(hyp, required=4) == []


def test_real_smoke_payload_now_passes():
    """Регрессия #138: реальный smoke payload теперь распознаётся."""
    hyp = {"bias_risks": [
        {"bias": "Availability Bias",
         "description": "Экзамплиплатиновая нейропатия — часто обсуждаемая тема",
         "mitigation": "Альтернативы добавлены явно"},
        {"bias": "Anchoring Bias",
         "description": "Триггер закрепил drift как primary",
         "mitigation": "decision_threshold"},
        {"bias": "Confirmation Bias",
         "description": "Поиск только подтверждающих данных",
         "mitigation": "evidence_against непустое"},
        {"bias": "Representative Bias",
         "description": "Сходство с прототипом chemo-induced",
         "mitigation": "Prevalence-weighted"},
        {"bias": "Search Satisficing",
         "description": "Первая гипотеза принята",
         "mitigation": "≥3 альтернатив"},
        {"bias": "Outcome Bias",
         "description": "Оценка по исходу",
         "mitigation": "Оценка по логике reasoning"},
    ]}
    assert ch._check_bias_minimum(hyp, required=4) == []


def test_still_catches_fake_biases():
    """Регрессия: 3 биaса с фиктивными именами всё ещё flagged."""
    hyp = {"bias_risks": [
        {"bias": "Magic Thinking", "description": "x", "mitigation": "y"},
        {"bias": "Lunar Phase Bias", "description": "x", "mitigation": "y"},
        {"bias": "Astrology Effect", "description": "x", "mitigation": "y"},
    ]}
    issues = ch._check_bias_minimum(hyp, required=4)
    assert len(issues) == 1


def test_old_bias_type_field_still_works():
    """Регрессия: старая schema с bias_type всё ещё работает."""
    hyp = {"bias_risks": [
        {"bias_type": "Availability Bias", "description": "x", "mitigation": "y"},
        {"bias_type": "Anchoring", "description": "x", "mitigation": "y"},
        {"bias_type": "Confirmation", "description": "x", "mitigation": "y"},
        {"bias_type": "Outcome", "description": "x", "mitigation": "y"},
    ]}
    assert ch._check_bias_minimum(hyp, required=4) == []
