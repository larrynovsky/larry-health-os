"""ingest_lan — приём данных с телефона по домашней сети, без дашборда.

Зачем отдельный процесс. Дашборд без пароля, его периметр — эта машина и частная сеть Tailscale.
Человеку с одним Маком дома Tailscale для «Здоровья» не нужен: телефон и Мак в одной Wi-Fi. Но
открыть в Wi-Fi порт дашборда — значит показать медкарту любому гостю сети. Здесь — только
маршруты записи под токеном (`/hae/ingest`, `/hae/ecg`, `/location/ingest`), чтения нет вовсе.
Разделение — по порту, а не по адресу клиента: в Докере (Colima) адрес клиента за пробросом
порта не виден.

Браузер сюда не ходит: запрос с Origin или Sec-Fetch-Site отвергается — страница чужого сайта в
той же Wi-Fi не может толкать данные, даже угадав токен (решение владельца 02.10, нить hae-lan).
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

import infra_config
from dashboard_routers.api_hae_ingest import router as _hae_router
from dashboard_routers.api_location_ingest import router as _location_router

DEFAULT_PORT = 8011


@asynccontextmanager
async def _lifespan(app):
    # Тот же страж единственного пишущего узла, что у дашборда (health_db._is_primary — приватное
    # соседа, поэтому правило повторено через публичное infra_config.is_primary): процесс пишет в базу.
    if not (os.environ.get("ALLOW_WRITE_NONPRIMARY") or infra_config.is_primary()):
        raise RuntimeError("ingest_lan: запись разрешена только на основной машине "
                           "(private/infra.yaml: primary_host) или с ALLOW_WRITE_NONPRIMARY=1")
    # Пульс службы в контейнере — как у дашборда: живость служба доказывает сама (этап 2б).
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


async def _no_browsers(request, call_next):
    if request.headers.get("origin") is not None or request.headers.get("sec-fetch-site"):
        return PlainTextResponse("browsers are not accepted here", status_code=403)
    return await call_next(request)


def lan_ingest_app() -> FastAPI:
    """Приложение приёма: только маршруты записи под токеном, без страниц и документации API."""
    app = FastAPI(title="Health ingest (LAN)", lifespan=_lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.middleware("http")(_no_browsers)
    app.include_router(_hae_router)
    app.include_router(_location_router)
    return app


app = lan_ingest_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("INGEST_HOST", "127.0.0.1"),
                port=int(os.environ.get("INGEST_PORT") or DEFAULT_PORT), log_level="info")
