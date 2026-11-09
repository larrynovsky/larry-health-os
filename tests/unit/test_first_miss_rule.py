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


# ── признак «повтор брифа сработал в жизни» (нить brief-net-retry, 28.10) ──────────
# Решение владельца 28.10 (вариант Б): обещание transient_net_does_not_lose_brief поднимается
# по первому брифу, доставленному после настоящего сетевого сбоя. Признак пишет бот, просьбу
# поднять — этот читатель. Пара «признак есть — просит / нет — молчит» + holds молчит.

def _with_retry_proof(db_path, first_delivered):
    c = sqlite3.connect(db_path)
    v = {"last": {"date": first_delivered, "attempt": 1, "outcome": "delivered"},
         "first_delivered": first_delivered}
    c.execute("INSERT INTO system_config VALUES ('brief.net_retry', NULL, ?)", (json.dumps(v),))
    c.commit()
    c.close()


def _promise_status(monkeypatch, status):
    import intent_registry as ir
    reg = [{"id": "morning_brief", "invariants": [
        {"id": "transient_net_does_not_lose_brief", "status": status}]}]
    monkeypatch.setattr(ir, "load_registry", lambda: reg)


def test_retry_proof_asks_to_raise_the_promise(tmp_path, monkeypatch):
    db = _tenant(tmp_path, "2026-10-28")
    _with_retry_proof(db, "2026-10-29")
    _promise_status(monkeypatch, "open")
    warns = _brief_warns(monkeypatch, db, datetime(2026, 10, 29, 7, 50))
    assert any("повтор брифа доказан в жизни" in n for n in warns), warns


def test_no_retry_proof_is_silent(tmp_path, monkeypatch):
    db = _tenant(tmp_path, "2026-10-28")
    _promise_status(monkeypatch, "open")
    assert not any("повтор" in n for n in _brief_warns(monkeypatch, db, datetime(2026, 10, 29, 7, 50)))


def test_retry_proof_silent_once_promise_holds(tmp_path, monkeypatch):
    db = _tenant(tmp_path, "2026-10-28")
    _with_retry_proof(db, "2026-10-29")
    _promise_status(monkeypatch, "holds")
    assert not any("повтор" in n for n in _brief_warns(monkeypatch, db, datetime(2026, 10, 29, 7, 50)))


def test_promise_missing_from_registry_is_loud(tmp_path, monkeypatch):
    import intent_registry as ir
    db = _tenant(tmp_path, "2026-10-28")
    monkeypatch.setattr(ir, "load_registry", lambda: [{"id": "morning_brief", "invariants": []}])
    warns = _brief_warns(monkeypatch, db, datetime(2026, 10, 29, 7, 50))
    assert any("обещание повтора брифа не найдено" in n for n in warns), warns


def test_live_registry_carries_the_promise_with_owner_criterion():
    """Живой реестр: обещание есть и критерий — владельца (иначе подъём отобьёт гейт кодом 9)."""
    import intent_registry as ir
    rec = next(r for r in ir.load_registry() if r["id"] == "morning_brief")
    inv = next(i for i in rec["invariants"] if i["id"] == "transient_net_does_not_lose_brief")
    assert inv["status"] == "holds" or inv.get("closes_when_by") == "owner"


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
