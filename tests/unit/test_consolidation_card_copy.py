"""
tests/unit/test_consolidation_card_copy.py — L4: копия карточки человек-гейта под
регресс-тестом. Инвариант читабельности: без БД-жаргона, с названным действием и
Вымышленный конфликт локаций: карточка должна объяснять, какой источник
подтверждён, чтобы выбор не требовал угадывать смысл кнопок.
"""
from __future__ import annotations
import pytest

pytestmark = pytest.mark.unit

from memory_consolidation import consolidation_card_text


_JARGON = ["valid_to", "Ключ:", "Значение:", "Консолидация памяти", "SUPERSEDE", "canonical"]


def test_no_db_jargon():
    txt = consolidation_card_text({"current_key": "melatonin", "current_value": "3mg",
                                   "rationale": "дубль", "medical_flag": 1})
    for j in _JARGON:
        assert j not in txt, f"жаргон «{j}» просочился в карточку"


def test_names_action_and_reversibility():
    txt = consolidation_card_text({"current_key": "x", "current_value": "v", "rationale": "r"})
    assert "Убрать" in txt and "можно отменить" in txt, "действие и обратимость должны быть явны"


def test_shows_value_and_medical_prefix():
    txt = consolidation_card_text({"current_key": "k", "current_value": "мелатонин 5мг",
                                   "rationale": "r", "medical_flag": 1})
    assert "мелатонин 5мг" in txt
    assert txt.startswith("⚕️"), "медицинский флаг — префикс ⚕️"


def test_no_topic_line_when_key_absent():
    txt = consolidation_card_text({"current_key": None, "current_value": "env-строка",
                                   "rationale": "мусор"})
    assert "тема:" not in txt and "env-строка" in txt
