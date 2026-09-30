"""dashboard_routers.api_hae_ingest — приём health-данных от Health Auto Export по REST.

Замена хрупкого iCloud-file-sync + poll (гонка с ленивой докачкой iCloud, лаг 1-3 дня):
HAE POST'ит JSON прямо на этот эндпоинт → пишем локальный файл (provenance) → тот же
battle-tested парсер import_apple_health.process_hae_json → save_daily_summaries → daily_metrics.
Данные свежие сразу, без iCloud-лага. См. HAE REST API doc.

Безопасность: дашборд за Tailscale Serve (tailnet-приватный), но WRITE-эндпоинт обязан
иметь токен (иначе любое устройство в тайлнете могло бы залить фейковые метрики).
Токен: <HEALTH_SECRETS_DIR>/hae_ingest_token (per-tenant, как у /location/ingest): у
каждого человека свой дашборд и свой токен. До 23.09 путь был жёстко ~/.health_secrets —
телефон второго человека мог войти только токеном владельца. HAE шлёт
`Authorization: Bearer <token>`.
"""
from __future__ import annotations
from _time_inject import get_today, get_utcnow  # seam

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request

log = logging.getLogger(__name__)
router = APIRouter(prefix="/hae", tags=["hae-ingest"])

_DATA = Path(__import__("os").environ.get("HEALTH_DATA_DIR", str(Path.home() / "health"))) / "data"
_REST_DIR = _DATA / "hae_rest"
_ECG_DIR = _DATA / "hae_rest_ecg"


def _payload_is_fresh(daily, max_age_days: int = 2) -> bool:
    """True, если распарсенный HAE-payload несёт день в пределах max_age_days.
    Гейт маркера свежести apple_health: пустой/heartbeat (daily={}) и исторический
    backfill (только старые даты) → False. Иначе датчик зеленел бы на стуке
    (ложно-зелёное), которого он призван не допускать. daily = dict[date_str→summary]."""
    if not daily:
        return False
    cutoff = (get_today() - timedelta(days=max_age_days)).isoformat()
    return max(daily) >= cutoff


def _token_path() -> Path:
    from secrets_paths import secrets_dir
    return secrets_dir() / "hae_ingest_token"


def _check_token(authorization: str | None, x_api_key: str | None) -> None:
    p = _token_path()
    if not p.exists():
        raise HTTPException(503, "hae_ingest_token не настроен на сервере")
    expected = p.read_text().strip()
    provided = ""
    if authorization and authorization.startswith("Bearer "):
        provided = authorization[len("Bearer "):].strip()
    provided = provided or (x_api_key or "").strip()
    if not expected or provided != expected:
        raise HTTPException(401, "неверный или отсутствующий токен")


@router.post("/ingest")
async def hae_ingest(
    request: Request,
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
):
    """Приём HAE Health Metrics JSON → daily_metrics. Идемпотентно (merge)."""
    _check_token(authorization, x_api_key)
    body = await request.body()
    if not body:
        raise HTTPException(400, "пустое тело")

    _REST_DIR.mkdir(parents=True, exist_ok=True)
    ts = get_utcnow().strftime("%Y%m%dT%H%M%S")
    fpath = _REST_DIR / f"HealthAutoExport-REST-{ts}.json"
    fpath.write_bytes(body)

    try:
        import import_apple_health as ia
        daily = ia.process_hae_json(fpath)          # переиспользуем файловый парсер
        rows = ia.save_daily_summaries(daily, mode="merge")
    except Exception as e:  # noqa: BLE001 — вернуть 400, не 500, чтобы HAE показал ошибку
        log.error("hae_ingest: парсинг/запись упали: %s", e, exc_info=True)
        raise HTTPException(400, f"не удалось обработать payload: {type(e).__name__}: {e}")

    # У каждой пришедшей метрики — хозяин (26.09). Прежний файловый скан (hae_checker.run_check,
    # снят 26.09) смотрел iCloud-каталоги, пустые после миграции на REST (06.07) — реестр не видел
    # НИ одной метрики, и давление тонометра пропадало молча. Судим здесь, на приёме; отказ судьи
    # не должен ронять уже принятые данные — но и не молчит (ночной триаж читает реестр).
    try:
        import hae_checker
        loud = hae_checker.judge_payload(fpath)
        if loud:
            log.warning("hae_ingest: метрики без хозяина: %s", ", ".join(loud))
    except Exception as _e:  # noqa: BLE001 — данные уже приняты
        log.error("hae_ingest: судья метрик упал: %s", _e, exc_info=True)

    # Свежесть apple_health: метим ТОЛЬКО при свежем payload (гейт). Файловый
    # _export_is_fresh смотрит iCloud-каталоги, пустые после миграции на REST, —
    # поэтому REST-путь обязан метить сам, иначе датчик врёт «мёртво» при живом потоке.
    marked_fresh = False
    try:
        if _payload_is_fresh(daily):
            import import_apple_health as _ia
            _ia._mark_apple_health_fresh()
            marked_fresh = True
    except Exception as _e:  # noqa: BLE001 — маркер не должен ронять уже принятые данные
        log.warning("hae_ingest: не удалось пометить свежесть apple_health: %s", _e)

    log.info("hae_ingest: %d дней, %d строк, fresh=%s из %s",
             len(daily), rows, marked_fresh, fpath.name)
    return {"ok": True, "days": len(daily), "rows_written": rows, "marked_fresh": marked_fresh}


@router.post("/ecg")
async def hae_ecg(
    request: Request,
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
):
    """Приём HAE ECG JSON (`data.ecg[]`) → ecg_readings (вердикт+метаданные, без волны).

    Полный payload с волной (512 Гц) сохраняется в провенанс-файл ecg_rest_ecg/;
    в БД идут только классификация/HR/severity/sampling. Идемпотентно (dedup start_time+source).
    """
    _check_token(authorization, x_api_key)
    body = await request.body()
    if not body:
        raise HTTPException(400, "пустое тело")

    _ECG_DIR.mkdir(parents=True, exist_ok=True)
    ts = get_utcnow().strftime("%Y%m%dT%H%M%S")
    fpath = _ECG_DIR / f"HealthAutoExport-ECG-{ts}.json"
    fpath.write_bytes(body)

    try:
        import json as _json
        payload = _json.loads(body)
        data = payload.get("data", payload)
        entries = data.get("ecg") or payload.get("ecg") or []
        import ecg_db
        inserted = ecg_db.save_ecg_readings(entries, source_file=fpath.name)
    except Exception as e:  # noqa: BLE001 — 400, чтобы HAE показал ошибку
        log.error("hae_ecg: парсинг/запись упали: %s", e, exc_info=True)
        raise HTTPException(400, f"не удалось обработать ECG payload: {type(e).__name__}: {e}")

    log.info("hae_ecg: %d записей получено, %d новых из %s", len(entries), inserted, fpath.name)
    return {"ok": True, "readings": len(entries), "inserted": inserted}
