"""Sprint 5b integration-тесты для dashboard lifespan / Р-3 guard (F-111).

Использует asyncio.run() в sync-тестах вместо @pytest.mark.asyncio,
потому что pytest-asyncio не установлен (только anyio).
"""
from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def restore_env():
    """Восстанавливает ALLOW_WRITE_NONPRIMARY после теста."""
    saved = os.environ.get("ALLOW_WRITE_NONPRIMARY")
    yield
    if saved is None:
        os.environ.pop("ALLOW_WRITE_NONPRIMARY", None)
    else:
        os.environ["ALLOW_WRITE_NONPRIMARY"] = saved


async def _run_lifespan(dashboard_module):
    """Helper: запустить _lifespan один цикл (startup → shutdown)."""
    async with dashboard_module._lifespan(dashboard_module.app):
        pass


def test_lifespan_blocks_on_nonprimary_without_override(
    db, restore_env, monkeypatch
):
    """non-primary + нет ALLOW_WRITE_NONPRIMARY → RuntimeError."""
    import health_db
    import dashboard
    monkeypatch.setattr(health_db, "_is_primary", lambda: False)
    os.environ.pop("ALLOW_WRITE_NONPRIMARY", None)

    with pytest.raises(RuntimeError, match="not on primary host"):
        asyncio.run(_run_lifespan(dashboard))


def test_lifespan_allows_nonprimary_with_override(
    db, restore_env, monkeypatch
):
    """non-primary + ALLOW_WRITE_NONPRIMARY=1 → OK (override)."""
    import health_db
    import dashboard
    monkeypatch.setattr(health_db, "_is_primary", lambda: False)
    os.environ["ALLOW_WRITE_NONPRIMARY"] = "1"

    asyncio.run(_run_lifespan(dashboard))  # no exception expected


def test_lifespan_allows_primary(
    db, restore_env, monkeypatch
):
    """primary → OK независимо от env var."""
    import health_db
    import dashboard
    monkeypatch.setattr(health_db, "_is_primary", lambda: True)
    os.environ.pop("ALLOW_WRITE_NONPRIMARY", None)

    asyncio.run(_run_lifespan(dashboard))  # no exception expected


def test_lifespan_бьёт_пульс_службы_в_контейнере(db, restore_env, monkeypatch, tmp_path):
    """docker-install, этап 2б: дашборд в контейнере доказывает живость пульсом из цикла
    событий. Без удара датчик увидел бы «пульса нет ни разу» у живого дашборда."""
    import health_db
    import dashboard
    import daemon_liveness
    monkeypatch.setattr(health_db, "_is_primary", lambda: True)
    monkeypatch.setenv(daemon_liveness.PULSE_LABEL_ENV, "com.larry.health.dashboard")
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))

    async def _one_tick():
        async with dashboard._lifespan(dashboard.app):
            await asyncio.sleep(0.05)

    asyncio.run(_one_tick())
    assert daemon_liveness.pulse_path("com.larry.health.dashboard", str(tmp_path)).exists()
