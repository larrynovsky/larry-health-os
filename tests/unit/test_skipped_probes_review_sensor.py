"""Датчик «исключённая проба: срок пересмотра прошёл» (18.10, решение владельца Б′).

Слой данных подменён (разбор носителя и «сегодня»); сам датчик — настоящий.
"""
from __future__ import annotations

import datetime as dt

import pytest

import integrity_tests as it

pytestmark = pytest.mark.unit


def _run(monkeypatch, skipped, today="2026-10-18"):
    import intent_registry
    monkeypatch.setattr(intent_registry, "carrier_lists", lambda root=None: (set(), skipped))
    monkeypatch.setattr(it, "today", dt.date.fromisoformat(today))
    said = []
    monkeypatch.setattr(it, "warn", lambda label, detail="": said.append((label, detail)))
    return it.check_skipped_probes_reviewed(), said


def _s(review, cause="причина пропуска достаточной длины"):
    return {"review_by": dt.date.fromisoformat(review) if review else None, "cause": cause}


def test_past_review_date_is_named(monkeypatch):
    r, said = _run(monkeypatch, {"plans/probe_x.py": _s("2026-10-01")})
    assert r["late"] == 1
    assert any("срок пересмотра прошёл" in l and "plans/probe_x.py (пересмотр 2026-10-01)" in d for l, d in said)


def test_future_review_date_is_quiet(monkeypatch):
    """Отрицательный контроль: дата впереди и дата сегодня — молчание (срок истекает ПОСЛЕ дня)."""
    r, said = _run(monkeypatch, {"plans/probe_x.py": _s("2027-01-16"), "plans/probe_y.py": _s("2026-10-18")})
    assert r == {"skipped": 2, "late": 0, "undated": 0} and not said


def test_undated_skip_is_named(monkeypatch):
    r, said = _run(monkeypatch, {"plans/probe_z.py": _s(None)})
    assert r["undated"] == 1 and any("без даты пересмотра" in l and "plans/probe_z.py" in d for l, d in said)
