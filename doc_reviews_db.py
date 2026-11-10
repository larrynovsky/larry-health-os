"""doc_reviews_db.py — доменный модуль doc_reviews. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import json
import os
import logging
from datetime import date, timedelta


log = logging.getLogger(__name__)


def save_pending_doc_review(source_file: str, proposed_type: str) -> int:
    """Записывает документ в очередь на подтверждение типа.
    Возвращает id новой записи."""
    with _hdb.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO pending_doc_reviews (source_file, proposed_type)
               VALUES (?, ?)""",
            (source_file, proposed_type),
        )
        return cur.lastrowid


def get_pending_doc_reviews() -> list:
    """Возвращает все записи со статусом needs_review."""
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            """SELECT id, source_file, proposed_type, imported_at
               FROM pending_doc_reviews
               WHERE status = 'needs_review'
               ORDER BY imported_at ASC"""
        ).fetchall()
    return [dict(r) for r in rows]


def mark_doc_review_sent(review_id: int) -> None:
    """Помечает запись как 'уведомление отправлено' (ждём ответа пользователя)."""
    with _hdb.get_conn() as conn:
        conn.execute(
            "UPDATE pending_doc_reviews SET status = 'sent' WHERE id = ?",
            (review_id,),
        )


def confirm_doc_review(review_id: int, confirmed_type: str) -> None:
    """Сохраняет подтверждённый тип, переводит статус в confirmed."""
    with _hdb.get_conn() as conn:
        conn.execute(
            """UPDATE pending_doc_reviews
               SET status = 'confirmed', confirmed_type = ?
               WHERE id = ?""",
            (confirmed_type, review_id),
        )


def reject_doc_review(review_id: int) -> None:
    """Помечает review как отклонённый (пользователь нажал Пропустить)."""
    with _hdb.get_conn() as conn:
        conn.execute(
            "UPDATE pending_doc_reviews SET status = 'rejected' WHERE id = ?",
            (review_id,),
        )


def auto_confirm_stale_reviews(hours: int = 48) -> int:
    """Автоматически подтверждает pending-записи старше N часов.
    Использует proposed_type как confirmed_type.
    Возвращает число обновлённых записей."""
    with _hdb.get_conn() as conn:
        cur = conn.execute(
            """UPDATE pending_doc_reviews
               SET status = 'auto_confirmed', confirmed_type = proposed_type
               WHERE status IN ('needs_review', 'sent')
                 AND imported_at < datetime('now', ? || ' hours')""",
            (f"-{hours}",),
        )
        return cur.rowcount


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
