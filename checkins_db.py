"""checkins_db.py — доменный модуль checkins. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import json
import os
import logging
from datetime import date, timedelta
from pathlib import Path


log = logging.getLogger(__name__)


def get_recent_checkins(n: int = 5) -> list[dict]:
    with _hdb.get_conn() as conn:
        rows = conn.execute("""
            SELECT date, question, answer, created_at
            FROM checkins
            ORDER BY created_at DESC
            LIMIT ?
        """, (n,)).fetchall()
    return [dict(r) for r in rows]


def save_checkin(day: str, question: str, answer: str, context: dict = None, time_of_day: str = "morning"):
    with _hdb.get_conn() as conn:
        conn.execute("""
            INSERT INTO checkins (date, time_of_day, question, answer, context)
            VALUES (?,?,?,?,?)
        """, (day, time_of_day, question, answer,
              json.dumps(context, ensure_ascii=False) if context else None))


def get_checkin_by_date(date_str: str, time_of_day: str = None) -> dict | None:
    """Возвращает чекин по дате. time_of_day: 'morning' | 'evening' | None (последний)."""
    with _hdb.get_conn() as conn:
        if time_of_day:
            row = conn.execute(
                "SELECT * FROM checkins WHERE date=? AND time_of_day=? "
                "ORDER BY created_at DESC LIMIT 1",
                (date_str, time_of_day)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM checkins WHERE date=? ORDER BY created_at DESC LIMIT 1",
                (date_str,)
            ).fetchone()
    return dict(row) if row else None


def update_checkin_scores(day: str, time_of_day: str,
                          stress_score: int | None = None,
                          mood_score: int | None = None,
                          energy_score: int | None = None) -> int:
    """UPDATE последнего чекина за day+time_of_day с тремя scores.
    Возвращает число обновлённых строк (обычно 1, иначе 0).

    Используется checkin_agent.finalize_checkin после save_checkin.
    """
    sets = []
    args: list = []
    if stress_score is not None:
        sets.append("stress_score = ?"); args.append(stress_score)
    if mood_score is not None:
        sets.append("mood_score = ?");   args.append(mood_score)
    if energy_score is not None:
        sets.append("energy_score = ?"); args.append(energy_score)
    if not sets:
        return 0
    args.extend([day, time_of_day])
    with _hdb.get_conn() as conn:
        cur = conn.execute(
            f"UPDATE checkins SET {', '.join(sets)} "
            "WHERE id = (SELECT id FROM checkins WHERE date=? AND time_of_day=? "
            "          ORDER BY created_at DESC LIMIT 1)",
            args,
        )
        return cur.rowcount


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
