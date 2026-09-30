"""field_reviews_db.py — доменный модуль field_reviews. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import json
import os
import logging
from datetime import date, timedelta
from pathlib import Path


log = logging.getLogger(__name__)


def confirm_field_alias(format_id: int, raw_name: str,
                        canonical: str, source_example: str = "") -> None:
    """Подтверждает (или создаёт) alias. Проверяет orphan-статус."""
    # Orphan: canonical не известен ни канону имён, ни кэшу референсов бланков
    # (lab_refs с 2026-09-02 — мода бланков, у нового аналита её ещё нет → канон решает)
    orphan = 0
    try:
        import lab_canon
        refs = _hdb.get_lab_refs()
        known = canonical in refs or lab_canon.normalize(canonical) in getattr(lab_canon, "CANONICALS", ())
        orphan = 0 if known else 1
    except Exception:
        pass  # silent-ok: lab_refs недоступны → orphan=0 (conservative default)

    with _hdb.get_conn() as conn:
        conn.execute(
            """INSERT INTO lab_name_aliases
               (format_id, raw_name, canonical, confirmed, orphan, source_example, confirmed_at)
               VALUES (?,?,?,1,?,?,datetime('now'))
               ON CONFLICT(format_id, raw_name) DO UPDATE SET
                   canonical=excluded.canonical, confirmed=1,
                   orphan=excluded.orphan,
                   source_example=excluded.source_example,
                   confirmed_at=excluded.confirmed_at""",
            (format_id, raw_name, canonical, orphan, source_example or "")
        )


def queue_field_reviews(format_id: int | None,
                        source_file: str,
                        unknown_fields: list[dict]) -> int:
    """
    Ставит список неизвестных полей в очередь ревью.
    Пропускает поля которые уже pending или confirmed.
    Возвращает кол-во новых записей.
    """
    if not unknown_fields:
        return 0

    existing_raw = set()
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT raw_name FROM pending_field_reviews "
            "WHERE format_id IS ? AND status='pending'",
            (format_id,)
        ).fetchall()
        existing_raw = {r[0] for r in rows}

    if format_id is not None:
        confirmed_raw = set(_hdb.get_confirmed_aliases(format_id).keys())
    else:
        confirmed_raw = set()

    count = 0
    with _hdb.get_conn() as conn:
        for f in unknown_fields:
            raw = f.get("raw_name", "")
            if not raw or raw in existing_raw or raw in confirmed_raw:
                continue
            conn.execute(
                "INSERT INTO pending_field_reviews"
                "(format_id, raw_name, value, unit, source_file) "
                "VALUES (?,?,?,?,?)",
                (format_id, raw, f.get("value"), f.get("unit"), source_file)
            )
            count += 1

    return count


def get_pending_field_reviews(format_id: int | None = None) -> list[dict]:
    """Возвращает pending field reviews (опционально фильтр по format_id)."""
    with _hdb.get_conn() as conn:
        if format_id is not None:
            rows = conn.execute(
                "SELECT id, format_id, raw_name, value, unit, source_file, suggested, created_at "
                "FROM pending_field_reviews WHERE status='pending' AND format_id=? "
                "ORDER BY created_at",
                (format_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, format_id, raw_name, value, unit, source_file, suggested, created_at "
                "FROM pending_field_reviews WHERE status='pending' "
                "ORDER BY created_at"
            ).fetchall()
    return [dict(r) for r in rows]


def set_field_review_tg_message(review_id: int, tg_message_id: int) -> None:
    """Сохраняет Telegram message_id уведомления для reply-to-message ввода."""
    with _hdb.get_conn() as conn:
        conn.execute(
            "UPDATE pending_field_reviews SET tg_message_id=?, suggested='__sent__' WHERE id=?",
            (tg_message_id, review_id)
        )


def get_field_review_by_tg_message(tg_message_id: int) -> dict | None:
    """Находит pending field review по Telegram message_id уведомления."""
    with _hdb.get_conn() as conn:
        row = conn.execute(
            "SELECT id, format_id, raw_name, value, unit, source_file "
            "FROM pending_field_reviews "
            "WHERE tg_message_id=? AND status='pending'",
            (tg_message_id,)
        ).fetchone()
    return dict(row) if row else None


def resolve_field_review(review_id: int, canonical: str | None) -> None:
    """
    Подтверждает (canonical is not None) или отклоняет field review.
    При подтверждении — сохраняет alias.
    """
    with _hdb.get_conn() as conn:
        row = conn.execute(
            "SELECT format_id, raw_name, source_file FROM pending_field_reviews WHERE id=?",
            (review_id,)
        ).fetchone()
        if not row:
            return
        status = "confirmed" if canonical else "rejected"
        conn.execute(
            "UPDATE pending_field_reviews SET status=? WHERE id=?",
            (status, review_id)
        )
    if canonical and row:
        confirm_field_alias(row[0], row[1], canonical, row[2] or "")


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
