"""constitutions_db.py — доменный модуль constitutions. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import json
import os
import logging
from datetime import date, timedelta
from pathlib import Path


log = logging.getLogger(__name__)


def upsert_constitution(domain: str, body_md: str, title: str | None = None,
                        source_version: str | None = None) -> None:
    """Записывает/обновляет нарратив конституции по домену (sleep|nutrition|...)."""
    with _hdb.get_conn() as conn:
        conn.execute(
            """INSERT INTO constitutions (domain, title, body_md, generated_at, source_version)
               VALUES (?, ?, ?, datetime('now'), ?)
               ON CONFLICT(domain) DO UPDATE SET
                   title          = excluded.title,
                   body_md        = excluded.body_md,
                   generated_at   = excluded.generated_at,
                   source_version = excluded.source_version""",
            (domain, title, body_md, source_version),
        )


def get_constitution(domain: str) -> dict | None:
    """Возвращает {domain, title, body_md, generated_at, source_version} или None."""
    with _hdb.get_conn() as conn:
        row = conn.execute(
            "SELECT domain, title, body_md, generated_at, source_version "
            "FROM constitutions WHERE domain=?", (domain,)
        ).fetchone()
    return dict(row) if row else None


def list_constitutions() -> list[dict]:
    """Список конституций без body_md: domain, title, generated_at, size_bytes."""
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT domain, title, generated_at, LENGTH(body_md) AS size_bytes "
            "FROM constitutions ORDER BY domain"
        ).fetchall()
    return [dict(r) for r in rows]


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
