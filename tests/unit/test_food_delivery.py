"""
Гарантия доставки «мягких» карт (еда/тропа/море) в бриф (2026-07-14, v2).

LLM стабильно роняет эти блоки даже при жёсткой инструкции. _ensure_shown_extras
детерминированно добавляет ЕСТЕСТВЕННЫЙ абзац («сейчас»), если гейт показал карту,
а её нет в тексте; и НЕ дублирует, когда LLM всё же вплёл.
"""
from __future__ import annotations

import pytest

import gp_agent
import brief_cards as bc

pytestmark = pytest.mark.unit


def _food():
    return bc.Card(provider="food", semantic_key="food:seasonal:legumes:boby",
                   lane="routine", severity=0.4,
                   evidence_summary="в сезоне и полезно: бобы — клетчатка/белок, фолат")


def _trail():
    return bc.Card(provider="trail", semantic_key="movement:trail:esja",
                   lane="routine", severity=0.4,
                   evidence_summary="выходной — тропа: Esja Ridge Loop Trail (~7 км)")


def _marine():
    return bc.Card(provider="env", semantic_key="sea:temp:swimmable", lane="routine",
                   severity=0.4, evidence_summary="море ~14.2°C — хороший день поплавать")


def test_food_appended_naturally_when_dropped():
    c = _food()
    out = gp_agent._ensure_shown_extras("Ночь короткая.\n\nUV высокий.", [c], {c.semantic_key})
    assert "бобы" in out.lower()
    assert "сейчас" in out.lower() and "сегодня" not in out.lower()


def test_trail_appended_when_dropped():
    c = _trail()
    out = gp_agent._ensure_shown_extras("Ночь короткая.", [c], {c.semantic_key})
    assert "Esja" in out and "тропа" in out.lower()


def test_marine_appended_when_dropped():
    c = _marine()
    out = gp_agent._ensure_shown_extras("Ночь короткая.", [c], {c.semantic_key})
    assert "оре" in out  # «Море …»


def test_not_duplicated_when_present():
    f, t, m = _food(), _trail(), _marine()
    report = "Взял бобы. Схожу на Esja. Море тёплое."
    out = gp_agent._ensure_shown_extras(
        report, [f, t, m], {f.semantic_key, t.semantic_key, m.semantic_key})
    assert out == report  # всё уже упомянуто → без добавок


def test_nothing_shown_no_change():
    out = gp_agent._ensure_shown_extras("Просто бриф.", [], set())
    assert out == "Просто бриф."


def test_trail_not_duplicated_when_llm_transliterates():
    """Вымышленный маршрут уже назван транслитерацией: повторять карточку не нужно."""
    c = bc.Card(provider="trail", semantic_key="movement:trail:maple_loop_trail",
                lane="routine", severity=0.4,
                evidence_summary="выходной — тропа: Maple Loop Trail — ~12.7 км от дома")
    report = "Мэйпл Луп Трейл — 13 км от дома: ранний выход меняет качество прогулки."
    assert gp_agent._ensure_shown_extras(report, [c], {c.semantic_key}) == report


def test_data_header_names_weekday_and_night():
    """2026-09-06 (воскресенье): модель из ISO-даты вывела «ночь с воскресенья на понедельник».
    Заголовок обязан отдавать день недели и ночь готовыми."""
    from datetime import date
    h = gp_agent._data_header(date(2026, 9, 6), date(2026, 9, 5))
    assert "воскресенье" in h and "с субботы на воскресенье" in h and "(суббота)" in h
    assert "понедельник" not in h
