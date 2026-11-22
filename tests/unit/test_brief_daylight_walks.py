"""Бриф 19.11: «прогулки 107 мин» рядом с «на свету 3 мин» и прогулка «56 км за 32 мин».

1. Время на свету не сходится с прогулками → в брифе сказано «не сходится», а не голое число,
   на котором модель строит совет «выйди на свет».
2. Дистанция с невозможной скоростью не пишется (писатель) и обнуляется в истории (миграция).
Числа — вымышленный день, повторяющий форму 18.11.
"""
from __future__ import annotations

from datetime import date

import health_db
import lifestyle_agents
import workouts_db

D = date(2031, 3, 4)


def _walk(start: str, end: str, km=None):
    workouts_db.upsert_workout(str(D), {"activity_type": "walking", "source": "Oura",
                                        "start_time": f"{D}T{start}:00+02:00",
                                        "end_time": f"{D}T{end}:00+02:00", "distance_km": km})


def _brief(daylight: float) -> str:
    day = {"hrv": {"avg": 30.0}, "resting_heart_rate": 55, "stress": {}, "resilience": {},
           "daylight_min": daylight}
    stats = {"avg_hrv": 30, "avg_rhr": 55, "n_days": 7}
    return lifestyle_agents.StressAgent().generate_brief(day, stats, stats, D)


def test_daylight_contradicting_walks_is_flagged(db):
    _walk("08:00", "08:40", 1.6)
    _walk("13:30", "13:45", 0.8)
    _walk("17:00", "17:32", 56.0)          # сбой трекера: 105 км/ч пешком
    out = _brief(3.0)
    assert "НЕ сходится с прогулками (87 мин)" in out
    assert "выводов и советов про дневной свет" in out


def test_daylight_consistent_with_walks_stays_plain(db):
    _walk("08:00", "08:40", 1.6)
    out = _brief(25.0)
    assert "Daylight: 25.0min" in out and "НЕ сходится" not in out


def test_impossible_distance_not_written(db):
    _walk("08:00", "08:40", 1.6)
    _walk("17:00", "17:32", 56.0)
    with health_db.get_conn() as c:
        rows = c.execute("SELECT start_time, distance_km FROM workouts ORDER BY start_time").fetchall()
    assert [r[1] for r in rows] == [1.6, None]


def test_history_repaired_by_migration(db):
    with health_db.get_conn() as c:
        c.execute("INSERT INTO workouts (date, activity_type, start_time, end_time, duration_min,"
                  " distance_km, source) VALUES (?,?,?,?,?,?,?)",
                  (str(D), "walking", f"{D}T17:00", f"{D}T17:32", 32.0, 56.0, "Oura"))
        c.execute("INSERT INTO workouts (date, activity_type, start_time, end_time, duration_min,"
                  " distance_km, source) VALUES (?,?,?,?,?,?,?)",
                  (str(D), "cycling", f"{D}T09:00", f"{D}T10:00", 60.0, 25.0, "Oura"))
    health_db._migrate_workouts_implausible_distance()
    with health_db.get_conn() as c:
        got = dict(c.execute("SELECT activity_type, distance_km FROM workouts").fetchall())
    assert got == {"walking": None, "cycling": 25.0}
