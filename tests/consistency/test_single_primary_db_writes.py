"""
Single-primary protocol: write в health.db разрешена только на Studio
(host == _PRIMARY). На MacBook без override — read-only.

Источник: USE_CASES.md UC-I-07 + TEST_ARCHITECTURE.md §5.3.
Уровень: consistency.

База уже покрыта unit-тестами (T-pre.2b). Здесь — phantom-тест на сценарий
«mock host=MacBook + попытка write» через monkeypatch.
"""
from __future__ import annotations

import os
import socket
import sqlite3

import pytest

import infra_config as _ic
_PRIMARY = _ic.PRIMARY_HOST  # основная машина установки, не литерал владельца

pytestmark = pytest.mark.consistency


def test_is_primary_uses_hostname(monkeypatch):
    """`_is_primary()` возвращает True только для основной машины."""
    import health_db
    monkeypatch.setattr(socket, "gethostname", lambda: _PRIMARY)
    monkeypatch.delenv("ALLOW_WRITE_NONPRIMARY", raising=False)
    assert health_db._is_primary() is True

    monkeypatch.setattr(socket, "gethostname", lambda: "MacBook-Pro")
    assert health_db._is_primary() is False


def test_allow_write_nonprimary_override(monkeypatch):
    """ALLOW_WRITE_NONPRIMARY=1 включает write для не-primary хоста."""
    import health_db
    monkeypatch.setattr(socket, "gethostname", lambda: "MacBook-Pro")
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    assert health_db._is_primary() is True


def test_get_conn_returns_readonly_on_nonprimary(monkeypatch, tmp_path):
    """
    На не-primary хосте без override — get_conn() возвращает read-only коннект.
    Любой write SQL → OperationalError.
    """
    import health_db

    # Создадим минимальную БД
    db_path = tmp_path / "test.db"
    with sqlite3.connect(db_path) as c:
        c.execute("CREATE TABLE t (x INTEGER)")
        c.execute("INSERT INTO t VALUES (1)")
        c.commit()

    monkeypatch.setattr(health_db, "DB_PATH", db_path)
    monkeypatch.setattr(socket, "gethostname", lambda: "MacBook-Pro")
    monkeypatch.delenv("ALLOW_WRITE_NONPRIMARY", raising=False)
    # Явный HEALTH_DATA_DIR — escape hatch: не-primary с явным путём → read-only.
    # Без него get_conn raise'ит (kill silent iCloud fallback, 59ce993) — см. тест ниже.
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))

    conn = health_db.get_conn()
    # Read OK
    rows = conn.execute("SELECT * FROM t").fetchall()
    assert len(rows) == 1
    # Write FAIL
    with pytest.raises(sqlite3.OperationalError) as exc:
        conn.execute("INSERT INTO t VALUES (2)")
        conn.commit()
    assert "readonly" in str(exc.value).lower()


def test_get_conn_raises_on_nonprimary_without_health_data_dir(monkeypatch):
    """Не-primary БЕЗ HEALTH_DATA_DIR → RuntimeError, а не молчаливое чтение
    устаревшей iCloud-копии (kill silent fallback, 59ce993 / split-brain 2026-06-18)."""
    import health_db
    monkeypatch.setattr(socket, "gethostname", lambda: "MacBook-Pro")
    monkeypatch.delenv("ALLOW_WRITE_NONPRIMARY", raising=False)
    monkeypatch.delenv("HEALTH_DATA_DIR", raising=False)
    with pytest.raises(RuntimeError):
        health_db.get_conn()


def test_get_conn_writable_on_primary(monkeypatch, tmp_path):
    """На primary — write проходит."""
    import health_db
    db_path = tmp_path / "primary.db"
    with sqlite3.connect(db_path) as c:
        c.execute("CREATE TABLE t (x INTEGER)")

    monkeypatch.setattr(health_db, "DB_PATH", db_path)
    monkeypatch.setattr(socket, "gethostname", lambda: _PRIMARY)

    conn = health_db.get_conn()
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()
    rows = conn.execute("SELECT x FROM t").fetchall()
    assert rows[0][0] == 42


def test_audit_concept_writes_only_from_primary(db):
    """
    Документирующий тест: при наличии fixture `db` (с ALLOW_WRITE_NONPRIMARY=1
    из conftest), запись проходит на любом хосте — это и есть override-семантика.

    В проде override НЕ должен быть выставлен (UC-I-07 charter).
    """
    assert os.environ.get("ALLOW_WRITE_NONPRIMARY") == "1", (
        "В тестах ALLOW_WRITE_NONPRIMARY должен быть выставлен (conftest.py). "
        "В проде — НЕТ."
    )
    db.add_daily_metrics("2026-05-08", hrv=22)
    assert db.count("daily_metrics") == 1
