"""
env_context.py — Среда С-2: адаптеры источников → карточки + env severity.

severity = seasonal_deviation × vulnerability (§5.8): «жарко» летом дома — не новость,
аномалия — да. Единый vulnerability — настройка модели, не утверждение о здоровье.
Жара гейтится по отклонению от СЕЗОННОЙ нормы домашнего региона (curated, private/region.yaml),
не по абсолюту; нет пакета региона — как в поездке, по абсолютному порогу. UV — редкий
защитный совет (кулдаун сделает ~раз в месяц). Пыль/воздух — по событию (пылевое событие,
плохой AQI). Источник упал → карточки нет (A7: молчим, не фантазируем).

ЖИВОЙ за флагом MORNING_BRIEF_GATE (вкл. в проде оба тенанта, 2026-07). semantic_key:
weather:heat:above_seasonal, weather:uv:high, weather:dust:elevated, air:aqi:elevated.
"""
from __future__ import annotations

import logging

import region_pack
from brief_cards import Card

log = logging.getLogger(__name__)

# Curated сезонная норма макс. температуры домашнего региона по месяцам (°C) — данные региона
# (private/region.yaml, `temp_norm_c`); до 2026-09-23 литерал с городом владельца (pub-prep).
HOME_TEMP_NORM: dict[int, float] = {int(k): v for k, v in (region_pack.value("temp_norm_c", {}) or {}).items()}
_TEMP_SD = 3.5
VULNERABILITY = 1.0  # общий коэффициент модели, не клиническая характеристика тенанта


def _quantize_env(cards: list) -> None:
    """Квантование env-severity (нить profile-staleness, п.2, 2026-08-05).

    env был ЕДИНСТВЕННЫМ провайдером с непрерывной severity (severity = дрейф× уязвимость,
    формулы ниже), а весь остальной код читает severity как ДИСКРЕТНЫЙ класс: гейт по
    theta, шумовой пол Э6, ранг слота. Непрерывность здесь — сырой индекс, протекающий в
    поле-класс; UV 0.46↔0.47 пробивал кулдаун как 'worsened'. Лечим в ИСТОЧНИКЕ, а не
    полом-патчем: непрерывная магнитуда → `relevance` (ей ранжирует select_slots),
    severity → дискретный ярлык (routine=0.5, safety=0.7) как у sleep/calendar/genome.
    Ранг слота сохранён точно (relevance == прежняя severity), severity стала классом,
    и env больше не пробивает кулдаун сам-собой (severity константна per lane). CAP-
    предупреждения уже дискретны (_WARN_SEV) — их не трогаем, они образец."""
    for c in cards:
        if c.severity is None:
            continue
        c.relevance = round(c.severity, 2)
        c.severity = 0.7 if c.lane == "safety" else 0.5

# PM в поездке (C2, 2026-07-15): Open-Meteo location-agnostic. Дома воздух держит наземная
# станция aqicn (from_air), поэтому PM из Open-Meteo эмитим ТОЛЬКО away (иначе двойной сигнал).
# Пороги — ВОЗ-2021 суточные, в конфиг (§9), НЕ хардкод:
# system_config env.pm25_travel_threshold / env.pm10_travel_threshold.
PM25_TRAVEL_DEFAULT = 15.0   # µg/m³ (WHO 2021 24h guideline)
PM10_TRAVEL_DEFAULT = 45.0   # µg/m³ (WHO 2021 24h guideline)


def pm_thresholds() -> tuple:
    """(pm2.5, pm10) пороги travel-воздуха: конфиг (§9) > дефолт ВОЗ-2021."""
    t25, t10 = PM25_TRAVEL_DEFAULT, PM10_TRAVEL_DEFAULT
    try:
        import config_db as _cfg
        t25 = float(_cfg.get_config("env.pm25_travel_threshold", t25))
        t10 = float(_cfg.get_config("env.pm10_travel_threshold", t10))
    except Exception:  # silent-ok: конфиг недоступен → дефолт ВОЗ, безопасно
        pass
    return t25, t10


def from_weather(data: dict, month: int, is_home: bool = True,
                 pm25_thr: float = PM25_TRAVEL_DEFAULT,
                 pm10_thr: float = PM10_TRAVEL_DEFAULT) -> list:
    """Open-Meteo data → карточки жары/UV/пыли.

    Дома: жара по СЕЗОННОЙ норме домашнего региона (аномалия, не абсолют). В поездке: локальной
    сезонной нормы места нет → жара по АБСОЛЮТНОМУ порогу (≥35°C). UV/пыль абсолютны
    всегда (не зависят от места)."""
    cards: list = []
    t = data.get("temp_max")
    if t is not None:
        # Сезонная норма есть только дома и только с пакетом региона; иначе — абсолютный порог.
        norm = HOME_TEMP_NORM.get(month) if is_home else None
        if norm:
            dev = (t - norm) / _TEMP_SD  # сезонное отклонение в σ
            if dev >= 1.0:  # заметно жарче сезонной нормы — вот это новость
                sev = min(0.9, (0.4 + 0.2 * dev) * VULNERABILITY)
                cards.append(Card(provider="env", semantic_key="weather:heat:above_seasonal",
                    lane="safety" if dev >= 2.0 else "routine",
                    origin="third_party", delivery="stable_api", severity=sev,
                    evidence_summary=f"жара {t}°C — выше сезонной нормы (~{norm}°C)",
                    allowed_claims=[f"температура до {t}°C (сезонная норма ~{norm}°C)"]))
        elif t >= 35:  # поездка или нет пакета региона: абсолютный порог «жарко»
            sev = min(0.9, (0.5 + (t - 35) * 0.05) * VULNERABILITY)
            cards.append(Card(provider="env", semantic_key="weather:heat:high_abs",
                lane="safety" if t >= 40 else "routine",
                origin="third_party", delivery="stable_api", severity=round(sev, 2),
                evidence_summary=f"жара {t}°C — высокая",
                allowed_claims=[f"температура до {t}°C"]))

    uv = data.get("uv_max")
    if uv is not None and uv >= 8:  # WHO: 8+ «очень высокий», защита нужна
        cards.append(Card(provider="env", semantic_key="weather:uv:high",
            lane="routine", origin="third_party", delivery="stable_api",
            severity=round(min(0.7, 0.42 + (uv - 8) * 0.05) * VULNERABILITY, 2),
            evidence_summary=f"UV-индекс до {uv} — очень высокий",
            allowed_claims=[f"UV-индекс до {uv}"]))

    dust = data.get("dust")
    if dust is not None and dust >= 40:  # пылевое событие
        sev = min(0.9, (0.5 + dust / 400) * VULNERABILITY)
        cards.append(Card(provider="env", semantic_key="weather:dust:elevated",
            lane="safety" if dust >= 120 else "routine",
            origin="third_party", delivery="stable_api", severity=round(sev, 2),
            evidence_summary=f"{region_pack.value('dust_label', 'пыль в воздухе')} {dust} µg/m³ — повышена",
            allowed_claims=[f"пыль (dust) {dust} µg/m³"]))

    # PM в ПОЕЗДКЕ (C2): pm2.5/pm10 из Open-Meteo (location-agnostic). Дома НЕ эмитим —
    # там воздух держит наземка aqicn (from_air). Порог — ВОЗ-2021 суточные (конфиг §9).
    if not is_home:
        pm25 = data.get("pm25")
        pm10 = data.get("pm10")
        over25 = isinstance(pm25, (int, float)) and pm25 >= pm25_thr
        over10 = isinstance(pm10, (int, float)) and pm10 >= pm10_thr
        if over25 or over10:
            unhealthy = ((isinstance(pm25, (int, float)) and pm25 >= 55)
                         or (isinstance(pm10, (int, float)) and pm10 >= 150))
            over_frac = max((pm25 - pm25_thr) / pm25_thr if over25 else 0.0,
                            (pm10 - pm10_thr) / pm10_thr if over10 else 0.0)
            sev = round(min(0.9, (0.45 + 0.3 * over_frac) * VULNERABILITY), 2)
            parts = ([f"PM2.5 {pm25}"] if isinstance(pm25, (int, float)) else []) \
                + ([f"PM10 {pm10}"] if isinstance(pm10, (int, float)) else [])
            nums = ", ".join(parts)
            cards.append(Card(provider="env", semantic_key="air:pm:elevated",
                lane="safety" if unhealthy else "routine",
                origin="third_party", delivery="stable_api", severity=sev,
                evidence_summary=f"воздух: {nums} µg/m³ — выше нормы",
                allowed_claims=[f"{p} µg/m³" for p in parts]))
    _quantize_env(cards)
    return cards


def from_air(data: dict) -> list:
    """aqicn наземка → карточка плохого воздуха (по AQI)."""
    aqi = data.get("aqi")
    if isinstance(aqi, (int, float)) and aqi >= 100:
        cards = [Card(provider="env", semantic_key="air:aqi:elevated",
            lane="routine", origin="third_party", delivery="stable_api",
            severity=round(min(0.9, 0.42 + aqi / 500), 2),
            evidence_summary=f"AQI {aqi} — воздух хуже обычного (наземка)",
            allowed_claims=[f"индекс качества воздуха AQI {aqi}"])]
        _quantize_env(cards)
        return cards
    return []


# CAP severity → (card severity, полоса). Extreme/Severe = safety (не зовём наружу).
_WARN_SEV = {"Extreme": (0.9, "safety"), "Severe": (0.72, "safety"), "Moderate": (0.45, "routine")}
_COLOR = {"Extreme": "красное", "Severe": "оранжевое", "Moderate": "жёлтое"}


def _warn_active_on(w: dict, target) -> bool:
    """Предупреждение активно на дату target? По onset/expires (даты CAP)."""
    from datetime import datetime

    def _d(s):
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except Exception:
            return None
    on, ex = _d(w.get("onset")), _d(w.get("expires"))
    if on and target < on:
        return False
    if ex and target > ex:
        return False
    return True


def from_warnings(data: dict, target) -> list:
    """Официальные предупреждения MeteoAlarm (фид региона) → карточки. Severe/Extreme → safety-полоса
    (перебивает слот + гасит зов на тропу). Активные на target. Дубли по типу схлопнутся."""
    import re
    cards: list = []
    for w in (data.get("warnings") or []):
        if not _warn_active_on(w, target):
            continue
        sev_name = (w.get("severity") or "Moderate").strip().title()
        sevnum, lane = _WARN_SEV.get(sev_name, (0.45, "routine"))
        event = (w.get("event") or "предупреждение").strip()
        ev_clean = re.sub(r"\s*(Yellow|Orange|Red)\s*$", "", event, flags=re.I).strip() or event
        slug = re.sub(r"[^a-z0-9]+", "_", ev_clean.lower()).strip("_")[:24] or "warning"
        color = _COLOR.get(sev_name, "")
        cards.append(Card(provider="env", semantic_key=f"met:warning:{slug}",
            lane=lane, origin="official", delivery="stable_api", severity=sevnum,
            evidence_summary=f"офиц. предупреждение ({color}): {ev_clean}",
            allowed_claims=[f"MeteoAlarm: {event}"]))
    return cards


# Резерв порогов морской карточки — ЗЕРКАЛО сидов system_config (health_db, решение владельца
# 29.09: пороги — в настройках); равенство держит coherence-тест. Читать через _marine_limits().
_MARINE_FALLBACK = {"env.sea_waves_notable_m": 1.25,   # заметное волнение — не зовём купаться
                    "env.sea_waves_safety_m": 2.5,     # шторм — карточка в полосу безопасности
                    "env.sea_swim_temp_c": 22.0}       # вода комфортна для купания


def _marine_limits() -> dict:
    """Пороги морской карточки тенанта; нет БД — резерв, громко (§14)."""
    out = {}
    for key, fallback in _MARINE_FALLBACK.items():
        try:
            import config_db
            out[key] = float(config_db.get_config(key, fallback))
        except Exception:  # noqa: BLE001 — нет БД/таблицы: резерв, но ГРОМКО
            log.warning("%s недоступен в БД, взят резерв %s (§14)", key, fallback)
            out[key] = fallback
    return out


def from_marine(data: dict) -> list:
    """Море (Open-Meteo Marine, дома-only) → купание/волны. Высокие волны ПЕРЕБИВАЮТ
    приглашение купаться (не зовём в шторм). Влияет на активность (решение 2026-07-14).
    Пороги — system_config тенанта (_marine_limits)."""
    lim = _marine_limits()
    t = data.get("sea_temp")
    w = data.get("wave_max")
    if isinstance(w, (int, float)) and w >= lim["env.sea_waves_notable_m"]:
        return [Card(provider="env", semantic_key="sea:waves:high",
            lane="safety" if w >= lim["env.sea_waves_safety_m"] else "routine",
            origin="third_party", delivery="stable_api",
            severity=round(min(0.9, 0.45 + w / 8), 2),
            evidence_summary=f"море волнуется — волны до {w} м",
            allowed_claims=[f"высота волн до {w} м"])]
    if isinstance(t, (int, float)) and t >= lim["env.sea_swim_temp_c"]:
        return [Card(provider="env", semantic_key="sea:temp:swimmable",
            lane="routine", origin="third_party", delivery="stable_api",
            severity=0.4,
            evidence_summary=f"море ~{t}°C — хороший день поплавать",
            allowed_claims=[f"температура моря ~{t}°C"])]
    return []


def assemble_env_cards(target) -> list:
    """Реальные источники за target → карточки среды, ПО ТЕКУЩЕЙ ЛОКАЦИИ.

    Локация из location_signal.resolve_place() (сигнал с телефона): сигнала НЕ было
    никогда → env молчит; устаревший сигнал НЕ глушит — берём последнюю известную
    локацию. Дома → погода дома + сезонный якорь + aqicn-наземка. В поездке → погода
    МЕСТА по координатам, жара по абсолюту, без наземки (её станция — домашняя). Сбой
    источника → пропуск (A7)."""
    import env_sources as es
    import location_signal as ls

    place = ls.resolve_place()
    if not place.get("known"):
        return []  # сигнала локации НЕ было НИКОГДА → молчим
    # Устаревший сигнал НЕ глушим: берём ПОСЛЕДНЮЮ ИЗВЕСТНУЮ локацию (владелец 2026-07-14:
    # «считать неизвестную = предыдущей, сломается — починим»). freshness — информ-поле.

    is_home = bool(place.get("is_home"))
    # B4 (brief-neutralization): дом-координаты per-tenant из home_anchor (config §9 > дефолт),
    # НЕ owner-константа (удалена 2026-09-23). Консистентно с is_home-детекцией (та тоже через home_anchor).
    _hlat, _hlon, _ = ls.home_anchor()
    lat = _hlat if is_home else place["lat"]
    lon = _hlon if is_home else place["lon"]

    cards: list = []
    om = es.fetch_open_meteo(lat, lon)
    if om.get("ok"):
        _t25, _t10 = pm_thresholds()
        cards += from_weather(om.get("data") or {}, target.month, is_home=is_home,
                              pm25_thr=_t25, pm10_thr=_t10)
    if is_home:  # aqicn-наземка + море + офиц. предупреждения — только дома (фид и берег региона)
        aq = es.fetch_aqicn()
        if aq.get("ok"):
            cards += from_air(aq.get("data") or {})
        mar = es.fetch_marine(_hlat, _hlon)
        if mar.get("ok"):
            cards += from_marine(mar.get("data") or {})
        warn = es.fetch_met_warnings()
        if warn.get("ok"):
            cards += from_warnings(warn.get("data") or {}, target)
    return cards


def env_probe() -> dict:
    """Лёгкий зонд среды (C1, 2026-07-15) — НЕ эмитит карточки, только СТАТУС проверки.
    Различает «нет локации / фетч упал» (тишина оправдана) от «проверено, всё чисто»
    (в поездке можно сказать «спокойно»). Иначе бриф при пустой среде сочиняет
    «данных по среде не поступало», хотя данные пришли — просто ниже порогов.

    Возвращает {known, reachable, away, place}. reachable=True → основной источник
    (Open-Meteo, location-agnostic) ответил. Тот же файловый кэш, что и assemble_env_cards
    → повторный вызов в одном брифе не бьёт по сети.
    """
    import env_sources as es
    import location_signal as ls
    place = ls.resolve_place()
    if not place.get("known"):
        return {"known": False, "reachable": False, "away": False, "place": None}
    is_home = bool(place.get("is_home"))
    _hlat, _hlon, _ = ls.home_anchor()  # B4: per-tenant дом (config §9 > дефолт), не owner-константа
    lat = _hlat if is_home else place.get("lat")
    lon = _hlon if is_home else place.get("lon")
    om = es.fetch_open_meteo(lat, lon)
    return {"known": True, "reachable": bool(om.get("ok")),
            "away": not is_home, "place": place.get("name")}
