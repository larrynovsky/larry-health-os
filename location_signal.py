"""
location_signal.py — сигнал локации пользователя с устройства (iOS Shortcut → GPS).

Один публичный смысл: «где сейчас пользователь и насколько свежо мы это знаем».
Вход — /location/ingest (dashboard_routers/api_location_ingest). Выход — resolve_place()
для travel-режима брифа + запись current_location в belief (memory_facts).

Дом — данные тенанта (system_config location.home_*), литерала нет. is_home по гаверсинусу.
Свежесть — по received_at последнего сигнала (порог FRESH_MAX_H): устарело → travel-режим
не доверяет координатам (не фантазирует место). Reverse-geocode (имя города) —
best-effort, сбой → name=None, координаты всё равно есть (A7: не выдумываем).
"""
from __future__ import annotations
from _time_inject import get_now  # seam

import json
import logging
import math
import urllib.request
from datetime import datetime, timezone

import health_db as _hdb

log = logging.getLogger(__name__)

# Дом тенанта — ТОЛЬКО данные (§9): system_config location.home_lat / home_lon /
# home_radius_km. Литерала-дефолта нет с 2026-09-23 (нить pii-scrub): прежний дефолт был
# городом владельца, раздавался всем тенантам и ехал бы в открытый репозиторий. Существующие
# тенанты не затронуты — их дом давно лежит в system_config. Дом не задан → is_home=False
# (всё считается поездкой) и WARNING в лог: тихой подстановки чужого дома больше нет.
HOME_RADIUS_KM = 15.0     # радиус, если тенант задал только координаты: пригороды — да, соседний город — нет
FRESH_MAX_H = 36.0        # сигнал старше — не доверяем координатам


def home_anchor() -> tuple:
    """(lat, lon, radius_km) дома тенанта из конфига (§9); дом не задан → (None, None, radius)."""
    lat = lon = None
    rad = HOME_RADIUS_KM
    try:
        import config_db as _cfg
        lat = _cfg.get_config("location.home_lat", None)
        lon = _cfg.get_config("location.home_lon", None)
        rad = float(_cfg.get_config("location.home_radius_km", rad))
    except Exception as e:  # noqa: BLE001 — конфиг недоступен → дома нет, сказано ниже
        log.warning("home_anchor: конфиг недоступен: %r", e)
    if lat is None or lon is None:
        log.warning("home_anchor: дом тенанта не задан (system_config location.home_*) — всё считается поездкой")
        return None, None, rad
    return float(lat), float(lon), rad


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def is_home(lat: float, lon: float) -> bool:
    hlat, hlon, rad = home_anchor()
    if hlat is None:
        return False
    return _haversine_km(lat, lon, hlat, hlon) <= rad


def _reverse_geocode(lat: float, lon: float, timeout: int = 6) -> str | None:
    """Координаты → имя города, best-effort. BigDataCloud reverse-geocode-client
    (без ключа, бесплатно). Сбой/таймаут → None (координаты остаются; не фантазируем)."""
    try:
        url = ("https://api.bigdatacloud.net/data/reverse-geocode-client"
               f"?latitude={lat}&longitude={lon}&localityLanguage=ru")
        req = urllib.request.Request(url, headers={"User-Agent": "health-os/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
        return (d.get("city") or d.get("locality") or d.get("principalSubdivision")
                or d.get("countryName") or None)
    except Exception as e:  # noqa: BLE001 — reverse-geocode не критичен, координаты важнее
        log.warning("reverse_geocode упал: %r", e)
        return None


def record(lat: float, lon: float, accuracy_m: float | None = None,
           source: str = "device_gps", geocode: bool = True) -> dict:
    """Записать сигнал локации + обновить belief current_location. Возвращает summary."""
    home = is_home(lat, lon)
    name = _reverse_geocode(lat, lon) if geocode else None
    _hdb._ensure_device_location_table()
    with _hdb.get_conn() as conn:
        conn.execute(
            "INSERT INTO device_location (subject, lat, lon, accuracy_m, name, is_home, source) "
            "VALUES ('self', ?, ?, ?, ?, ?, ?)",
            (lat, lon, accuracy_m, name, 1 if home else 0, source))
    # Belief current_location: имя места (или «дом»/«поездка» если геокод не дал).
    # source=device_gps + свежий valid_from → перебивает устаревшие чат-факты по свежести.
    label = name or ("дом" if home else "в поездке")
    try:
        import memory_facts_db as mf
        mf.save_fact("fact", value=label, key="current_location",
                     confidence=0.9, source="device_gps", subject="self")
    except Exception as e:  # noqa: BLE001 — сбой belief-записи не рушит приём координат
        log.warning("save current_location belief упал: %r", e)
    return {"lat": lat, "lon": lon, "is_home": home, "name": name, "label": label}


def latest() -> dict | None:
    """Последний сигнал локации (или None, если сигналов нет)."""
    _hdb._ensure_device_location_table()
    with _hdb.get_conn() as conn:
        row = conn.execute(
            "SELECT lat, lon, name, is_home, source, received_at FROM device_location "
            "WHERE subject='self' ORDER BY received_at DESC, id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def _age_hours(received_at: str) -> float:
    try:
        t = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (get_now(timezone.utc) - t).total_seconds() / 3600.0
    except Exception:  # noqa: BLE001 — нераспарсенный ts → трактуем как устаревший
        return float("inf")


def resolve_place(max_age_h: float = FRESH_MAX_H) -> dict:
    """Резолвер для travel-режима: где пользователь + свежо ли это.

    Возвращает {known, fresh, is_home, lat, lon, name, age_h, source}. Свежесть —
    датчик: сигнал старше max_age_h → fresh=False (travel-режим не доверяет координатам,
    не показывает погоду места по протухшему сигналу). Нет сигнала → known=False."""
    row = latest()
    if not row:
        return {"known": False, "fresh": False, "is_home": None, "lat": None,
                "lon": None, "name": None, "age_h": None, "source": None}
    age = _age_hours(row["received_at"])
    # is_home пересчитываем от координат ТЕКУЩИМ радиусом/якорем (конфиг мог измениться
    # после ингеста; старый столбец device_location.is_home считался старым радиусом).
    # Координаты — источник истины.
    return {"known": True, "fresh": age <= max_age_h,
            "is_home": is_home(row["lat"], row["lon"]),
            "lat": row["lat"], "lon": row["lon"], "name": row["name"],
            "age_h": round(age, 1), "source": row["source"]}


# ── Таймзона тенанта из GPS (для местных расписаний брифа/синка) ─────────────
_TF = None  # кэш TimezoneFinder (грузит ~40МБ данных — инстанс один на процесс)


def _tf_instance():
    global _TF
    if _TF is None:
        try:
            from timezonefinder import TimezoneFinder
            _TF = TimezoneFinder()
        except Exception as e:  # noqa: BLE001 — пакет недоступен → tz по env/UTC
            log.warning("timezonefinder недоступен: %r — tz по env/UTC", e)
            _TF = False  # sentinel: пытались, нет
    return _TF or None


def tenant_timezone() -> str:
    """IANA-таймзона тенанта из СВЕЖЕГО GPS (timezonefinder). Fallback-цепь:
    свежий GPS → дом (home_anchor координаты) → env HEALTH_TZ → UTC. Свежесть —
    FRESH_MAX_H: протухший сигнал → не доверяем координатам, идём на дом (не
    выдумываем место — тот же принцип, что resolve_place). Чистая строка IANA."""
    import os
    tf = _tf_instance()
    if tf is not None:
        p = resolve_place()
        if p.get("known") and p.get("fresh") and p.get("lat") is not None:
            try:
                tz = tf.timezone_at(lat=float(p["lat"]), lng=float(p["lon"]))
                if tz:
                    return tz
            except Exception as e:  # noqa: BLE001 — сбой на GPS → идём на дом
                log.warning("tenant_timezone: tf(GPS) упал: %r", e)
        hlat, hlon, _ = home_anchor()
        if hlat is None or hlon is None:
            # Дом не задан — нормальное состояние новой установки до знакомства; home_anchor
            # уже сказал об этом. Не исключение: до 01.10 здесь падал float(None) и журнал
            # нового человека получал «упал: TypeError» десятки раз за минуту (анонимный урок).
            return os.environ.get("HEALTH_TZ") or "UTC"
        try:
            tz = tf.timezone_at(lat=float(hlat), lng=float(hlon))
            if tz:
                return tz
        except Exception as e:  # noqa: BLE001 — сбой на доме → env/UTC
            log.warning("tenant_timezone: tf(дом) упал: %r", e)
    return os.environ.get("HEALTH_TZ") or "UTC"


# ── Событие смены города (A, 2026-07-15) ────────────────────────────────────
# Геометрия первична: внутри радиуса дома — 'home' (город НЕ смотрим, джиттер пригород↔
# центр гаснет). Вне радиуса — 'away:<город>' (город различает away-места). Событие
# фиксируется РОВНО раз на переход через watermark (последнее объявленное состояние),
# per-tenant (config в health.db тенанта). Watermark двигается ТОЛЬКО после доставки (RYW).

_WATERMARK_KEY = "brief.location_watermark"


def location_state(place: dict) -> str | None:
    """Текущее состояние локации для событийного детектора. ЧИСТАЯ (кроме home_anchor).
    Несвежий/неизвестный сигнал → None (событие только на свежем чтении — не выдумываем
    переход по протухшему). Внутри радиуса дома → 'home'. Вне → 'away:<город>' или
    'away:?' если имя города неизвестно (тогда событие не объявляется)."""
    if not place.get("known") or not place.get("fresh"):
        return None
    lat, lon = place.get("lat"), place.get("lon")
    if lat is None or lon is None:
        return None
    if is_home(lat, lon):
        return "home"
    city = (place.get("name") or "").strip()
    return f"away:{city}" if city else "away:?"


def location_event(current_state: str | None, watermark: str | None) -> dict | None:
    """Событие смены локации. ЧИСТАЯ. Сравнивает текущее состояние с watermark.
      current is None (несвежо/неизвестно)  → нет события, watermark НЕ двигаем.
      current == watermark                   → нет события (уже объявлено).
      current == 'away:?' (город неизвестен) → нет события (§3: нет city → молчим).
      current == 'home' (был away)           → «домой».
      current == 'away:<city>'               → «новый город <city>».
    Первый запуск дома (watermark None) НЕ кричит «домой» на пустом месте.
    Возвращает {kind, city?, text, new_watermark} или None."""
    if current_state is None or current_state == watermark or current_state == "away:?":
        return None
    if current_state == "home":
        if watermark is None or watermark == "home":
            return None  # не объявляем «домой», если и так дома / первый запуск
        return {"kind": "home", "text": "вернулся домой", "new_watermark": "home"}
    city = current_state.split("away:", 1)[1]
    return {"kind": "new_city", "city": city,
            "text": f"новый город — {city}", "new_watermark": current_state}


def get_location_watermark() -> str | None:
    """Последнее объявленное состояние локации (per-tenant). None → ещё не объявляли."""
    try:
        import config_db as _cfg
        v = _cfg.get_config(_WATERMARK_KEY)
        return v if isinstance(v, str) else None
    except Exception as e:  # noqa: BLE001
        log.warning("get_location_watermark упал: %r", e)
        return None


def set_location_watermark(state: str) -> None:
    """Двигать watermark ТОЛЬКО после успешной доставки брифа (RYW). Иначе бриф упал,
    watermark уехал → событие потеряно навсегда."""
    import config_db as _cfg
    _cfg.upsert_config(_WATERMARK_KEY, value_text=state,
                       category="brief", source="location_event")
