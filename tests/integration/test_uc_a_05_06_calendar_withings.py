"""
UC-A-05 (Withings BP) + UC-A-06 (Calendar/KAYAK trips) — smoke tests.

Источник: USE_CASES.md §3.A.
Status: A-05 `intended`, A-06 снят 06.11.2026 (нить macbook-kayak-oura): модуль calendar_sync удалён,
остаётся проверка словарей calendar_client — их читает бриф.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


# ── UC-A-06: Calendar sync ──────────────────────────────────────────────────


def test_calendar_client_keywords_distinct():
    """`CHECKIN_KEYWORDS ≠ FLIGHT_KEYWORDS` (ложный путь C-29: «Check in to Hotel» — не перелёт)."""
    import calendar_client as cc
    flight = getattr(cc, "FLIGHT_KEYWORDS", set())
    checkin = getattr(cc, "CHECKIN_KEYWORDS", set())
    if flight and checkin:
        # Множества не должны совпадать
        intersection = set(flight) & set(checkin)
        # Допустимо очень малое пересечение, но не полное равенство
        assert set(flight) != set(checkin), \
            "FLIGHT_KEYWORDS и CHECKIN_KEYWORDS — одинаковы (UC-A-06 invariant)"


# ── UC-A-05: Withings ──────────────────────────────────────────────────────


def test_withings_intended_skipped():
    """UC-A-05 intended — Withings BP integration не реализована полностью."""
    pytest.skip("UC-A-05 intended — Withings sync ещё не реализован")
