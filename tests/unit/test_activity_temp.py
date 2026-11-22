"""Нить activity-temp (11.11): бриф не видел фитнес с часов и ночную температуру.

Что стережёт каждый тест — и что краснеет, если вернуть старый код:
- длительность тренировки: писатель и миграция считают её из start/end (Oura v2 не отдаёт
  `duration`; до 11.11 NULL в 3388 из 3388 строк) — вернуть `workout.get("duration_min")`
  без COALESCE → красный test_writer_*;
- градусы температуры: parse_readiness их сохраняет — убрать ключ → красный;
- тренировки часов: разбор data.workouts, копии Oura отсекаются по источнику;
  приём /hae/ingest пишет их в workouts — убрать вызов → красный test_ingest_*;
- одиночная ночь выше личного верхнего перцентиля — карточка safety (решение владельца);
- агент движения называет тренировку рядом с «мало шагов».
Устройство в фикстурах выдумано; данные людей сюда не попадают.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.unit

# Выдуманный пакет HAE в формате живого (снят 11.11): копия Oura + тренировка часов.
HAE_PAYLOAD = {"data": {"workouts": [
    {"name": "Other", "start": "2040-03-10 19:20:00 +0200", "end": "2040-03-10 19:32:00 +0200",
     "duration": 720, "source": {"name": "Oura", "identifier": "com.ouraring.oura"},
     "activeEnergyBurned": {"qty": 188.5, "units": "kJ"}},
    {"name": "Core Training", "id": "TEST-1",
     "start": "2040-03-10 09:32:52 +0200", "end": "2040-03-10 10:09:28 +0200",
     "duration": 2196.05, "source": {"name": "Test Watch", "identifier": "com.apple.health.TEST"},
     "activeEnergyBurned": {"qty": 563.11, "units": "kJ"},
     "avgHeartRate": {"qty": 103.3, "units": "bpm"}, "maxHeartRate": {"qty": 135, "units": "bpm"},
     "heartRateData": [{"qty": 100}] * 3},
]}}


def test_writer_computes_duration_across_dst_change(db):
    """Переход на зимнее время внутри тренировки: 02:30+03:00 → 03:30+02:00 = 120 мин, не 60."""
    import workouts_db
    workouts_db.upsert_workout("2040-10-28", {
        "activity_type": "walking", "start_time": "2040-10-28T02:30:00.000+03:00",
        "end_time": "2040-10-28T03:30:00.000+02:00", "source": "Oura"})
    row = db.fetchone("SELECT duration_min FROM workouts WHERE date='2040-10-28'")
    assert row["duration_min"] == 120.0


def test_writer_keeps_explicit_duration(db):
    import workouts_db
    workouts_db.upsert_workout("2040-03-10", {
        "activity_type": "x", "start_time": "2040-03-10T09:00:00+02:00",
        "end_time": "2040-03-10T10:00:00+02:00", "duration_min": 42.0})
    assert db.fetchone("SELECT duration_min FROM workouts")["duration_min"] == 42.0


def test_migration_fills_history_only_where_null(db):
    import health_db
    db.execute("INSERT INTO workouts (date, start_time, end_time, duration_min) VALUES "
               "('2040-01-01','2040-01-01T08:00:00.000+02:00','2040-01-01T08:45:00.000+02:00',NULL),"
               "('2040-01-02','2040-01-02T08:00:00.000+02:00','2040-01-02T08:45:00.000+02:00',7)")
    health_db._migrate_workouts_duration()
    got = {r["date"]: r["duration_min"] for r in db.fetchall("SELECT date, duration_min FROM workouts")}
    assert got == {"2040-01-01": 45.0, "2040-01-02": 7.0}


def test_parse_readiness_keeps_degrees():
    from import_oura import parse_readiness
    out = parse_readiness([{"day": "2040-03-11", "score": 72, "temperature_deviation": 0.5,
                            "temperature_trend_deviation": 0.11,
                            "contributors": {"body_temperature": 64}}])["2040-03-11"]
    assert out["temperature_deviation"] == 0.5
    assert out["temperature_trend_deviation"] == 0.11
    assert out["body_temperature"] == 64


def test_degrees_reach_daily_metrics(db):
    import health_db
    health_db.upsert_metrics_from_json("2040-03-11", {"readiness_score": 72,
                                       "temperature_deviation": 0.5,
                                       "temperature_trend_deviation": 0.11}, source="Oura")
    row = db.fetchone("SELECT temp_deviation, temp_trend_deviation FROM daily_metrics")
    assert (row["temp_deviation"], row["temp_trend_deviation"]) == (0.5, 0.11)


def test_parse_hae_workouts_skips_oura_copies_and_converts_units():
    from import_apple_health import parse_hae_workouts
    out = parse_hae_workouts(HAE_PAYLOAD)
    assert [w["activity_type"] for w in out] == ["Core Training"]
    w = out[0]
    assert w["date"] == "2040-03-10" and w["source"] == "AppleHealth"
    assert w["duration_min"] == 36.6
    assert w["calories"] == round(563.11 / 4.184, 1)
    assert w["avg_hr"] == 103.3 and w["max_hr"] == 135
    assert "Test Watch" not in json.dumps(w, ensure_ascii=False)   # имя устройства не храним


def test_ingest_writes_watch_workout_once(db, tmp_path, monkeypatch):
    """Повторная доставка того же пакета не двоит строку (UNIQUE date+start_time)."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    (tmp_path / "hae_ingest_token").write_text("t-ok\n")
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path))
    from dashboard_routers import api_hae_ingest
    monkeypatch.setattr(api_hae_ingest, "_REST_DIR", tmp_path / "rest")
    from ingest_lan import lan_ingest_app
    body = json.dumps(HAE_PAYLOAD).encode()
    with TestClient(lan_ingest_app()) as c:
        for _ in range(2):
            r = c.post("/hae/ingest", content=body, headers={"Authorization": "Bearer t-ok"})
            assert r.status_code == 200, r.text
    rows = db.fetchall("SELECT activity_type, source, duration_min FROM workouts")
    assert [tuple(r) for r in rows] == [("Core Training", "AppleHealth", 36.6)]


def seed_temp_params(db, pct: float = 97.5, window: int = 365, min_nights: int = 60) -> None:
    """Параметры сигнала температуры живут в absolute_thresholds (нить temp-guards, 12.11)."""
    for variant, val in (("temp_pct", pct), ("temp_window_days", window), ("temp_min_nights", min_nights)):
        db.execute("INSERT OR REPLACE INTO absolute_thresholds (metric, direction, value, reason_template, "
                   "source, kind, baseline, band_label, variant, norm_kind) VALUES "
                   "('temp_deviation','ceiling',?, 'test','owner_personal_test','percentile','x','',?,'personal')",
                   (val, variant))


def _nights(db, target: date, values: list[float]) -> None:
    seed_temp_params(db)
    for i, v in enumerate(values, start=1):
        db.add_daily_metrics(str(target - timedelta(days=i)), temp_deviation=v)


def test_single_night_above_personal_ceiling_alerts(db):
    import safety_net as sn
    target = date(2040, 3, 11)
    _nights(db, target, [round(-0.3 + 0.006 * (i % 100), 3) for i in range(200)])  # ≈ −0.3…+0.29
    db.add_daily_metrics(str(target), temp_deviation=0.5)
    a = sn._temperature_alert(target)
    assert a and a["level"] == sn.WARN and a["direction"] == "high"
    db.execute("UPDATE daily_metrics SET temp_deviation=0.1 WHERE date=?", (str(target),))
    assert sn._temperature_alert(target) is None


def test_cold_nights_count_as_history(db):
    """Ночи ниже нуля — тоже история: перцентиль, отбросивший их как «нулевые», остался
    бы без базы у человека с прохладной нормой и промолчал бы о жаре."""
    import safety_net as sn
    target = date(2040, 3, 11)
    _nights(db, target, [-0.1] * 195 + [0.05] * 5)
    db.add_daily_metrics(str(target), temp_deviation=0.3)
    assert sn._temperature_alert(target)


def test_no_judgement_without_history(db):
    import safety_net as sn
    target = date(2040, 3, 11)
    _nights(db, target, [0.0] * 10)
    db.add_daily_metrics(str(target), temp_deviation=0.9)
    assert sn._temperature_alert(target) is None


def test_movement_brief_names_workout_next_to_low_steps(db, monkeypatch):
    import lifestyle_agents as la
    monkeypatch.setattr(la.db, "get_threshold", lambda *a, **k: 7000 if k.get("variant") else 0.5)
    db.execute("INSERT INTO workouts (date, activity_type, start_time, end_time, duration_min, source) "
               "VALUES ('2040-03-10','Core Training','2040-03-10T09:32:52+02:00',"
               "'2040-03-10T10:09:28+02:00',36.6,'AppleHealth')")
    text = la.MovementAgent().generate_brief({"steps": 4677}, {"avg_steps": 9000}, {}, date(2040, 3, 10))
    assert "Тренировки: Core Training 37м" in text
    assert "⚠" not in text and "шаги силовую не мерят" in text
