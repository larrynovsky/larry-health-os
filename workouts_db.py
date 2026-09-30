"""workouts_db.py — доменный модуль workouts. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import json
import os
import logging
from datetime import date, timedelta
from pathlib import Path


log = logging.getLogger(__name__)


def upsert_workout(date_str: str, workout: dict):
    """Записывает тренировку. Дедуплицирует по дате + start_time."""
    with _hdb.get_conn() as conn:
        conn.execute("""
            INSERT INTO workouts
                (date, activity_type, start_time, end_time, duration_min,
                 distance_km, calories, avg_hr, max_hr, source, raw)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT DO NOTHING
        """, (
            date_str,
            workout.get("activity_type"),
            workout.get("start_time"),
            workout.get("end_time"),
            workout.get("duration_min"),
            workout.get("distance_km"),
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
