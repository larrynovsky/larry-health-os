"""Тесты для flatten_cbcr_payload — mapping CBCR-структуры в плоские поля гипотезы."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest
import cbcr_hypothesis as ch

pytestmark = pytest.mark.unit


def _base_payload(**kwargs):
    """Минимальный валидный CBCR-payload."""
    base = {
        "one_line_statement": "Тестовое наблюдение",
        "illness_script": {"fault": {"description": "Тестовый механизм"}},
        "falsification": {"etiological_confirmation": "Предсказание X через 14 дней"},
        "line_of_reasoning": {},
        "resolution_type": "self_managed",
    }
    base.update(kwargs)
    return base


def test_flatten_test_from_immediate_next_steps():
    """Нормальный путь: test берётся из immediate_next_steps[0]."""
    payload = _base_payload(line_of_reasoning={
        "immediate_next_steps": ["Сдать анализ A", "Обратиться к врачу B"],
        "specialist_referral_trigger": "триггер",
    })
    flat = ch.flatten_cbcr_payload(payload)
    assert flat["test"] == "Сдать анализ A"


def test_flatten_test_from_specialist_referral_trigger():
    """Fallback 1: immediate_next_steps пустой -> specialist_referral_trigger."""
    payload = _base_payload(line_of_reasoning={
        "immediate_next_steps": [],
        "specialist_referral_trigger": "Обратиться к гастроэнтерологу",
    })
    flat = ch.flatten_cbcr_payload(payload)
    assert flat["test"] == "Обратиться к гастроэнтерологу"


def test_flatten_test_fallback_to_prediction():
    """Fallback 2 (баг 647/648): оба источника пусты -> test <- prediction."""
    payload = _base_payload(line_of_reasoning={})
    flat = ch.flatten_cbcr_payload(payload)
    assert flat["test"] == flat["prediction"]
    assert flat["test"] != ""


def test_flatten_test_empty_when_all_sources_empty():
    """Если prediction тоже пустой — test остаётся пустым (патологический кейс)."""
    payload = _base_payload(
        line_of_reasoning={},
        falsification={},
    )
    flat = ch.flatten_cbcr_payload(payload)
    assert flat["test"] == ""
    assert flat["prediction"] == ""


def test_flatten_resolution_type_preserved():
    """resolution_type из payload сохраняется как есть."""
    payload = _base_payload(resolution_type="needs_specialist")
    flat = ch.flatten_cbcr_payload(payload)
    assert flat["resolution_type"] == "needs_specialist"
