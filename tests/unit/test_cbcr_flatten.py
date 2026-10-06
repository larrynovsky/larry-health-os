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


# Замер 05.10: манифест форму line_of_reasoning не задаёт, модели пишут её по-разному.
@pytest.mark.parametrize("lor, want", [
    ([{"step": 1, "action": "Сдать ферритин и ОАК"}, "Колоноскопия"], "Сдать ферритин и ОАК"),     # sonnet-5-5
    (["Сдать ферритин и ОАК", "Колоноскопия"], "Сдать ферритин и ОАК"),
    ({"next_step_immediate": "ЭКГ и эхо", "specialist_referral": "кардиолог"}, "ЭКГ и эхо"),       # sonnet-4-6
    ({"specialist_referral": "кардиолог"}, "кардиолог"),
    ("Повторить анализ через месяц", "Повторить анализ через месяц"),
])
def test_flatten_reads_every_reasoning_shape(lor, want):
    assert ch.flatten_cbcr_payload(_base_payload(line_of_reasoning=lor))["test"] == want


@pytest.mark.parametrize("module", ["survivorship_curator", "literature_curator"])
def test_curator_lost_hypothesis_is_a_fault(monkeypatch, module):
    """До 05.10 падение CBCR у куратора было строкой в логе: наблюдение терялось без сигнала."""
    import importlib
    import notify
    import hypothesis_semantic_check as semcheck
    mod = importlib.import_module(module)
    monkeypatch.setattr(semcheck, "check", lambda obs: (False, None, ""))
    monkeypatch.setattr(ch, "generate_hypothesis_with_critique", lambda obs: (_ for _ in ()).throw(AttributeError("x")))
    faults = []
    monkeypatch.setattr(notify, "fault", lambda tech, person_key=None, **kw: faults.append((tech, person_key)))
    fn = getattr(mod, "_execute_hypothesis")
    import inspect
    args = [{"type": "t"}, {"summary": "наблюдение"}] if len(inspect.signature(fn).parameters) == 2 else None
    assert args, f"{module}._execute_hypothesis: сигнатура изменилась — поправь тест"
    assert fn(*args) is None
    assert len(faults) == 1 and faults[0][1] is None and module in faults[0][0]
