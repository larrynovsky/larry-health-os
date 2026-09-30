"""dashboard_db — низкоуровневый DB слой для FastAPI dashboard.

Sprint 5 step 2 (2026-05-22): извлечено из dashboard.py.

Функции:
  _conn() — read-only connection (mode=ro URI)
  _q(sql, params) — read query
  _w(sql, params) — write query (PRAGMA busy_timeout=5000)
  _log_edit(...) — audit log в dashboard_edits
  _ensure_dashboard_edits_table() — idempotent миграция

Заметка: _w использует прямой sqlite3.connect (не health_db.get_conn),
потому что для эндпоинтов нужен busy_timeout 5000ms. Single-primary
guard теперь в dashboard._lifespan (Р-3 / F-111, commit 7da8d9e).
"""
from __future__ import annotations

import sqlite3

import health_db


def _conn() -> sqlite3.Connection:
    """Read-only connection через mode=ro URI."""
    c = sqlite3.connect(f"file:{health_db.DB_PATH}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _q(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Read query, возвращает list[Row]."""
    with _conn() as c:
        return c.execute(sql, params).fetchall()


def _has_table(name: str) -> bool:
    """Есть ли таблица. lab_results рождается при первом анализе, не в init_db:
    на свежей установке её нет, и это правда «анализов ещё нет», а не сбой.
    Спрашивается вопросом, а не except — иначе сбой другой природы стал бы «пусто»."""
    return bool(_q("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))


def _w(sql: str, params=()):
    """Write to health.db with busy_timeout=5000. Use ONLY for POST endpoints.

    Single-primary guard: dashboard._lifespan уже проверяет _is_primary()
    или ALLOW_WRITE_NONPRIMARY=1 при старте (Sprint 5 Р-3 / F-111). Здесь
    raw sqlite3.connect намеренно — get_conn дублировал бы guard (тогда
    test_dashboard_lifespan_guard с mocked _is_primary=False ломается даже
    при override). A++ R1/R2 guard поднимается через health_db._validate_db_path
    на module load.
    """
    c = sqlite3.connect(str(health_db.DB_PATH))
    c.execute("PRAGMA busy_timeout=5000")
    try:
        cur = c.execute(sql, params)
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def _log_edit(entity: str, entity_id, action: str,
              field: str | None = None,
              old_value: str | None = None,
              new_value: str | None = None,
              actor: str = "dashboard"):
    """Audit-log запись в dashboard_edits. Вызывать после основного UPDATE."""
    _w(
        """INSERT INTO dashboard_edits
           (entity, entity_id, action, field, old_value, new_value, actor)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (entity, str(entity_id), action, field, old_value, new_value, actor),
    )


def _ensure_dashboard_edits_table():
    """Idempotent миграция: создаёт dashboard_edits если нет.

    Вызывается из dashboard._lifespan при старте FastAPI. Raw sqlite3.connect
    намеренно — см. _w() для rationale (lifespan guard, test scenario).
    """
    c = sqlite3.connect(str(health_db.DB_PATH))
    c.execute("PRAGMA busy_timeout=5000")
    try:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS dashboard_edits (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         TEXT DEFAULT (datetime('now')),
                entity     TEXT NOT NULL,
                entity_id  TEXT NOT NULL,
                action     TEXT NOT NULL,
                field      TEXT,
                old_value  TEXT,
                new_value  TEXT,
                actor      TEXT DEFAULT 'dashboard'
            );
            CREATE INDEX IF NOT EXISTS idx_dashboard_edits_entity ON dashboard_edits(entity, entity_id);
            CREATE INDEX IF NOT EXISTS idx_dashboard_edits_ts     ON dashboard_edits(ts);
        """)
        c.commit()
    finally:
        c.close()
