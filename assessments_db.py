"""assessments_db.py — доменный модуль assessments. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import json
import os
import logging
from datetime import date, timedelta
from pathlib import Path


log = logging.getLogger(__name__)


def save_assessment_session(
    instrument_id: str,
    wording_version_hash: str,
    chat_id: int | None = None,
    task_id: int | None = None,
    answers_json: str = "{}",
) -> int:
    """SX-1.8: create new assessment session row. Возвращает id."""
    with _hdb.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO assessment_sessions "
            "(instrument_id, wording_version_hash, chat_id, task_id, answers_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (instrument_id, wording_version_hash, chat_id, task_id, answers_json),
        )
        return cur.lastrowid


def get_assessment_session(session_id: int) -> dict | None:
    """SX-1.8: загрузить одну сессию по id."""
    with _hdb.get_conn() as conn:
        row = conn.execute(
            "SELECT id, instrument_id, wording_version_hash, chat_id, task_id, "
            "started_at, completed_at, answers_json, status "
            "FROM assessment_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    return dict(row) if row else None


def get_active_assessment_session(chat_id: int, instrument_id: str | None = None) -> dict | None:
    """SX-1.8: найти активную (in_progress) сессию для chat_id, опционально по инструменту."""
    with _hdb.get_conn() as conn:
        if instrument_id:
            row = conn.execute(
                "SELECT id, instrument_id, wording_version_hash, chat_id, task_id, "
                "started_at, completed_at, answers_json, status "
                "FROM assessment_sessions "
                "WHERE chat_id=? AND instrument_id=? AND status='in_progress' "
                "ORDER BY id DESC LIMIT 1",
                (chat_id, instrument_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, instrument_id, wording_version_hash, chat_id, task_id, "
                "started_at, completed_at, answers_json, status "
                "FROM assessment_sessions "
                "WHERE chat_id=? AND status='in_progress' "
                "ORDER BY id DESC LIMIT 1",
                (chat_id,),
            ).fetchone()
    return dict(row) if row else None


def update_assessment_session(session_id: int, answers_json: str | None = None,
                              status: str | None = None,
                              completed_at: str | None = None) -> None:
    """SX-1.8: update progress / status of assessment session."""
    sets = []
    args = []
    if answers_json is not None:
        sets.append("answers_json = ?")
        args.append(answers_json)
    if status is not None:
        sets.append("status = ?")
        args.append(status)
    if completed_at is not None:
        sets.append("completed_at = ?")
        args.append(completed_at)
    if not sets:
        return
    args.append(session_id)
    with _hdb.get_conn() as conn:
        conn.execute(
            f"UPDATE assessment_sessions SET {', '.join(sets)} WHERE id = ?",
            args,
        )


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
