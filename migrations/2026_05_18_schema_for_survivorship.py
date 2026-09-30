#!/usr/bin/env python3.11
"""Миграция схемы для survivorship-extension (фаза 1.8).

Идемпотентная:
  1. ALTER TABLE alerts ADD COLUMN source — если ещё нет.
  2. CREATE TABLE assessment_sessions — если ещё нет.
  3. Индексы на assessment_sessions.

Запуск: python3.11 migrations/2026_05_18_schema_for_survivorship.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import health_db as db


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def main() -> int:
    db.init_db()
    changes = 0
    with db.get_conn() as conn:
        # 1. alerts.source
        if not _column_exists(conn, "alerts", "source"):
            conn.execute("ALTER TABLE alerts ADD COLUMN source TEXT")
            print("  + alerts.source added")
            changes += 1
        else:
            print("  = alerts.source already exists")

        # 2. assessment_sessions
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assessment_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instrument_id TEXT NOT NULL,
                wording_version_hash TEXT NOT NULL,
                chat_id INTEGER,
                task_id INTEGER,
                started_at TEXT NOT NULL DEFAULT (datetime('now')),
                completed_at TEXT,
                answers_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'in_progress',
                FOREIGN KEY (task_id) REFERENCES tasks(id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_assess_sess_status "
            "ON assessment_sessions(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_assess_sess_chat "
            "ON assessment_sessions(chat_id)"
        )
        # Проверим что создалась
        tables = [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='assessment_sessions'"
        ).fetchall()]
        if tables:
            print("  + assessment_sessions exists")
            changes += 1
        else:
            print("  ! assessment_sessions FAILED to create")
            return -1

    print(f"\nDone. Changes: {changes}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
