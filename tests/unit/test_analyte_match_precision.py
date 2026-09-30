"""Совпадение начала слова не выдаётся за имя аналита.

Найдено испытанием на живой модели 14.09, вторым заходом: синоним «ПОЛ»
(перекисное окисление липидов) с хвостом \\w* матчил слово «полностью», и фраза
«Free T3, Free T4 полностью не сдавались» обвиняла Lipid_peroxidation. Первый заход
в тот же день чинил родственный класс (стем «витамин » с висящим пробелом) — один
предмет, две формы.

Правило: у форм длиной ≤4 символов (аббревиатуры) хвоста нет, у длинных — есть,
потому что падежи и множественное живут только у слов.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_short_abbreviation_does_not_eat_a_longer_word(db, clock):
    """«полностью» — не «ПОЛ». Мутация: вернуть хвост коротким формам → краснеет."""
    clock.set("2026-09-14")
    db.add_lab_result("2021-06-14", "Lipid_peroxidation", 3.2, unit="мкмоль/л")
    import gp_context as gc
    hits, note = gc.judge_absence_claims(
        "тироидная панель (free t3, free t4 полностью) не сдавалась")
    assert "Lipid_peroxidation" not in note


def test_short_abbreviation_still_matches_itself(db, clock):
    """Позитивный контроль: сама аббревиатура ловится — фильтр не глушитель."""
    clock.set("2026-09-14")
    db.add_lab_result("2021-06-14", "Lipid_peroxidation", 3.2, unit="мкмоль/л")
    import gp_context as gc
    hits, note = gc.judge_absence_claims("ПОЛ не сдавался ни разу")
    assert "Lipid_peroxidation" in note


def test_long_form_keeps_its_cases(db, clock):
    """Позитивный контроль в другую сторону: падежи длинных имён по-прежнему ловятся."""
    clock.set("2026-09-14")
    db.add_lab_result("2026-06-01", "Folate", 8.0, unit="нг/мл")
    import gp_context as gc
    hits, note = gc.judge_absence_claims("фолаты не сдавались ни разу")
    assert "Folate" in note
