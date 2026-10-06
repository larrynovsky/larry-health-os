"""Тесты не пишут в живую базу тенанта (нить treatment-homes, 04.10.2026).

Повод: фикстуры test_treatment.py лежали в medications владельца с 22.06 — утренний
pytest гоняется с HEALTH_DATA_DIR = живая база.
Оракул: get_conn на базе, объявленной живой через HEALTH_TEST_LIVE_DB, отклоняет запись;
на любой другой базе — пишет как прежде (фикстура `db` не задета).
Мутация: убрать `or _test_run_on_live_db()` из get_conn → первый тест красный.
"""
import sqlite3

import pytest

import health_db


@pytest.fixture
def fake_live(tmp_path, monkeypatch):
    live = tmp_path / "health.db"
    sqlite3.connect(live).execute("CREATE TABLE t (x INTEGER)").connection.commit()
    monkeypatch.setattr(health_db, "_is_primary", lambda: True)
    monkeypatch.setenv("HEALTH_TEST_LIVE_DB", str(live))
    return live


def test_запись_в_живую_базу_отклоняется(fake_live, monkeypatch):
    monkeypatch.setattr(health_db, "DB_PATH", fake_live)
    with health_db.get_conn() as conn:
        assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 0  # читать можно
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO t VALUES (1)")


def test_другая_база_пишется_как_прежде(fake_live, tmp_path, monkeypatch):
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("CREATE TABLE t (x INTEGER)").connection.commit()
    monkeypatch.setattr(health_db, "DB_PATH", other)
    with health_db.get_conn() as conn:
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
    assert sqlite3.connect(other).execute("SELECT count(*) FROM t").fetchone()[0] == 1


def test_без_объявления_страж_молчит(tmp_path, monkeypatch):
    db = tmp_path / "x.db"
    sqlite3.connect(db).execute("CREATE TABLE t (x INTEGER)").connection.commit()
    monkeypatch.delenv("HEALTH_TEST_LIVE_DB", raising=False)
    monkeypatch.setattr(health_db, "DB_PATH", db)
    assert health_db._test_run_on_live_db() is False


def test_прямой_connect_к_живой_базе_на_запись_падает(fake_live):
    """Второй слой (нить treatment-tails): 37 модулей зовут sqlite3.connect мимо get_conn.
    Мутация: убрать sys.addaudithook(_live_db_write_guard) в conftest → красный."""
    with pytest.raises(PermissionError):
        sqlite3.connect(str(fake_live))
    ro = sqlite3.connect(f"file:{fake_live}?mode=ro", uri=True)   # на чтение — можно
    assert ro.execute("SELECT count(*) FROM t").fetchone()[0] == 0
    ro.close()
