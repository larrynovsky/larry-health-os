"""workouts_db.py — доменный модуль workouts. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import json
import os
import logging
from datetime import date, timedelta
from pathlib import Path


log = logging.getLogger(__name__)


# Скорость выше потолка — сбой трекера, а не тренировка (19.11: Oura отдал прогулку «56 км за 32 мин»,
# бриф вынес число в текст; у владельца 10 таких прогулок >15 км с июля). Пешком быстрее 10 км/ч
# не ходят; 60 км/ч — потолок для остального (велосипед). Сырое значение остаётся в raw.
MAX_KMH = {"walking": 10.0, "hiking": 10.0, "outdoor walk": 10.0}   # «Outdoor Walk» — часы
WALK_TYPES = tuple(MAX_KMH)
MAX_KMH_OTHER = 60.0


def _minutes(start: str | None, end: str | None) -> float | None:
    from datetime import datetime
    try:
        return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60
    except (TypeError, ValueError):
        return None


def _plausible_distance(activity_type: str | None, duration_min: float | None,
                       distance_km: float | None) -> float | None:
    """Дистанция, если скорость правдоподобна для вида тренировки; иначе None (нет данных, а не 0)."""
    if not distance_km or not duration_min or duration_min <= 0:
        return distance_km
    cap = MAX_KMH.get((activity_type or "").lower(), MAX_KMH_OTHER)
    if distance_km / (duration_min / 60) <= cap:
        return distance_km
    log.warning("workout: дистанция %.2f км за %.0f мин (%s) — сбой трекера, не пишу",
                distance_km, duration_min, activity_type)
    return None


def upsert_workout(date_str: str, workout: dict):
    """Записывает тренировку. Дедуплицирует по дате + start_time.

    Длительность без явного значения считается из start/end (11.11): Oura v2 не отдаёт
    поле `duration`, и все 3388 строк владельца с 2020 года лежали с NULL — бриф писал «0м».
    julianday переводит обе метки в UTC, поэтому переход на зимнее время не сдвигает разницу.
    Тот же расчёт — в health_db._migrate_workouts_duration для истории."""
    with _hdb.get_conn() as conn:
        conn.execute("""
            INSERT INTO workouts
                (date, activity_type, start_time, end_time, duration_min,
                 distance_km, calories, avg_hr, max_hr, source, raw)
            VALUES (?,?,?,?,COALESCE(?, ROUND((julianday(?) - julianday(?)) * 1440, 1)),
                    ?,?,?,?,?,?)
            ON CONFLICT DO NOTHING
        """, (
            date_str,
            workout.get("activity_type"),
            workout.get("start_time"),
            workout.get("end_time"),
            workout.get("duration_min"),
            workout.get("end_time"),
            workout.get("start_time"),
            _plausible_distance(workout.get("activity_type"),
                               workout.get("duration_min")
                               or _minutes(workout.get("start_time"), workout.get("end_time")),
                               workout.get("distance_km")),
            workout.get("calories"),
            workout.get("avg_hr"),
            workout.get("max_hr"),
            workout.get("source", "Oura"),
            json.dumps(workout, ensure_ascii=False),
        ))


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
