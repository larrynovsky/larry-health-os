"""Нативная копия тенанта, переехавшего в контейнер, вне контейнера — только чтение (06.10.2026).

Замер 06.10: на Studio ~/health/data/health.db (заморожена с переезда 30.09) открывалась на
запись; старый импорт из папки iCloud писал бы документы в мёртвую копию."""
import sqlite3

import pytest

import health_db

pytestmark = pytest.mark.unit


@pytest.fixture
def tenant(tmp_path, monkeypatch):
    root = tmp_path / "health"
    (root / "data").mkdir(parents=True)
    sqlite3.connect(root / "data" / "health.db").close()
    monkeypatch.setattr(health_db, "DB_PATH", root / "data" / "health.db")
    monkeypatch.setattr(health_db, "_is_primary", lambda: True)
    monkeypatch.delenv("HEALTH_RUNTIME", raising=False)
    return root


def test_moved_tenant_is_read_only_outside_container(tenant):
    """Чтение работает (хостовый weekly_digest читает настройки), запись — громкий отказ."""
    (tenant / "RUNTIME").write_text("container\n", encoding="utf-8")
    with health_db.get_conn() as c:
        assert c.execute("select 1").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            c.execute("CREATE TABLE t (x)")


def test_inside_container_the_store_is_live(tenant, monkeypatch):
    (tenant / "RUNTIME").write_text("container\n", encoding="utf-8")
    monkeypatch.setenv("HEALTH_RUNTIME", "container")
    with health_db.get_conn() as c:
        c.execute("CREATE TABLE t (x)")


def test_tenant_without_mark_opens(tenant):
    with health_db.get_conn() as c:
        c.execute("CREATE TABLE t (x)")
