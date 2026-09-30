"""«Общее правило» датчиков живости (решение владельца 2026-09-23): тревога после ПЕРВОГО
пропущенного запуска, без своего допуска у каждого датчика.

Здесь — датчики, у которых нет отдельного дома тестов: гейт брифа (время брифа из
system_config тенанта), reschedule (ритм в квитанции), ротация логов (StartInterval плиста)
и сама выводимость интервала из плиста. У каждого — пара «пропуск краснеет / без пропуска
тихо», иначе датчик, который кричит всегда, тоже «ловит».
"""
from __future__ import annotations

import json
import plistlib
import sqlite3
from datetime import date, datetime, timedelta

import pytest

pytestmark = pytest.mark.unit


# ── гейт брифа ────────────────────────────────────────────────────────────────

def _tenant(tmp_path, last_card: str, brief=(8, 30), tz="Etc/GMT-3"):
    p = tmp_path / "tenant_x" / "data" / "health.db"
    p.parent.mkdir(parents=True)
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE context_cards (date TEXT)")
    c.execute("CREATE TABLE system_config (key TEXT PRIMARY KEY, value_text TEXT, value_json TEXT)")
    c.execute("INSERT INTO context_cards VALUES (?)", (last_card,))
    c.execute("INSERT INTO system_config VALUES ('schedule.morning_brief', NULL, ?)",
              (json.dumps({"hour": brief[0], "minute": brief[1]}),))
    c.execute("INSERT INTO system_config VALUES ('schedule.active_tz', ?, NULL)", (tz,))
    c.commit()
    c.close()
    return p


def _brief_warns(monkeypatch, db_path, now):
    import integrity_tests as it
    monkeypatch.setattr(it, "_tenant_db_paths", lambda include_current=True: [db_path])
    monkeypatch.setattr(it, "get_now", lambda *a, **k: now)
    monkeypatch.setattr(it, "_brief_provider_failures_warn", lambda: None)
    cap = []
    monkeypatch.setattr(it, "warn", lambda n, d="": cap.append(n))
    it.check_morning_brief_gate_liveness()
    return cap


def test_brief_gate_silent_before_todays_brief(tmp_path, monkeypatch):
    """07:50, бриф в 08:30: последний плановый бриф — вчера. Вчерашняя запись — норма."""
    db = _tenant(tmp_path, "2026-09-22")
    assert _brief_warns(monkeypatch, db, datetime(2026, 9, 23, 7, 50)) == []


def test_brief_gate_loud_after_one_missed_brief(tmp_path, monkeypatch):
    """Та же запись в 07:50 следующего дня — один бриф пропущен. До 23.09 молчало три дня."""
    db = _tenant(tmp_path, "2026-09-22")
    assert any("гейт замолк" in n for n in _brief_warns(monkeypatch, db, datetime(2026, 9, 24, 7, 50)))


def test_brief_time_is_read_from_the_tenant_not_assumed(tmp_path, monkeypatch):
    """Бриф тенанта в 07:00 → в 07:50 последний бриф — СЕГОДНЯ, вчерашняя запись = пропуск."""
    db = _tenant(tmp_path, "2026-09-22", brief=(7, 0))
    assert any("гейт замолк" in n for n in _brief_warns(monkeypatch, db, datetime(2026, 9, 23, 7, 50)))


def test_brief_gate_unjudgeable_is_loud(tmp_path, monkeypatch):
    db = _tenant(tmp_path, "2026-09-22", tz="")
    assert any("не судима" in n for n in _brief_warns(monkeypatch, db, datetime(2026, 9, 23, 7, 50)))


# ── reschedule: ритм в квитанции ──────────────────────────────────────────────

def _resched_warns(monkeypatch, hours_ago, every_s=43200):
    import integrity_tests as it
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE system_config (key TEXT PRIMARY KEY, updated_at TEXT, value_json TEXT)")
    started = (it.get_utcnow() - timedelta(hours=hours_ago)).isoformat() + "+00:00"
    c.execute("INSERT INTO system_config VALUES ('schedule.reschedule_last_run', 'x', ?)",
              (json.dumps({"every_s": every_s, "started_at": started}),))
    monkeypatch.setattr(it, "_iter_tenant_ro", lambda: iter([("health", c, True)]))
    cap = []
    monkeypatch.setattr(it, "warn", lambda n, d="": cap.append(n))
    it.check_reschedule_liveness()
    return cap


def test_reschedule_silent_inside_its_interval(monkeypatch):
    assert _resched_warns(monkeypatch, 11) == []


def test_reschedule_loud_after_one_missed_run(monkeypatch):
    """12-часовой джоб, старт 13 ч назад — пропуск. До 23.09 молчало до 26 ч."""
    assert any("пропустил запуск" in n for n in _resched_warns(monkeypatch, 13))


# ── ротация логов: StartInterval плиста ───────────────────────────────────────

def test_start_interval_is_read_from_the_plist(tmp_path):
    import plist_env_liveness as pl
    (tmp_path / "x.plist").write_bytes(plistlib.dumps({"Label": "x", "StartInterval": 1800}))
    (tmp_path / "y.plist").write_bytes(plistlib.dumps({"Label": "y",
                                                       "StartCalendarInterval": {"Hour": 3, "Minute": 0}}))
    assert pl.start_interval_s("x", tmp_path) == 1800
    assert pl.start_interval_s("y", tmp_path) is None     # календарь — не интервал
    assert pl.start_interval_s("нет", tmp_path) is None


def _rotate(monkeypatch, age_h):
    import integrity_tests as it
    import log_rotate
    monkeypatch.setattr(log_rotate, "receipt_status",
                        lambda *a, **k: {"exists": True, "age_h": age_h, "selftest_ok": True})
    cap = []
    monkeypatch.setattr(it, "warn", lambda n, d="": cap.append(n))
    return it, cap


def test_logrotate_one_missed_run_warns(monkeypatch):
    """Интервал 30 мин (плист), квитанция 40 мин назад — пропущен запуск. До 23.09 — только с 2 ч."""
    it, cap = _rotate(monkeypatch, 40 / 60)
    it.check_logrotate_liveness()
    assert "квитанция ротации несвежая" in cap


def test_logrotate_inside_interval_is_silent(monkeypatch):
    it, cap = _rotate(monkeypatch, 20 / 60)
    it.check_logrotate_liveness()
    assert cap == []
