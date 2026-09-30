"""
env_sources.py — фетчеры среды дома тенанта с кэшем и провенансом свежести.

Каждый фетчер → {source, ok, delivery, age_min?, data?|error?}. delivery:
stable_api (Open-Meteo/aqicn) | scrape_risk (национальная метеослужба региона — ПЛАНИРУЕТСЯ, ещё не написан) |
manual_staleness (источник недоступен / не сконфигурирован).
При сбое ok=False адаптер (env_context.assemble_env_cards) ПРОПУСКАЕТ источник —
карточки нет (A7: молчим, не фантазируем). Статус degraded_stale в схеме БД
зарезервирован под будущую ВИДИМУЮ деградацию (источник был свежим → протух),
env-путём пока НЕ эмитится. Кэш файловый (TTL), чтобы не долбить API и пережить блип.

Open-Meteo — без ключа. aqicn — токен из ~/.health_secrets/aqicn_token (общий погодный,
не тенант-приватный; лежит на Studio). ЖИВОЙ за флагом MORNING_BRIEF_GATE (вкл. в проде).
Координаты — всегда аргументом от вызывающего (дом тенанта из location_signal.home_anchor).
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import region_pack

# ДОМ КЭША — НЕ КАТАЛОГ СЕКРЕТОВ (2026-09-02). Замер: 12 файлов 644 и каталог 755
# внутри каталога 700; сенсор прав (security_sensors._secrets_perms) их НЕ видел —
# он итерировал только файлы верхнего уровня. Норма проекта записана там же дословно:
# «в каталоге секретов лежат только секреты, и каждый обязан быть 600».
_CACHE_DIR = Path.home() / ".health_cache" / "env"
_TTL_SEC = 2 * 3600
_AQICN_TOKEN_PATH = Path.home() / ".health_secrets" / "aqicn_token"


def _http_json(url: str, timeout: int = 8) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "health-os/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _ensure_cache_home() -> None:
    """Дом кэша — 700, файлы — 600. ЯВНО, а не надеждой на umask.

    Замер 2026-09-02, живой прогон после переезда из каталога секретов: свежий
    mkdir даёт 755, write_text даёт 644. Внутри прежнего дома (каталог секретов, 700)
    эти же права были прикрыты родителем; переезд ЗАЩИТУ СНЯЛ, а в кэше лежат
    координаты (marine_<lat>_<lon>, open_meteo_<lat>_<lon>) — данные о местоположении.
    Ручной chmod чинил бы экземпляр: любая новая машина и любое пересоздание кэша
    вернули бы 755. Поэтому права ставит код, при каждом обращении.
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for d in (_CACHE_DIR.parent, _CACHE_DIR):
        try:
            if d.stat().st_mode & 0o077:
                d.chmod(0o700)
        except OSError as e:                       # noqa: BLE001 — чужой каталог/ФС
            log = __import__("logging").getLogger(__name__)
            log.warning("не удалось сузить права кэша %s: %s", d, e)


def _cached(key: str, fetch_fn):
    """Файловый кэш с TTL → (data, age_sec, from_cache)."""
    _ensure_cache_home()
    p = _CACHE_DIR / f"{key}.json"
    now = time.time()
    if p.exists() and (now - p.stat().st_mtime) < _TTL_SEC:
        return json.loads(p.read_text()), int(now - p.stat().st_mtime), True
    data = fetch_fn()
    p.write_text(json.dumps(data))
    p.chmod(0o600)
    return data, 0, False


def parse_open_meteo(forecast: dict, air: dict) -> dict:
    """Чистый парсер → {temp_max, uv_max, dust, pm10, pm25} (сегодняшние пики)."""
    out: dict = {}
    d = (forecast or {}).get("daily", {}) or {}
    if d.get("temperature_2m_max"):
        out["temp_max"] = d["temperature_2m_max"][0]
    if d.get("uv_index_max"):
        out["uv_max"] = d["uv_index_max"][0]
    h = (air or {}).get("hourly", {}) or {}

    def _peak(key):
        vals = [v for v in (h.get(key) or []) if isinstance(v, (int, float))]
        return round(max(vals), 1) if vals else None

    out["dust"] = _peak("dust")
    out["pm10"] = _peak("pm10")
    out["pm25"] = _peak("pm2_5")
    return out


def fetch_open_meteo(lat: float, lon: float) -> dict:
    """Open-Meteo forecast (жара/UV) + air-quality (пыль/PM). Без ключа."""
    def _do():
        fc = _http_json(
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            f"&daily=temperature_2m_max,uv_index_max&timezone=auto&forecast_days=1")
        aq = _http_json(
            f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}"
            f"&hourly=pm10,pm2_5,dust,uv_index&timezone=auto&forecast_days=1")
        return {"forecast": fc, "air": aq}
    try:
        # Ключ кэша ЗАВИСИТ от координат — иначе поездка отдала бы кэш дома (travel-баг).
        raw, age, cached = _cached(f"open_meteo_{lat:.2f}_{lon:.2f}", _do)
        return {"source": "open_meteo", "ok": True, "delivery": "stable_api",
                "age_min": age // 60, "from_cache": cached,
                "data": parse_open_meteo(raw.get("forecast", {}), raw.get("air", {}))}
    except Exception as e:
        return {"source": "open_meteo", "ok": False, "delivery": "manual_staleness", "error": repr(e)}


def fetch_marine(lat: float, lon: float) -> dict:
    """Open-Meteo Marine: температура моря (пик за день) + макс. высота волны. Без ключа.
    Только для домашнего берега (море — дома-only, семейная активность, 2026-07-14)."""
    def _do():
        return _http_json(
            f"https://marine-api.open-meteo.com/v1/marine?latitude={lat}&longitude={lon}"
            f"&daily=wave_height_max&hourly=sea_surface_temperature&timezone=auto&forecast_days=1")
    try:
        raw, age, cached = _cached(f"marine_{lat:.2f}_{lon:.2f}", _do)
        sst = [v for v in (raw.get("hourly", {}) or {}).get("sea_surface_temperature", [])
               if isinstance(v, (int, float))]
        waves = (raw.get("daily", {}) or {}).get("wave_height_max") or []
        return {"source": "open_meteo_marine", "ok": True, "delivery": "stable_api",
                "age_min": age // 60, "from_cache": cached,
                "data": {"sea_temp": round(max(sst), 1) if sst else None,
                         "wave_max": waves[0] if waves else None}}
    except Exception as e:
        return {"source": "open_meteo_marine", "ok": False, "delivery": "manual_staleness", "error": repr(e)}


# Фид предупреждений региона — данные (private/region.yaml, `meteoalarm_feed`); нет → источник не сконфигурирован.
_MET_WARN_URL = region_pack.value("meteoalarm_feed")


def parse_met_warnings(xml_text: str) -> list:
    """MeteoAlarm Atom (CAP) → [{event, severity, onset, expires}]. Чистый парсер."""
    import xml.etree.ElementTree as ET
    ns = {"a": "http://www.w3.org/2005/Atom",
          "cap": "urn:oasis:names:tc:emergency:cap:1.2"}
    out: list = []
    root = ET.fromstring(xml_text)
    for e in root.findall("a:entry", ns):
        def g(tag):
            el = e.find(tag, ns)
            return el.text.strip() if (el is not None and el.text) else None
        event = g("cap:event")
        if not event:
            continue
        out.append({"event": event, "severity": g("cap:severity"),
                    "onset": g("cap:onset") or g("cap:effective"),
                    "expires": g("cap:expires")})
    return out


def fetch_met_warnings() -> dict:
    """Официальные предупреждения региона (MeteoAlarm CAP-фид). Бесплатно, CC BY 4.0.
    Дома-only (фид региона). ok=True с пустым списком = нет активных предупреждений."""
    if not _MET_WARN_URL:
        return {"source": "meteoalarm", "ok": False, "delivery": "manual_staleness", "error": "no region feed"}

    def _do():
        req = urllib.request.Request(_MET_WARN_URL, headers={"User-Agent": "health-os/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return {"xml": r.read().decode()}
    try:
        raw, age, cached = _cached("met_warnings", _do)
        return {"source": "meteoalarm", "ok": True, "delivery": "stable_api",
                "age_min": age // 60, "from_cache": cached,
                "data": {"warnings": parse_met_warnings(raw.get("xml", ""))}}
    except Exception as e:
        return {"source": "meteoalarm", "ok": False, "delivery": "manual_staleness", "error": repr(e)}


def fetch_aqicn(city: str | None = None) -> dict:
    """aqicn наземная станция. Город — аргументом или из пакета региона (`aqicn_city`).
    Токен из ~/.health_secrets/aqicn_token."""
    city = city or region_pack.value("aqicn_city")
    if not city:
        return {"source": "aqicn", "ok": False, "delivery": "manual_staleness", "error": "no region city"}
    try:
        token = _AQICN_TOKEN_PATH.read_text().strip()
    except Exception:
        return {"source": "aqicn", "ok": False, "delivery": "manual_staleness", "error": "no token"}

    def _do():
        return _http_json(f"https://api.waqi.info/feed/{urllib.parse.quote(city)}/?token={token}")
    try:
        raw, age, cached = _cached("aqicn", _do)
        if raw.get("status") != "ok":
            return {"source": "aqicn", "ok": False, "delivery": "stable_api", "error": raw.get("data")}
        iaqi = (raw.get("data") or {}).get("iaqi", {}) or {}
        data = {k: v.get("v") for k, v in iaqi.items() if isinstance(v, dict)}
        data["aqi"] = (raw.get("data") or {}).get("aqi")
        return {"source": "aqicn", "ok": True, "delivery": "stable_api",
                "age_min": age // 60, "from_cache": cached, "data": data}
    except Exception as e:
        return {"source": "aqicn", "ok": False, "delivery": "manual_staleness", "error": repr(e)}
