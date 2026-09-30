"""
Ф1 context_cards — схема + позитивные контроли (посаженный дефект обязан упасть).

Зеркалит боевую миграцию _migrate_context_cards(); фикстура `db` строит схему из
tests/fixtures/health_schema.sql, сторож test_fixture_schema_covers_prod_tables
держит их синхронными на Studio.

RST: не «happy insert», а границы, которые счастливый прогон не покрывает —
UNIQUE(semantic_key,date) (идемпотентность catch-up) и CHECK-энумы lane/status/
recurrence_state (DB-level enforcement). Каждый — позитивный контроль: плохое
значение ОБЯЗАНО поднять IntegrityError, иначе гейт молча пропустит мусор.
"""
from __future__ import annotations

import sqlite3

import pytest

pytestmark = pytest.mark.unit

_EXPECTED_COLS = {
    "id", "date", "provider", "semantic_key", "lane", "origin", "delivery",
    "relevance", "importance", "severity", "status", "gate_reason",
    "evidence_summary", "allowed_claims", "forbidden_claims", "recurrence_state",
    "last_value", "last_shown_at", "escalation_level", "cooldown_until",
    "expires_at", "rendered_text_hash", "created_at",
}


def _insert(db, **over):
    row = {"date": "2026-07-13", "provider": "test", "semantic_key": "k1", "lane": "routine"}
    row.update(over)
    cols = ",".join(row.keys())
    ph = ",".join("?" * len(row))
    db.execute(f"INSERT INTO context_cards ({cols}) VALUES ({ph})", tuple(row.values()))


def test_table_exists(db):
    tabs = {r["name"] for r in db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "context_cards" in tabs


def test_columns_match_design(db):
    cols = {r["name"] for r in db.fetchall("PRAGMA table_info(context_cards)")}
    assert cols == _EXPECTED_COLS, f"drift: {_EXPECTED_COLS ^ cols}"


def test_unique_semantic_key_date_blocks_double(db):
    """Идемпотентность catch-up: (semantic_key,date) уникален → повторный прогон
    не плодит вторую строку того же смысла за день."""
    _insert(db)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(db)  # тот же (k1, 2026-07-13)


def test_same_key_next_day_ok(db):
    _insert(db, date="2026-07-13")
    _insert(db, date="2026-07-14")
    assert db.count("context_cards") == 2


@pytest.mark.parametrize("col,bad", [
    ("lane", "bogus"),
    ("status", "shown_maybe"),
    ("recurrence_state", "half_active"),
    ("origin", "rumor"),
    ("delivery", "vibes"),
])
def test_check_enums_reject_planted_garbage(db, col, bad):
    """Позитивный контроль: DB-level CHECK обязан отвергнуть мусорный enum.
    Если пройдёт — гейт молча примет невалидное состояние."""
    with pytest.raises(sqlite3.IntegrityError):
        _insert(db, **{col: bad})
