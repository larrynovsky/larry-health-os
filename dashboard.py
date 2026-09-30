"""Health Dashboard — Tailscale-only витрина state из health.db.

Skeleton MVP (Wave 6, #181). Read-only страницы + HTMX inline-edit.

Запуск:
  Слушает ТОЛЬКО на Tailscale-интерфейсе infra_config.STUDIO_HOST:DASHBOARD_PORT.
  Не добавлять в `tailscale funnel` — доступ только из private mesh.

launchd: ~/Library/LaunchAgents/com.larry.health.dashboard.plist (KeepAlive).

──────────────────────────────────────────────────────────────────────────────
Sprint 5 FINISH (2026-05-22): orchestrator-only. Декомпозиция god-файла
1223 → ~75 LOC через паттерн:
  - dashboard_state         — templates instance + filters registration
  - dashboard_db            — _conn/_q/_w/_log_edit/_ensure_dashboard_edits_table
  - dashboard_filters       — Jinja2 filters
  - dashboard_views         — HTML-cell renderers + whitelist constants
  - dashboard_routers/
      api_profile           — patient_profile inline-edit (2 endpoints)
      api_status_actions    — hypotheses/protocols/problems/proposals
      api_tasks             — tasks edit/save/done/snooze (4 ep)
      api_events            — events/new POST + ping (2 ep)
      views                 — 15 GET HTML pages

Р-3 (F-111): single-primary guard в lifespan — fail-fast если не основная машина (infra_config.is_primary)
и нет ALLOW_WRITE_NONPRIMARY=1 env. Защищает от split-brain через iCloud sync
при случайном запуске на MacBook.
"""
from __future__ import annotations
import infra_config

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# INTENT: dashboard — дашборд: витрина из БД за периметром tailnet (write-гейт + no-auth).
#          Замысел и инварианты — subsystem_intent.yaml, раздел dashboard.
import health_db
from dashboard_db import _ensure_dashboard_edits_table
from dashboard_state import templates, STATIC_DIR


@asynccontextmanager
async def _lifespan(app):
    """Startup: миграция dashboard_edits + Р-3 single-primary guard.

    Sprint 5 / Р-3 (F-111): dashboard fail-fast на non-primary без явного
    ALLOW_WRITE_NONPRIMARY=1. 14 POST endpoint пишут через прямой
    sqlite3.connect (census 2026-09-26; история: 31 → 16 → 15 → 14, см. subsystem_intent.yaml,
    BL-EXP-1) — без guard мог случиться split-brain через iCloud.
    """
    import logging as _logging
    import os
    import socket
    _log = _logging.getLogger("uvicorn")

    if not health_db._is_primary() and not os.environ.get("ALLOW_WRITE_NONPRIMARY"):
        msg = (
            f"dashboard write-mode заблокирован: not on primary host "
            f"(hostname={socket.gethostname()}). "
            "Запуск разрешён только на основной машине (private/infra.yaml: primary_host) или с явным "
            "ALLOW_WRITE_NONPRIMARY=1 (только для dev/test)."
        )
        _log.error(msg)
        raise RuntimeError(msg)

    try:
        _ensure_dashboard_edits_table()
        _log.info("dashboard_edits migration OK")
    except Exception as e:
        _log.error("dashboard_edits migration FAILED: %s", e)
        raise
    # Пульс службы в контейнере (docker-install, этап 2б): удар из цикла событий uvicorn.
    # Натив — без метки HEALTH_SERVICE_LABEL beat() ничего не делает.
    import asyncio
    import daemon_liveness

    async def _pulse():
        while True:
            daemon_liveness.beat()
            await asyncio.sleep(daemon_liveness.PULSE_EVERY_S)

    pulse = asyncio.create_task(_pulse())
    try:
        yield
    finally:
        pulse.cancel()


app = FastAPI(title="Health Dashboard", lifespan=_lifespan)
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Routers ───────────────────────────────────────────────────────────────────
from dashboard_routers.api_profile        import router as _api_profile_router
from dashboard_routers.api_status_actions import router as _api_status_actions_router
from dashboard_routers.api_tasks          import router as _api_tasks_router
from dashboard_routers.api_events         import router as _api_events_router
from dashboard_routers.api_lab_review      import router as _api_lab_review_router
from dashboard_routers.api_hae_ingest      import router as _api_hae_ingest_router
from dashboard_routers.api_location_ingest import router as _api_location_ingest_router
from dashboard_routers.views              import router as _views_router

app.include_router(_api_profile_router)
app.include_router(_api_status_actions_router)
app.include_router(_api_tasks_router)
app.include_router(_api_events_router)
app.include_router(_api_lab_review_router)
app.include_router(_api_hae_ingest_router)
app.include_router(_api_location_ingest_router)
app.include_router(_views_router)


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    import os as _os
    # ВАЖНО: bind на Tailscale-интерфейс, НЕ 0.0.0.0
    # Порт override через env DASHBOARD_PORT (второй инстанс тенанта, напр. партнёр :8002)
    _port = int(_os.environ.get("DASHBOARD_PORT") or infra_config.DASHBOARD_PORT)
    # В контейнере (docker-install, этап 5) слушаем внутреннюю сеть контейнера (DASHBOARD_HOST из
    # compose): 127.0.0.1 внутри контейнера публикация порта не достаёт. Наружу порт выходит только
    # на 127.0.0.1 хоста (compose ports), в tailnet — через tailscale serve (этап 11).
    _host = _os.environ.get("DASHBOARD_HOST") or infra_config.STUDIO_HOST
    uvicorn.run(app, host=_host, port=_port, log_level="info")
