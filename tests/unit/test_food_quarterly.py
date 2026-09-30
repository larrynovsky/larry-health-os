"""
Квартальная доставка пищевого профиля — гвард should_deliver (2026-07-14).

Раз в 3 месяца (1/4/7/10), первые 7 дней, идемпотентно по маркеру квартала.
"""
from __future__ import annotations

import datetime

import pytest

import food_quarterly as fq

pytestmark = pytest.mark.unit


def test_delivers_at_quarter_start(tmp_path, monkeypatch):
    m = tmp_path / "marker.txt"
    monkeypatch.setattr(fq, "_marker", lambda: m)
    assert fq.should_deliver(datetime.date(2026, 7, 1)) is True
    assert fq.should_deliver(datetime.date(2026, 10, 3)) is True


def test_not_after_first_week(tmp_path, monkeypatch):
    monkeypatch.setattr(fq, "_marker", lambda: tmp_path / "m.txt")
    assert fq.should_deliver(datetime.date(2026, 7, 8)) is False


def test_not_in_non_quarter_month(tmp_path, monkeypatch):
    monkeypatch.setattr(fq, "_marker", lambda: tmp_path / "m.txt")
    assert fq.should_deliver(datetime.date(2026, 8, 1)) is False


def test_dedup_within_quarter(tmp_path, monkeypatch):
    m = tmp_path / "marker.txt"
    monkeypatch.setattr(fq, "_marker", lambda: m)
    m.write_text(fq._quarter(datetime.date(2026, 7, 1)))
    assert fq.should_deliver(datetime.date(2026, 7, 3)) is False   # уже слали
    # новый квартал — снова да
    assert fq.should_deliver(datetime.date(2026, 10, 1)) is True


def test_maybe_deliver_never_raises(tmp_path, monkeypatch):
    # secrets/telegram недоступны → maybe_deliver ловит и возвращает False, не роняет daily job.
    monkeypatch.setattr(fq, "_marker", lambda: tmp_path / "m.txt")
    monkeypatch.setattr(fq, "should_deliver", lambda *a, **k: True)
    monkeypatch.setattr(fq, "deliver", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no telegram")))
    assert fq.maybe_deliver(datetime.date(2026, 7, 1)) is False
