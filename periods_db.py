"""periods_db.py — доменный модуль periods. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import json
import os
import logging
from datetime import date, timedelta
from pathlib import Path

from _time_inject import get_today

log = logging.getLogger(__name__)


def historical_periods(
    exclude_types: list[str] | None = None,
    include_deleted: bool = False,
) -> list[dict]:
    """All periods (past + current + future), default excludes soft-deleted.

    exclude_types: список type'ов для фильтрации (например ['travel', 'baseline']
        для gp_agent._build_clinical_history). None = вернуть всё.
    include_deleted: True = включить soft-deleted (для audit/UI listing).

    Filter hybrid: `deleted_at IS NULL AND active=1` для backward-compat с
    legacy raw SQL который ещё устанавливает active=0 (см. current_periods).
    """
    sql = "SELECT * FROM periods WHERE 1=1"
    params: list = []
    if not include_deleted:
        sql += " AND deleted_at IS NULL AND active = 1"
    if exclude_types:
        placeholders = ",".join("?" * len(exclude_types))
        sql += f" AND type NOT IN ({placeholders})"
        params.extend(exclude_types)
    sql += " ORDER BY start_date"
    with _hdb.get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def current_periods(date_str: str | None = None) -> list[dict]:
    """Periods, активные на дату (default: today). Excludes soft-deleted.

    Current = start_date <= date AND (end_date IS NULL OR end_date >= date).

    Filter: `deleted_at IS NULL AND active=1`. Hybrid D++ (2026-06-19):
    `active` deprecated, но **пока не DROP'нут** — backward-compat с raw INSERT
    кодом который set'ит active=0 без deleted_at (legacy + tests). Invariant
    через soft_delete_period: новая работа всегда поддерживает обе колонки.
    После DROP active (#209) — filter упростится до `deleted_at IS NULL`.
    """
    if date_str is None:
        date_str = str(get_today())
    with _hdb.get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM periods
            WHERE start_date <= ?
              AND (end_date IS NULL OR end_date >= ?)
              AND deleted_at IS NULL
              AND active = 1
            ORDER BY start_date
        """, (date_str, date_str)).fetchall()
    return [dict(r) for r in rows]


def get_active_period(date_str: str | None = None) -> list[dict]:
    """DEPRECATED (2026-06-19): use current_periods().

    Backward-compat alias для legacy callers (gp_agent:637 и др).
    Удалён будет в Phase 6 (DROP active column followup).
    """
    return current_periods(date_str)


def save_period(name: str, type_: str, start_date: str,
                end_date: str = None, source: str = 'manual',
                notes: str = None, tags: list = None) -> int:
    """Создаёт новый период (active=1, deleted_at=NULL по дефолту)."""
    import json as _j
    with _hdb.get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO periods (name, type, start_date, end_date, source, notes, tags)
            VALUES (?,?,?,?,?,?,?)
        """, (name, type_, start_date, end_date, source, notes,
              _j.dumps(tags or [])))
        return cur.lastrowid


def future_periods() -> list[dict]:
    """Periods starting in the future. Excludes soft-deleted.

    Filter hybrid: `deleted_at IS NULL AND active=1` для backward-compat
    (см. current_periods).
    """
    today = str(get_today())
    with _hdb.get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM periods
            WHERE start_date > ?
              AND deleted_at IS NULL
              AND active = 1
            ORDER BY start_date
        """, (today,)).fetchall()
    return [dict(r) for r in rows]


def soft_delete_period(period_id: int, reason: str | None = None) -> None:
    """Mark period as soft-deleted (sets deleted_at=now()).

    reason — опциональная аннотация в notes (audit trail).
    """
    with _hdb.get_conn() as conn:
        if reason:
            conn.execute(
                "UPDATE periods SET deleted_at=datetime('now'), "
                "notes=COALESCE(notes,'') || ?, active=0 WHERE id=?",
                (f"\n[soft-deleted {get_today()}: {reason}]", period_id),
            )
        else:
            conn.execute(
                "UPDATE periods SET deleted_at=datetime('now'), active=0 WHERE id=?",
                (period_id,),
            )


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
