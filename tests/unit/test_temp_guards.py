"""Нить temp-guards (решения владельца 12.11): порог температуры живёт в таблице порогов,
два сторожа тихой пропажи — «сон есть, градусов нет» и «автоматизация тренировок HAE молчит».

Что краснеет, если вернуть старое:
- уровень перцентиля снова литерал в коде → test_level_comes_from_table (95 и 97.5 дают разный ответ);
- параметров нет в таблице, а сигнал молчит без звука → test_missing_params_are_loud;
- сторож температуры не считает серии → test_runs_*; не подключён → test_check_temperature_warns;
- сторож тренировок путает «не тренировался» с поломкой или один запуск телефона с двумя →
  test_automation_*; не подключён → test_check_workouts_warns.
Данные выдуманы.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from tests.unit.test_activity_temp import seed_temp_params

pytestmark = pytest.mark.unit
UTC = timezone.utc


def _history(db, target, values):
    for i, v in enumerate(values, start=1):
        db.add_daily_metrics(str(target - timedelta(days=i)), temp_deviation=v, sleep_total=7.0)


def test_level_comes_from_table(db):
    """Ночь между p95 и p97.5 истории: при 95 в таблице сигнал есть, при 97.5 — нет."""
    import safety_net as sn
    target = date(2040, 3, 11)
    _history(db, target, [round(0.003 * i, 3) for i in range(200)])   # 0.000 … 0.597
    db.add_daily_metrics(str(target), temp_deviation=0.575)           # p95≈0.567, p97.5≈0.582
    seed_temp_params(db, pct=95)
    assert sn._temperature_alert(target)
    seed_temp_params(db, pct=97.5)
    assert sn._temperature_alert(target) is None


def test_missing_params_are_loud(db, monkeypatch):
    import safety_net as sn
    seen = []
    monkeypatch.setattr(sn, "_emit_fallback_alert", lambda scope, err: seen.append(scope))
    db.execute("UPDATE absolute_thresholds SET active=0 WHERE metric='temp_deviation'")
    db.add_daily_metrics("2040-03-11", temp_deviation=0.9)
    assert sn._temperature_alert(date(2040, 3, 11)) is None
    assert seen == ["temperature"]


@pytest.mark.parametrize("nights,expected", [
    ([True, False, True, False, False, True], (0, 2)),
    ([True, False, False, True, False, False, False], (3, 2)),
    ([], (0, 0)),
    ([False], (1, 0)),
])
def test_runs_current_and_longest(nights, expected):
    import metrics_db
    assert metrics_db.temp_missing_runs(nights) == expected


def test_check_temperature_warns(db, monkeypatch):
    import integrity_tests as I
    got = []
    monkeypatch.setattr(I, "warn", lambda label, detail="": got.append(label))
    monkeypatch.setattr(I, "today", date(2040, 3, 20))
    for i in range(1, 20):
        db.add_daily_metrics(str(date(2040, 3, 20) - timedelta(days=i)), sleep_total=7.0,
                             temp_deviation=None if i in (1, 2, 3, 10) else 0.1)
    assert I.check_temperature_arrives() == (3, 1)
    assert any("температура ночью не приходит" in g for g in got)


def _t(h, d=12):
    return datetime(2040, 11, d, h, 0, tzinfo=UTC)


def test_automation_not_configured():
    import import_apple_health as ia
    assert ia.workouts_automation_state([], [_t(3)], [_t(1)])["state"] == "not_configured"


def test_automation_ok_when_copy_arrived():
    import import_apple_health as ia
    st = ia.workouts_automation_state([_t(1, 11), _t(5)], [_t(6), _t(9)], [_t(2)])
    assert st["state"] == "ok"


def test_automation_silent_after_two_sibling_runs():
    import import_apple_health as ia
    st = ia.workouts_automation_state([_t(1, 11)], [_t(5), _t(9)], [_t(2)])
    assert st["state"] == "silent" and st["metric_runs_after"] == 2


def test_one_phone_run_is_not_two():
    """Две выгрузки метрик в одном часу — один запуск телефона: тренировки могут прийти следом."""
    import import_apple_health as ia
    m = [_t(5).replace(minute=2), _t(5).replace(minute=9)]
    assert ia.workouts_automation_state([_t(1, 11)], m, [_t(2)])["state"] == "ok"


def test_no_workout_no_alarm():
    """Нет тренировок Oura — ждать копий нечего: не тренировался ≠ поломка."""
    import import_apple_health as ia
    assert ia.workouts_automation_state([_t(1, 11)], [_t(5), _t(9)], [])["state"] == "ok"


def test_rest_dir_matches_ingest(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    import importlib
    import import_apple_health as ia
    from dashboard_routers import api_hae_ingest
    api = importlib.reload(api_hae_ingest)
    try:
        assert ia.hae_rest_dir() == api._REST_DIR
    finally:
        monkeypatch.undo()
        importlib.reload(api_hae_ingest)


def test_check_workouts_warns(db, monkeypatch, tmp_path):
    import integrity_tests as I
    import import_apple_health as ia
    rest = tmp_path / "data" / "hae_rest"
    rest.mkdir(parents=True)

    def pkt(ts: datetime, kind: str):
        body = {"data": {"workouts": [{"name": "Walk"}]} if kind == "W" else {"data": {"metrics": []}}}
        (rest / f"HealthAutoExport-REST-{ts:%Y%m%dT%H%M%S}.json").write_text(json.dumps(body))
    pkt(_t(1, 11), "W")
    pkt(_t(5), "M")
    pkt(_t(9), "M")
    monkeypatch.setattr(ia, "hae_rest_dir", lambda: rest)
    monkeypatch.setattr(I, "today", date(2040, 11, 12))
    db.execute("INSERT INTO workouts (date, activity_type, start_time, end_time, duration_min, source) "
               "VALUES ('2040-11-12','walking','2040-11-12T01:30:00+00:00','2040-11-12T02:00:00+00:00',30,'Oura')")
    got = []
    monkeypatch.setattr(I, "warn", lambda label, detail="": got.append(label))
    assert I.check_hae_workouts_automation() == "silent"
    assert got == ["автоматизация тренировок Health Auto Export молчит"]
