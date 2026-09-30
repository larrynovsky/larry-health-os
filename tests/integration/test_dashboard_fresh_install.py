"""Дашборд на базе свежей установки: ни одна страница не падает с 500.

Зачем отдельно от test_dashboard_smoke: тот судит страницы на замороженной схеме
tests/fixtures/health_schema.sql, где до сих пор есть снятая таблица experiments.
26.09 он был зелёным, пока главная и /experiments у владельца отдавали 500 (§20:
зелёный от окружения). Здесь схему строит ТОТ ЖЕ init_db, что и scripts/install.py,
а список страниц берётся из самого приложения — новая страница попадает под суд сама.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fresh_client(tmp_path, monkeypatch):
    health_dir = tmp_path / "health"
    data_dir = health_dir / "data"
    data_dir.mkdir(parents=True)
    monkeypatch.setenv("HEALTH_DATA_DIR", str(health_dir))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", data_dir / "health.db")
    monkeypatch.setattr(health_db, "_HEALTH_DIR", health_dir)
    monkeypatch.setattr(health_db, "ICLOUD", health_dir)
    monkeypatch.setattr(health_db, "METRICS_DIR", data_dir / "daily_metrics")
    health_db.init_db()

    from fastapi.testclient import TestClient
    import dashboard
    with TestClient(dashboard.app, raise_server_exceptions=False) as c:
        yield c, dashboard.app


def _pages(app):
    return sorted(r.path for r in app.routes
                  if "GET" in getattr(r, "methods", ()) and "{" not in r.path
                  and not r.path.startswith(("/docs", "/redoc", "/openapi", "/static")))


def test_every_page_renders_on_fresh_install(fresh_client):
    c, app = fresh_client
    pages = _pages(app)
    assert "/" in pages and len(pages) >= 10, pages   # страховка от пустого обхода
    broken = {p: r.status_code for p in pages if (r := c.get(p)).status_code >= 500}
    assert not broken, f"страницы падают на пустой базе: {broken}"


def test_sidebar_links_resolve(fresh_client):
    c, _ = fresh_client
    sidebar = (ROOT / "dashboard_templates" / "sidebar.html").read_text(encoding="utf-8")
    links = re.findall(r'href="(/[^"]*)"', sidebar)
    assert links, "в sidebar не найдено ни одной ссылки"
    dead = {h: r.status_code for h in links if (r := c.get(h)).status_code >= 400}
    assert not dead, f"ссылки меню ведут в никуда: {dead}"
