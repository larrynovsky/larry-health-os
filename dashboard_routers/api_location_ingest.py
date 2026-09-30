"""dashboard_routers.api_location_ingest — приём координат с устройства (iOS Shortcut).

iPhone (Shortcuts, ежедневная time-автоматизация) POST'ит текущую локацию → пишем
device_location (координаты + свежесть) + belief current_location. Вход travel-режима
брифа: система узнаёт «дома / в поездке» без вопросов и без календаря.

Безопасность как у HAE: дашборд за Tailscale (tailnet-приватный), но WRITE-эндпоинт
обязан иметь токен — иначе любое устройство в тайлнете залило бы фейковую локацию.
Токен: <HEALTH_SECRETS_DIR>/location_ingest_token (per-tenant). Заголовок
`Authorization: Bearer <token>` или `X-API-Key: <token>`.
"""
from __future__ import annotations
from _time_inject import get_utcnow  # seam

import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request

log = logging.getLogger(__name__)
router = APIRouter(prefix="/location", tags=["location-ingest"])


def _token_path() -> Path:
    from secrets_paths import secrets_dir
    return secrets_dir() / "location_ingest_token"


def _check_token(authorization: str | None, x_api_key: str | None) -> None:
    p = _token_path()
    if not p.exists():
        raise HTTPException(503, "location_ingest_token не настроен на сервере")
    expected = p.read_text().strip()
    provided = ""
    if authorization and authorization.startswith("Bearer "):
        provided = authorization[len("Bearer "):].strip()
    provided = provided or (x_api_key or "").strip()
    if not expected or provided != expected:
        raise HTTPException(401, "неверный или отсутствующий токен")


def _num(v, name: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{name}: не число ({v!r})")


@router.post("/ingest")
async def location_ingest(
    request: Request,
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
):
    """Приём {lat, lon, accuracy?, ts?}. Пишет device_location + belief current_location."""
    _check_token(authorization, x_api_key)
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "тело не JSON")
    if not isinstance(payload, dict):
        raise HTTPException(400, "ожидался JSON-объект")

    lat = _num(payload.get("lat"), "lat")
    lon = _num(payload.get("lon"), "lon")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(400, f"координаты вне диапазона: lat={lat}, lon={lon}")
    acc = payload.get("accuracy")
    acc = _num(acc, "accuracy") if acc is not None else None

    # Провенанс-файл (как HAE): сырьё на диск до обработки.
    try:
        import os
        data_dir = Path(os.environ.get("HEALTH_DATA_DIR", str(Path.home() / "health"))) / "data"
        prov = data_dir / "location_rest"
        prov.mkdir(parents=True, exist_ok=True)
        ts = get_utcnow().strftime("%Y%m%dT%H%M%S")
        (prov / f"loc-{ts}.json").write_text(await _raw(request, payload))
    except Exception as e:  # noqa: BLE001 — провенанс не критичен для приёма
        log.warning("location_ingest: провенанс-файл не записан: %r", e)

    import location_signal as ls
    summary = ls.record(lat, lon, accuracy_m=acc, source="device_gps")
    log.info("location_ingest: %s (is_home=%s, name=%s)", (lat, lon),
             summary["is_home"], summary["name"])
    return {"ok": True, **summary}


async def _raw(request: Request, fallback: dict) -> str:
    import json
    try:
        return (await request.body()).decode() or json.dumps(fallback)
    except Exception:  # noqa: BLE001
        return json.dumps(fallback)
