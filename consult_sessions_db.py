"""consult_sessions_db.py — доменный модуль consult_sessions. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import json
import os
import logging
from datetime import date, timedelta
from pathlib import Path


log = logging.getLogger(__name__)


def save_consultation_session(chat_id: int, session_dict: dict) -> None:
    """Сохраняет (или обновляет) in-progress /consult сессию в БД.
    session_dict — результат ConsultationSession.to_dict()."""
    import json as _json
    session_json = _json.dumps(session_dict, ensure_ascii=False)
    with _hdb.get_conn() as conn:
        conn.execute(
            """INSERT INTO consultation_sessions (chat_id, session_json, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(chat_id) DO UPDATE SET
                   session_json = excluded.session_json,
                   updated_at   = excluded.updated_at""",
            (chat_id, session_json),
        )


def load_consultation_session(chat_id: int) -> dict | None:
    """Загружает сохранённую сессию. Возвращает dict или None если нет."""
    import json as _json
    with _hdb.get_conn() as conn:
        row = conn.execute(
            "SELECT session_json FROM consultation_sessions WHERE chat_id=?",
            (chat_id,),
        ).fetchone()
    if row:
        return _json.loads(row[0])
    return None


def delete_consultation_session(chat_id: int) -> None:
    """Удаляет персистентную сессию (вызывается при /end или таймауте)."""
    with _hdb.get_conn() as conn:
        conn.execute(
            "DELETE FROM consultation_sessions WHERE chat_id=?", (chat_id,)
        )


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
