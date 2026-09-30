"""memory_db.py — доменный модуль memory. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import logging


log = logging.getLogger(__name__)


def get_memory(category: str = None, n: int = 20, active_only: bool = True) -> list[dict]:
    _hdb._ensure_memory_table()
    with _hdb.get_conn() as conn:
        if category:
            rows = conn.execute("""
                SELECT * FROM memory WHERE category=?
                  AND (active=1 OR active=?)
                ORDER BY updated_at DESC LIMIT ?
            """, (category, 0 if not active_only else 1, n)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM memory WHERE (active=1 OR active=?)
                ORDER BY updated_at DESC LIMIT ?
            """, (0 if not active_only else 1, n)).fetchall()
    return [dict(r) for r in rows]


def save_memory(category: str, value: str, key: str = None,
                confidence: float = 0.8, source: str = "conversation"):
    """
    Сохраняет факт в память. Если key задан — обновляет существующую запись.
    Категории: profile_update | observation | pattern | experiment | preference | fact
    """
    _hdb._ensure_memory_table()
    with _hdb.get_conn() as conn:
        if key:
            existing = conn.execute(
                "SELECT id FROM memory WHERE category=? AND key=? AND active=1",
                (category, key)
            ).fetchone()
            if existing:
                conn.execute("""
                    UPDATE memory SET value=?, confidence=?, updated_at=datetime('now')
                    WHERE id=?
                """, (value, confidence, existing["id"]))
                return existing["id"]
        cur = conn.execute("""
            INSERT INTO memory (category, key, value, confidence, source)
            VALUES (?,?,?,?,?)
        """, (category, key, value, confidence, source))
        return cur.lastrowid


# Таблица patterns оставлена архивом (BL-PATTERNS-FROZEN-1).
# Устаревшие записи не должны постоянно попадать в контекст чатов и отчётов.
# Поиск закономерностей выполняет monthly_consilium.
# Таблица оставлена архивом; писателя и читателей в коде нет (сторож:
# tests/unit/test_patterns_retired.py).


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
