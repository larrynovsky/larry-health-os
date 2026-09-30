"""PERIODS-SEMANTICS Sprint 2 (2026-06-19): unit tests для periods helpers.

D++ hybrid: переход от `active` к `deleted_at` для soft-delete; current/past/future
определяется через даты, не через flag. Эти tests покрывают новые helpers
в health_db.

Backward-compat: get_active_period (deprecated alias на current_periods) тоже
должен работать.
"""
import json
from datetime import date, timedelta

import pytest


@pytest.fixture
def db_with_periods(tmp_path, monkeypatch):
    """In-memory DB с минимальной periods table + sample data."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    (tmp_path / "health" / "data").mkdir(parents=True)

    # Подмена DB_PATH на tmp БЕЗ swap sys.modules / reload: иначе health_db
    # подменяется НОВЫМ объектом модуля, а уже импортировавшие старый
    # (treatment_*, survivorship) держат v1 → split-brain двух health_db и потеря
    # изоляции в полном прогоне unit-сьюта (bisect 2026-06-22, фикс изоляции).
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")

    today = date.today()
    yesterday = today - timedelta(days=1)
    tomorrow = today + timedelta(days=1)
    last_week = today - timedelta(days=7)
    next_week = today + timedelta(days=7)
    long_ago = today - timedelta(days=365)

    # Initialize schema
    with health_db.get_conn() as c:
        c.execute("""
            CREATE TABLE periods (
                id INTEGER PRIMARY KEY,
                name TEXT, type TEXT,
                start_date TEXT, end_date TEXT,
                tags TEXT DEFAULT '[]', source TEXT DEFAULT 'manual',
                notes TEXT, active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                deleted_at TEXT
            )
        """)
        # Mix: current, past, future, soft-deleted
        c.execute(
            "INSERT INTO periods (id, name, type, start_date, end_date, active, deleted_at) VALUES "
            "(1, 'Current treatment', 'treatment', ?, NULL, 1, NULL), "          # ongoing (current)
            "(2, 'Past observation', 'watchful_waiting', ?, ?, 1, NULL), "       # past, valid
            "(3, 'Future travel', 'travel', ?, ?, 1, NULL), "                    # future, valid
            "(4, 'Soft-deleted', 'treatment', ?, ?, 0, ?), "                     # soft-deleted
            "(5, 'Current travel', 'travel', ?, ?, 1, NULL), "                   # current (overlap with today)
            "(6, 'Baseline', 'baseline', ?, ?, 1, NULL)",                        # past baseline
            (
                str(last_week),  # 1
                str(long_ago), str(yesterday),  # 2
                str(tomorrow), str(next_week),  # 3
                str(long_ago), str(yesterday), "2026-01-01 00:00:00",  # 4 (soft-deleted)
                str(yesterday), str(tomorrow),  # 5
                str(long_ago), str(yesterday),  # 6
            ),
        )

    return health_db, today


class TestCurrentPeriods:
    def test_default_today(self, db_with_periods):
        db, today = db_with_periods
        rows = db.current_periods()
        names = sorted(r["name"] for r in rows)
        # Current: id=1 (ongoing) + id=5 (current travel)
        # Not current: id=2 (past), id=3 (future), id=4 (soft-deleted), id=6 (past)
        assert names == ["Current travel", "Current treatment"]

    def test_specific_date_includes_past_phase(self, db_with_periods):
        db, today = db_with_periods
        past_date = str(today - timedelta(days=3))  # yesterday's past period was active
        rows = db.current_periods(past_date)
        names = sorted(r["name"] for r in rows)
        # id=1 (start=last_week, ongoing) — active 3 дня назад
        # id=2 (past observation) — end was yesterday, was active 3 дня назад
        # id=5 (current travel) — start was yesterday, NOT active 3 дня назад
        # id=6 (baseline) — end was yesterday, was active 3 дня назад
        assert "Current treatment" in names
        assert "Past observation" in names
        assert "Current travel" not in names

    def test_excludes_soft_deleted(self, db_with_periods):
        db, today = db_with_periods
        rows = db.current_periods()
        ids = [r["id"] for r in rows]
        assert 4 not in ids  # soft-deleted excluded


class TestHistoricalPeriods:
    def test_default_excludes_deleted(self, db_with_periods):
        db, today = db_with_periods
        rows = db.historical_periods()
        ids = sorted(r["id"] for r in rows)
        # All except soft-deleted (id=4)
        assert ids == [1, 2, 3, 5, 6]

    def test_include_deleted(self, db_with_periods):
        db, today = db_with_periods
        rows = db.historical_periods(include_deleted=True)
        ids = sorted(r["id"] for r in rows)
        # All 6 records
        assert ids == [1, 2, 3, 4, 5, 6]

    def test_exclude_types(self, db_with_periods):
        db, today = db_with_periods
        rows = db.historical_periods(exclude_types=["travel", "baseline"])
        ids = sorted(r["id"] for r in rows)
        # Exclude id=3 (travel), id=5 (travel), id=6 (baseline)
        # Include id=1, 2 (treatment+watchful_waiting). NOT 4 (deleted by default).
        assert ids == [1, 2]

    def test_exclude_types_with_include_deleted(self, db_with_periods):
        db, today = db_with_periods
        rows = db.historical_periods(exclude_types=["travel"], include_deleted=True)
        ids = sorted(r["id"] for r in rows)
        # Exclude travels (3, 5), include deleted (4)
        assert ids == [1, 2, 4, 6]


class TestFuturePeriods:
    def test_future_only(self, db_with_periods):
        db, today = db_with_periods
        rows = db.future_periods()
        names = [r["name"] for r in rows]
        # Only id=3 (future travel)
        assert names == ["Future travel"]

    def test_excludes_deleted(self, db_with_periods):
        db, today = db_with_periods
        # Even if a deleted period was future-dated — excluded
        with db.get_conn() as c:
            c.execute("INSERT INTO periods (name, type, start_date, end_date, active, deleted_at) VALUES (?,?,?,?,?,?)",
                      ("Future deleted", "travel", str(today + timedelta(days=30)), None, 0, "2026-01-01"))
        rows = db.future_periods()
        names = [r["name"] for r in rows]
        assert "Future deleted" not in names


class TestSoftDelete:
    def test_soft_delete_sets_deleted_at(self, db_with_periods):
        db, today = db_with_periods
        db.soft_delete_period(1, reason="superseded by id=5")
        with db.get_conn() as c:
            row = c.execute("SELECT active, deleted_at, notes FROM periods WHERE id=1").fetchone()
        assert row["active"] == 0
        assert row["deleted_at"] is not None
        assert "superseded" in (row["notes"] or "")

    def test_soft_delete_excludes_from_current(self, db_with_periods):
        db, today = db_with_periods
        db.soft_delete_period(1)
        rows = db.current_periods()
        ids = [r["id"] for r in rows]
        assert 1 not in ids


class TestBackwardCompat:
    def test_get_active_period_alias(self, db_with_periods):
        """get_active_period (deprecated) делегирует current_periods."""
        db, today = db_with_periods
        legacy = db.get_active_period()
        new = db.current_periods()
        assert sorted(r["id"] for r in legacy) == sorted(r["id"] for r in new)


class TestInvariant:
    def test_active_eq_deleted_at_null(self, db_with_periods):
        """Invariant: active=1 ⇔ deleted_at IS NULL (для всех записей)."""
        db, today = db_with_periods
        with db.get_conn() as c:
            mismatch_a = c.execute(
                "SELECT COUNT(*) FROM periods WHERE active=0 AND deleted_at IS NULL"
            ).fetchone()[0]
            mismatch_b = c.execute(
                "SELECT COUNT(*) FROM periods WHERE active=1 AND deleted_at IS NOT NULL"
            ).fetchone()[0]
        assert mismatch_a == 0
        assert mismatch_b == 0
