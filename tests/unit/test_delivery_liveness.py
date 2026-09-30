"""tests/unit/test_delivery_liveness.py — E1: liveness делверинг-джоба консолидации.

Хвост D1: если run_nightly_consolidation тихо умрёт — disagree/supersede не доедут.
Датчик по свежести heartbeat _consolidation_delivery_last_run. Три случая (RST):
fresh→тихо, missing→warn, stale(>48ч)→warn.
"""
from __future__ import annotations
import pytest

pytestmark = pytest.mark.unit


def _set_hb(hours_ago: float, raw=None):
    """Квитанция с объявленным ритмом (23.09): старт hours_ago часов назад, суточный джоб."""
    import json
    from datetime import timedelta
    import health_db
    import integrity_tests as it
    started = (it.get_utcnow() - timedelta(hours=hours_ago)).isoformat() + "+00:00"
    vj = raw if raw is not None else json.dumps({"every_s": 86400, "started_at": started})
    with health_db.get_conn() as c:
        c.execute(
            "INSERT INTO system_config (key, value_text, value_json, updated_at) "
            "VALUES ('_consolidation_delivery_last_run', 'ok', ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (vj,))


def test_silent_when_fresh(db):
    _set_hb(0)
    import integrity_tests as it
    it._warnings.clear()
    it.check_consolidation_delivery_liveness()
    assert not any("делверинг" in w[0] for w in it._warnings), \
        f"свежий heartbeat не должен варнить: {it._warnings}"


def test_warns_when_missing(db):
    import integrity_tests as it
    it._warnings.clear()
    it.check_consolidation_delivery_liveness()
    assert any("делверинг" in w[0] for w in it._warnings), "нет heartbeat → warn"


def test_warns_after_the_first_missed_run(db):
    """23.09 «общее правило»: суточный джоб, старт 25 ч назад — пропуск (до 23.09 молчал до 48 ч)."""
    _set_hb(25)
    import integrity_tests as it
    it._warnings.clear()
    it.check_consolidation_delivery_liveness()
    assert any("пропустил запуск" in w[0] for w in it._warnings), it._warnings


def test_warns_when_rhythm_unreadable(db):
    """A6 (23.09): квитанция без читаемого ритма — «не судимо», вслух. До 23.09 датчик
    на битой дате молча выходил, и она была неотличима от живого джоба."""
    _set_hb(0, raw='{"every_s": 86400, "started_at": "не-дата"}')
    import integrity_tests as it
    it._warnings.clear()
    it.check_consolidation_delivery_liveness()
    assert any("ритм не объявлен" in w[0] for w in it._warnings), it._warnings
