"""
Unit-тест на саму fixture `db` из tests/fixtures/db.py.

Проверяет:
- БД создаётся со схемой
- builders вставляют строки
- реальный health_db API работает с тестовой БД (через monkeypatch DB_PATH)
- между тестами БД свежая (нет утечек данных)
"""
from __future__ import annotations

import sqlite3

import pytest

pytestmark = pytest.mark.unit


def test_db_fixture_creates_schema(db):
    """Все ключевые таблицы созданы."""
    tables = [r["name"] for r in db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )]
    expected = {"daily_metrics", "lab_results", "problem_list", "checkins",
                "tasks", "genetic_variants", "agent_reports", "memory",
                "experiments", "context_events", "periods"}
    missing = expected - set(tables)
    assert not missing, f"missing tables: {missing}"


def test_db_starts_empty(db):
    assert db.count("daily_metrics") == 0
    assert db.count("lab_results") == 0
    assert db.count("checkins") == 0


def test_add_daily_metrics(db):
    db.add_daily_metrics("2026-05-08", hrv=25, sleep_total=7.5, steps=8000)
    row = db.fetchone("SELECT * FROM daily_metrics WHERE date=?", ("2026-05-08",))
    assert row is not None
    assert row["hrv"] == 25
    assert row["sleep_total"] == 7.5
    assert row["steps"] == 8000


def test_add_daily_metrics_replace(db):
    """INSERT OR REPLACE: повторный add для той же даты — обновляет."""
    db.add_daily_metrics("2026-05-08", hrv=25)
    db.add_daily_metrics("2026-05-08", hrv=30)
    row = db.fetchone("SELECT hrv FROM daily_metrics WHERE date=?", ("2026-05-08",))
    assert row["hrv"] == 30
    assert db.count("daily_metrics") == 1


def test_add_lab_result(db):
    rid = db.add_lab_result("2026-05-01", "WBC", 4.2,
                             unit="x10^9/L", ref_low=4.0, ref_high=10.0, status=None)
    assert rid > 0
    row = db.fetchone("SELECT * FROM lab_results WHERE id=?", (rid,))
    assert row["test_name"] == "WBC"
    assert row["value"] == 4.2
    assert row["status"] is None


def test_add_lab_result_with_flag(db):
    """status='high' = «выход за верхний порог», аналог flagged=true."""
    rid = db.add_lab_result("2026-05-01", "AST", 80,
                             unit="U/L", ref_low=10, ref_high=40, status="high")
    row = db.fetchone("SELECT status FROM lab_results WHERE id=?", (rid,))
    assert row["status"] == "high"


def test_add_problem(db):
    db.add_problem("P001", "Test problem", priority=1)
    row = db.fetchone("SELECT * FROM problem_list WHERE problem_id=?", ("P001",))
    assert row is not None
    assert row["title"] == "Test problem"
    assert row["priority"] == 1  # 1=high (INTEGER)
    assert row["status"] == "active"  # default
    assert row["first_seen"] is not None  # NOT NULL констрейнт


def test_add_checkin(db):
    db.add_checkin("2026-05-08", "Как день?", "норм", time_of_day="evening")
    row = db.fetchone("SELECT * FROM checkins WHERE date=?", ("2026-05-08",))
    assert row is not None
    assert row["answer"] == "норм"


def test_add_genetic_variant(db):
    db.add_genetic_variant("rs4680", gene="COMT", genotype="AG",
                            significance="risk factor",
                            annotation_source="functional_whitelist")
    row = db.fetchone("SELECT * FROM genetic_variants WHERE rsid=?", ("rs4680",))
    assert row["gene"] == "COMT"
    assert row["genotype"] == "AG"
    assert row["annotation_source"] == "functional_whitelist"


def test_health_db_api_uses_test_db(db):
    """
    Главное — реальный health_db API работает с тестовой БД, а не с реальной
    health.db на Studio/iCloud.
    """
    db.add_daily_metrics("2026-05-08", hrv=42, sleep_total=8.0)

    import health_db
    # get_day должна вернуть JSON-форму того, что мы добавили
    row = health_db.get_day("2026-05-08")
    assert row is not None, f"health_db.get_day не нашёл вчерашнюю запись (DB_PATH={health_db.DB_PATH})"


def test_db_isolation_between_tests_part1(db):
    """Этот тест добавляет; следующий должен видеть пустую БД."""
    db.add_daily_metrics("2099-12-31", hrv=999)
    assert db.count("daily_metrics") == 1


def test_db_isolation_between_tests_part2(db):
    """Если изоляция работает — здесь БД пустая."""
    assert db.count("daily_metrics") == 0
    row = db.fetchone("SELECT * FROM daily_metrics WHERE date='2099-12-31'")
    assert row is None
