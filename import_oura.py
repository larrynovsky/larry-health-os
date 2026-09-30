#!/usr/bin/env python3.11
"""
Импорт данных Oura Ring через API v2.
Сохраняет в health/data/daily_metrics/YYYY-MM-DD.json (merge).
Запуск: python3.11 import_oura.py [days=30] [start=YYYY-MM-DD]
"""

import json
import os as _os
import sys
import logging
import health_db as _hdb_status
import urllib.request
import urllib.error
from datetime import date, timedelta
from pathlib import Path

# ── Пути ──────────────────────────────────────────────────────────────────
import infra_config   # дом облачного пути (BL-PUB-12)
_HEALTH_DIR = Path(_os.environ.get("HEALTH_DATA_DIR", str(infra_config.cloud_dir())))
# tenant-aware суффикс лога (2026-07-03): owner → "", партнёр → "_partner".
# Раньше оба тенанта писали в общий ~/health_oura_import.log — мешало диагностике.
_LOG_SFX = "" if _HEALTH_DIR.name == "health" else "_" + _HEALTH_DIR.name.replace("health_", "")
METRICS_DIR = _HEALTH_DIR / "data" / "daily_metrics"
# per-tenant: токен из HEALTH_SECRETS_DIR через единый резолвер secrets_dir()
# (ленивый вызов в get_token — иначе fail-closed рейзил бы на импорте). 2026-07-02.
from secrets_paths import secrets_dir

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(Path.home() / f"health_oura_import{_LOG_SFX}.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


# ── API ────────────────────────────────────────────────────────────────────

def get_token():
    # secrets_dir() — единый резолвер; он же fail-closed для тенанта без secrets
    # (иначе взяли бы токен владельца → доказанная кросс-тенант утечка 2026-07-03).
    return (secrets_dir() / "oura_token").read_text().strip()


def oura_get(endpoint: str, start: str, end: str) -> list:
    """W5K-A2 (2026-05-14): follow next_token пагинацию до конца.

    Sleep endpoint имеет до ~700 сессий на страницу. При больших окнах
    мы молча теряли страницы 2+. Теперь цикл по next_token, max 20 страниц
    (предохранитель от бесконечного цикла).
    """
    token = get_token()
    base = f"https://api.ouraring.com/v2/usercollection/{endpoint}?start_date={start}&end_date={end}"
    out: list = []
    next_token = None
    for page in range(20):
        url = base + (f"&next_token={next_token}" if next_token else "")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                payload = json.loads(r.read())
        except urllib.error.HTTPError as e:
            log.error(f"Oura API error {e.code} on {endpoint} (page {page}): {e.read()}")
            return out
        out.extend(payload.get("data", []))
        next_token = payload.get("next_token")
        if not next_token:
            return out
    log.warning(f"oura_get({endpoint}): hit 20-page limit, returning {len(out)} items")
    return out


# ── Парсинг ────────────────────────────────────────────────────────────────

def _hours(secs):
    """Секунды → часы, сохраняя различие «поля нет» (None) и «прибор измерил ноль» (0.0)."""
    return None if secs is None else round(secs / 3600, 3)


def parse_sleep(sessions: list, scores: list) -> dict:
    """
    Возвращает dict[date_str → sleep_summary].
    Берём только long_sleep сессии как основной ночной сон.
    """
    score_map = {s["day"]: s for s in scores}
    result = {}

    for s in sessions:
        if s.get("type") not in ("long_sleep", "sleep"):
            continue
        total = s.get("total_sleep_duration", 0)
        if total < 3600:  # < 1 часа — пропускаем обрывки
            continue

        day = s["day"]
        existing = result.get(day, {})

        # Берём сессию с максимальной продолжительностью за день
        if existing and existing.get("total_sec", 0) >= total:
            continue

        # Провенанс (2026-07-31): дефолт 0 превращал ОТСУТСТВИЕ стадии в измеренный ноль,
        # и COUNT() считал его данными. Отсутствует → None → колонка не пишется (metrics_db
        # фильтрует `is not None`). Явный ноль от прибора остаётся нулём и отличим от дыры.
        deep = s.get("deep_sleep_duration")
        rem = s.get("rem_sleep_duration")
        light = s.get("light_sleep_duration")
        awake = s.get("awake_time")
        bedtime_start = s.get("bedtime_start", "")
        bedtime_end = s.get("bedtime_end", "")

        sc = score_map.get(day, {})
        contributors = sc.get("contributors", {})

        result[day] = {
            "total_sec": total,
            "source": "Oura",
            "totalSleep": round(total / 3600, 3),
            "inBed": _hours(total + awake) if awake is not None else None,
            "deep": _hours(deep),
            "rem": _hours(rem),
            "core": _hours(light),
            "awake": _hours(awake),
            "sleepStart": bedtime_start,
            "sleepEnd": bedtime_end,
            "inBedStart": bedtime_start,
            "inBedEnd": bedtime_end,
            "sleep_score": sc.get("score"),
            "contributors": {
                "deep_sleep": contributors.get("deep_sleep"),
                "rem_sleep": contributors.get("rem_sleep"),
                "efficiency": contributors.get("efficiency"),
                "timing": contributors.get("timing"),
                "restfulness": contributors.get("restfulness"),
                "total_sleep": contributors.get("total_sleep"),
            },
        }

    return result


def parse_readiness(data: list) -> dict:
    result = {}
    for r in data:
        c = r.get("contributors", {})
        result[r["day"]] = {
            "readiness_score": r.get("score"),
            "hrv_balance": c.get("hrv_balance"),
            "body_temperature": c.get("body_temperature"),
            "recovery_index": c.get("recovery_index"),
            "resting_hr_contrib": c.get("resting_heart_rate"),
        }
    return result


def parse_activity(data: list) -> dict:
    result = {}
    for a in data:
        result[a["day"]] = {
            "steps": a.get("steps"),
            "active_kcal": a.get("active_calories"),
            "total_kcal": a.get("total_calories"),
            "distance_km": round(a.get("equivalent_walking_distance", 0) / 1000, 2) if a.get("equivalent_walking_distance") else None,
            "activity_score": a.get("score"),
        }
    return result


def parse_spo2(data: list) -> dict:
    """daily_spo2: spo2_percentage.average (число %), breathing_disturbance_index.

    W5K-A1 (2026-05-14): починен путь до spo2 (был `s["average"]["percentage"]`,
    реальная структура — `s["spo2_percentage"]["average"]`).
    """
    result = {}
    for s in data:
        breathe = s.get("breathing_disturbance_index")
        spo2_pct = s.get("spo2_percentage") or {}
        avg = spo2_pct.get("average") if isinstance(spo2_pct, dict) else None
        result[s["day"]] = {
            "spo2": {
                "avg": avg,
                "min": None,
                "unit": "%",
                "source": "Oura",
            },
            "breathing_disturbance": breathe,
        }
    return result


def parse_hrv(sessions: list) -> dict:
    """HRV из ночных сессий — берём average_hrv из long_sleep."""
    result = {}
    for s in sessions:
        if s.get("type") != "long_sleep":
            continue
        hrv = s.get("average_hrv")
        if hrv:
            result[s["day"]] = {
                "hrv": {"avg": round(hrv, 1), "unit": "ms", "source": "Oura"},
                "resting_heart_rate": {
                    "value": round(s.get("average_heart_rate", 0), 1) if s.get("average_heart_rate") else None,
                    "unit": "count/min",
                    "source": "Oura",
                },
            }
    return result


def parse_stress(data: list) -> dict:
    """daily_stress: day_summary, stress_high (сек), recovery_high (сек)."""
    result = {}
    for s in data:
        result[s["day"]] = {
            "stress": {
                "day_summary":   s.get("day_summary"),
                "stress_high":   s.get("stress_high"),
                "recovery_high": s.get("recovery_high"),
            }
        }
    return result


def parse_resilience(data: list) -> dict:
    """daily_resilience: level + contributors."""
    result = {}
    for r in data:
        c = r.get("contributors") or {}
        result[r["day"]] = {
            "resilience": {
                "level": r.get("level"),
                "contributors": {
                    "sleep_recovery":    c.get("sleep_recovery"),
                    "daytime_recovery":  c.get("daytime_recovery"),
                    "stress":            c.get("stress"),
                },
            }
        }
    return result


def parse_workouts(data: list) -> list[dict]:
    """workout: список тренировок с датой."""
    result = []
    for w in data:
        start = w.get("start_datetime") or ""
        end   = w.get("end_datetime") or ""
        dur_s = w.get("duration")
        dist  = w.get("distance")
        result.append({
            "date":          w.get("day"),
            "activity_type": w.get("activity"),
            "start_time":    start,
            "end_time":      end,
            "duration_min":  round(dur_s / 60, 1) if dur_s else None,
            "distance_km":   round(dist / 1000, 2) if dist else None,
            "calories":      w.get("calories"),
            "avg_hr":        w.get("average_heart_rate"),
            "max_hr":        w.get("max_heart_rate"),
            "source":        "Oura",
        })
    return result


# ── Сохранение ─────────────────────────────────────────────────────────────

def load_day(d: str) -> dict:
    p = METRICS_DIR / f"{d}.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_day(d: str, data: dict):
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    existing = load_day(d)
    existing.update(data)
    path = METRICS_DIR / f"{d}.json"
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2))


# ── Main ───────────────────────────────────────────────────────────────────

def import_oura(start: date, end: date):
    start_s = start.isoformat()
    end_s = end.isoformat()
    log.info(f"Импорт Oura: {start_s} → {end_s}")

    # Получаем все данные
    sleep_sessions  = oura_get("sleep", start_s, end_s)
    sleep_scores    = oura_get("daily_sleep", start_s, end_s)
    readiness_data  = oura_get("daily_readiness", start_s, end_s)
    activity_data   = oura_get("daily_activity", start_s, end_s)
    spo2_data       = oura_get("daily_spo2", start_s, end_s)
    stress_data     = oura_get("daily_stress", start_s, end_s)
    resilience_data = oura_get("daily_resilience", start_s, end_s)
    workout_data    = oura_get("workout", start_s, end_s)

    # Парсим
    sleep_map      = parse_sleep(sleep_sessions, sleep_scores)
    readiness_map  = parse_readiness(readiness_data)
    activity_map   = parse_activity(activity_data)
    spo2_map       = parse_spo2(spo2_data)
    hrv_map        = parse_hrv(sleep_sessions)
    stress_map     = parse_stress(stress_data)
    resilience_map = parse_resilience(resilience_data)
    workouts_list  = parse_workouts(workout_data)

    # Мёрджим по дням
    all_days = (
        set(sleep_map) | set(readiness_map) | set(activity_map)
        | set(spo2_map) | set(hrv_map) | set(stress_map) | set(resilience_map)
    )
    saved = 0
    for day in sorted(all_days):
        merged = {}
        if day in sleep_map:
            merged["sleep"] = sleep_map[day]
            merged["sleep"].pop("total_sec", None)
        if day in readiness_map:
            merged.update(readiness_map[day])
        if day in activity_map:
            for k, v in activity_map[day].items():
                if v is not None:
                    merged[k] = v
        if day in spo2_map:
            merged.update(spo2_map[day])
        if day in hrv_map:
            merged.update(hrv_map[day])
        if day in stress_map:
            merged.update(stress_map[day])
        if day in resilience_map:
            merged.update(resilience_map[day])

        save_day(day, merged)
        # Сразу пишем в SQLite — не ждём отдельного migrate_all_json
        try:
            from health_db import upsert_metrics_from_json as _upsert
            _upsert(day, merged, source="Oura")
        except Exception as e:
            log.warning(f"upsert SQLite {day}: {e}")
        saved += 1

    # Сохраняем тренировки
    if workouts_list:
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).parent))
            from health_db import upsert_workout
            for w in workouts_list:
                if w.get("date"):
                    upsert_workout(w["date"], w)
            log.info(f"Тренировок сохранено: {len(workouts_list)}")
        except Exception as e:
            log.warning(f"Ошибка сохранения тренировок: {e}")

    _hdb_status.set_import_status("oura")
    log.info(f"Сохранено/обновлено дней: {saved}")
    return saved


if __name__ == "__main__":
    if len(sys.argv) > 1 and "-" in sys.argv[1]:
        # start=YYYY-MM-DD
        start = date.fromisoformat(sys.argv[1])
        end = date.today()
    else:
        days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
        end = date.today()
        start = end - timedelta(days=days)

    import_oura(start, end)
