"""
Календарь-провайдер (второй этап): ближайшая поездка «за день до».

from_calendar: событие → карта с человекочитаемым «когда» + слаг места (кулдаун на место).
assemble_cards: берёт ТОЛЬКО ближайшую поездку (не вываливает весь календарь = не шум).
Слот plan — поездка не конкурирует с геномом/средой за их слоты.
"""
from __future__ import annotations

from datetime import date

import pytest

import brief_cards as bc
import brief_gate as bg
import brief_pipeline as bp

pytestmark = pytest.mark.unit


def test_from_calendar_when_and_slug():
    c = bc.from_calendar({"title": "Flight", "location": "Tromsø, NO",
                          "date_raw": "tomorrow - 15 Jul 2026 08:00", "is_travel": True})
    assert c.provider == "calendar"
    assert c.semantic_key.startswith("calendar:travel:")
    assert "завтра" in c.evidence_summary and "Tromsø" in c.evidence_summary
    assert c.severity >= bg.ROUTINE_SEVERITY_THETA          # проходит θ гейта


def test_calendar_has_own_plan_slot():
    c = bc.from_calendar({"title": "t", "location": "Berlin", "date_raw": "today", "is_travel": True})
    assert bg.category_of(c) == "plan"
    assert "plan" in bg.SLOT_BUDGET


def test_assemble_picks_only_nearest_travel(db, monkeypatch):
    """Два travel-события → в карты попадает ТОЛЬКО ближайшее (не весь календарь)."""
    import calendar_client as cal
    monkeypatch.setattr(cal, "get_travel_events", lambda *a, **k: [
        {"title": "Flight TOS", "location": "Tromsø, NO",
         "date_raw": "tomorrow - 15 Jul 2026 08:00", "is_travel": True},
        {"title": "Flight BER", "location": "Berlin, DE",
         "date_raw": "20 Jul 2026 09:00", "is_travel": True},   # далеко → не берём
    ])
    cards = bp.assemble_cards(date(2026, 7, 14))
    cal_cards = [c for c in cards if getattr(c, "provider", "") == "calendar"]
    assert len(cal_cards) == 1
    assert "Tromsø" in cal_cards[0].evidence_summary


def test_assemble_no_imminent_travel_no_card(db, monkeypatch):
    """Есть поездки, но не в ближайшие дни → карты нет (не тащим далёкое как новость)."""
    import calendar_client as cal
    monkeypatch.setattr(cal, "get_travel_events", lambda *a, **k: [
        {"title": "Flight", "location": "Tokyo", "date_raw": "20 Jul 2026", "is_travel": True},
    ])
    cards = bp.assemble_cards(date(2026, 7, 14))
    assert not any(getattr(c, "provider", "") == "calendar" for c in cards)
