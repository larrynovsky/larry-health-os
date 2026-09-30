#!/usr/bin/env python3.11
"""
Health Auto Export JSON importer.
Handles:
  - Big historical export (single JSON with years of data)
  - Daily files from New Automation/ folder (one JSON per day)

Saves aggregated daily summaries to:
  ~/iCloud/health/data/daily_metrics/YYYY-MM-DD.json
"""

from _time_inject import get_today  # seam
import json
import os
import shutil
import sys
import glob
import logging
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict

# ── Paths ──────────────────────────────────────────────────────────────────
import os as _os
import infra_config   # дом облачного пути и папки приложения HAE (BL-PUB-12)
_HEALTH_DIR = Path(_os.environ.get('HEALTH_DATA_DIR', str(infra_config.cloud_dir())))
# tenant-aware суффикс лога (2026-07-03): owner → "", партнёр → "_partner".
_LOG_SFX = "" if _HEALTH_DIR.name == "health" else "_" + _HEALTH_DIR.name.replace("health_", "")
HEALTH_DATA = _HEALTH_DIR / "data" / "daily_metrics"
HAE_DAILY_DIR = infra_config.HAE_APP_DIR
HAE_ARCHIVE_DIR = infra_config.cloud_dir("data", "hae_archive")


def historical_export():
    """Самая большая полная выгрузка HealthAutoExport в облачной папке установки — или None.

    До 27.09 здесь стояло имя файла владельца с датами его выгрузки (BL-PUB-16 д): у любой
    другой установки путь не существовал, и работал только запасной поиск ниже — он и стал правилом."""
    candidates = list(infra_config.cloud_dir().glob("HealthAutoExport_*/HealthAutoExport-*.json"))
    return max(candidates, key=lambda p: p.stat().st_size) if candidates else None

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(Path.home() / f"health_apple_import{_LOG_SFX}.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────

def _keep_none(v):
    """Округляет число, сохраняя различие «поля нет» (None) и «прибор измерил ноль» (0.0)."""
    return None if v is None else round(v, 3)


def parse_hae_date(date_str):
    """Parse HAE date string '2026-01-01 08:00:00 +0000' → date object."""
    if not date_str:
        return None
    try:
        # strip timezone offset, parse date part only
        return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except Exception:
        return None


# метрика HAE → ключ дня (он же колонка daily_metrics); среднее за день по уникальным замерам
_FITNESS_DAILY_MEAN = {
    "cardio_recovery":                  "cardio_recovery_bpm",   # count/min
    "six_minute_walking_test_distance": "six_min_walk_m",        # m, вносится вручную
    "stair_speed_up":                   "stair_speed_up_ms",     # m/s
    "stair_speed_down":                 "stair_speed_down_ms",   # m/s
}


# состав тела весов Fitdays через Apple Health → колонка daily_metrics (только в пустые)
_BODYCOMP_MORNING = {
    "body_fat_percentage": "body_fat_pct",   # %
    "body_mass_index":     "bmi",
}


def aggregate_metric_by_day(metric_name, entries, daily):
    """
    Aggregate metric entries into daily buckets.
    daily: dict[date_str → dict]
    """
    for entry in entries:
        d = parse_hae_date(entry.get("date"))
        if not d:
            continue
        ds = str(d)
        if ds not in daily:
            daily[ds] = {}

        bucket = daily[ds]

        if metric_name == "sleep_analysis":
            # Keep the richest sleep record (longest totalSleep)
            prev = bucket.get("sleep")
            total = entry.get("totalSleep", 0) or 0
            if prev is None or total > prev.get("totalSleep", 0):
                bucket["sleep"] = {
                    "inBedStart": entry.get("inBedStart"),
                    "inBedEnd": entry.get("inBedEnd"),
                    "sleepStart": entry.get("sleepStart"),
                    "sleepEnd": entry.get("sleepEnd"),
                    "totalSleep": round(total, 3),
                    # Отсутствие поля нельзя превращать в измеренный ноль:
                    # источник может вообще не измерять стадии сна.
                    # Нет поля → None, иначе корреляции получат вымышленные данные.
                    "inBed":  _keep_none(entry.get("inBed")),
                    "deep":   _keep_none(entry.get("deep")),
                    "rem":    _keep_none(entry.get("rem")),
                    "core":   _keep_none(entry.get("core")),
                    "awake":  _keep_none(entry.get("awake")),
                    "source": entry.get("source", ""),
                }

        elif metric_name == "heart_rate":
            prev = bucket.get("heart_rate", {})
            cur_min = entry.get("Min") or entry.get("qty")
            cur_avg = entry.get("Avg") or entry.get("qty")
            cur_max = entry.get("Max") or entry.get("qty")
            if cur_avg is not None:
                bucket["heart_rate"] = {
                    "min": min(prev.get("min", 9999), cur_min) if cur_min else prev.get("min"),
                    "avg": round((prev.get("avg", cur_avg) + cur_avg) / 2, 1) if prev.get("avg") else cur_avg,
                    "max": max(prev.get("max", 0), cur_max) if cur_max else prev.get("max"),
                    "unit": "count/min",
                }

        elif metric_name == "resting_heart_rate":
            qty = entry.get("qty")
            if qty is not None:
                prev = bucket.get("resting_heart_rate")
                bucket["resting_heart_rate"] = {
                    "value": round((prev["value"] + qty) / 2, 1) if prev else qty,
                    "unit": "count/min",
                }

        elif metric_name == "heart_rate_variability":
            qty = entry.get("qty")
            if qty is not None:
                prev_list = bucket.setdefault("hrv_samples", [])
                prev_list.append(qty)
                bucket["hrv"] = {
                    "avg": round(sum(prev_list) / len(prev_list), 2),
                    "unit": "ms",
                }

        elif metric_name == "blood_oxygen_saturation":
            qty = entry.get("qty")
            if qty is not None:
                prev_list = bucket.setdefault("spo2_samples", [])
                prev_list.append(qty)
                bucket["spo2"] = {
                    "avg": round(sum(prev_list) / len(prev_list), 1),
                    "min": round(min(prev_list), 1),
                    "unit": "%",
                }

        elif metric_name == "step_count":
            qty = entry.get("qty", 0) or 0
            bucket["steps"] = round(bucket.get("steps", 0) + qty)

        elif metric_name == "walking_running_distance":
            qty = entry.get("qty", 0) or 0
            bucket["distance_km"] = round(bucket.get("distance_km", 0) + qty, 3)

        elif metric_name == "active_energy":
            qty = entry.get("qty", 0) or 0
            bucket["active_kcal"] = round(bucket.get("active_kcal", 0) + qty, 1)

        elif metric_name == "basal_energy_burned":
            qty = entry.get("qty", 0) or 0
            bucket["basal_kcal"] = round(bucket.get("basal_kcal", 0) + qty, 1)

        elif metric_name == "vo2_max":
            qty = entry.get("qty")
            if qty is not None:
                bucket["vo2_max"] = {"value": round(qty, 2), "unit": "ml/(kg·min)"}

        elif metric_name == "weight_body_mass":
            qty = entry.get("qty")
            if qty is not None:
                bucket["weight_kg"] = round(qty, 2)

        elif metric_name == "blood_pressure_systolic":
            qty = entry.get("qty")
            if qty is not None:
                prev_list = bucket.setdefault("bp_sys_samples", [])
                prev_list.append(qty)
                bucket["bp_systolic"] = round(sum(prev_list) / len(prev_list), 1)
                bucket["bp_systolic_max"] = round(max(prev_list), 1)

        elif metric_name == "blood_pressure_diastolic":
            qty = entry.get("qty")
            if qty is not None:
                prev_list = bucket.setdefault("bp_dia_samples", [])
                prev_list.append(qty)
                bucket["bp_diastolic"] = round(sum(prev_list) / len(prev_list), 1)
                bucket["bp_diastolic_max"] = round(max(prev_list), 1)

        elif metric_name == "blood_pressure":
            # Форма тонометра (через HAE, замер 26.09): ОДНА метрика, поля
            # systolic/diastolic в записи, без qty. Раздельные имена выше не пришли ни разу —
            # давление терялось с апреля. Решение владельца 26.09 (вариант Б): колонка = среднее
            # за день (вывод о давлении), максимум рядом в raw (на него смотрит тревога).
            # Один замер повторяется в нескольких выгрузках → считаем по времени замера один раз.
            sys_v, dia_v = entry.get("systolic"), entry.get("diastolic")
            if sys_v is None or dia_v is None:
                continue
            seen = bucket.setdefault("_bp_seen", [])
            if entry.get("date") in seen:
                continue
            seen.append(entry.get("date"))
            s = bucket.setdefault("bp_sys_samples", [])
            d_ = bucket.setdefault("bp_dia_samples", [])
            s.append(sys_v)
            d_.append(dia_v)
            bucket["bp_systolic"] = round(sum(s) / len(s), 1)
            bucket["bp_diastolic"] = round(sum(d_) / len(d_), 1)
            bucket["bp_systolic_max"] = round(max(s), 1)
            bucket["bp_diastolic_max"] = round(max(d_), 1)

        elif metric_name == "respiratory_rate":
            qty = entry.get("qty")
            if qty is not None:
                prev_list = bucket.setdefault("resp_rate_samples", [])
                prev_list.append(qty)
                bucket["respiratory_rate"] = {
                    "avg": round(sum(prev_list) / len(prev_list), 1),
                    "unit": "breaths/min",
                }

        elif metric_name == "walking_heart_rate_average":
            qty = entry.get("qty")
            if qty is not None:
                bucket["walking_hr"] = round(qty, 1)

        elif metric_name == "time_in_daylight":
            qty = entry.get("qty", 0) or 0
            bucket["daylight_min"] = round(bucket.get("daylight_min", 0) + qty, 1)

        elif metric_name == "flights_climbed":
            qty = entry.get("qty", 0) or 0
            bucket["flights"] = round(bucket.get("flights", 0) + qty)

        elif metric_name == "mindful_minutes":
            qty = entry.get("qty", 0) or 0
            bucket["mindful_min"] = round(bucket.get("mindful_min", 0) + qty, 1)

        # ── Модуль 2: расширенные метрики (2026-06-01) ─────────────────────────
        elif metric_name == "cycling_distance":
            qty = entry.get("qty", 0) or 0
            bucket["cycling_km"] = round(bucket.get("cycling_km", 0) + qty, 3)

        elif metric_name == "apple_exercise_time":
            # iPhone/Watch считает активные минуты (кольцо «Упражнения»)
            qty = entry.get("qty", 0) or 0
            bucket["exercise_min"] = round(bucket.get("exercise_min", 0) + qty, 1)

        elif metric_name == "apple_stand_time":
            qty = entry.get("qty", 0) or 0
            bucket["stand_min"] = round(bucket.get("stand_min", 0) + qty, 1)

        elif metric_name == "walking_speed":
            qty = entry.get("qty")
            if qty is not None:
                bucket.setdefault("_walking_speed_samples", []).append(qty)
                s = bucket["_walking_speed_samples"]
                bucket["walking_speed_avg"] = round(sum(s) / len(s), 3)

        elif metric_name == "walking_step_length":
            qty = entry.get("qty")
            if qty is not None:
                bucket.setdefault("_step_length_samples", []).append(qty)
                s = bucket["_step_length_samples"]
                bucket["walking_step_length_avg"] = round(sum(s) / len(s), 1)

        elif metric_name == "walking_asymmetry_percentage":
            qty = entry.get("qty")
            if qty is not None:
                bucket.setdefault("_asymmetry_samples", []).append(qty)
                s = bucket["_asymmetry_samples"]
                bucket["walking_asymmetry_avg"] = round(sum(s) / len(s), 1)

        elif metric_name == "walking_double_support_percentage":
            # Реестр с июня звал её handled (Module 2), ветки не было (замер 26.09) — гейт-маркер
            # того же класса, что асимметрия: среднее за день, только raw.
            qty = entry.get("qty")
            if qty is not None:
                bucket.setdefault("_double_support_samples", []).append(qty)
                s = bucket["_double_support_samples"]
                bucket["walking_double_support_avg"] = round(sum(s) / len(s), 1)

        elif metric_name in _FITNESS_DAILY_MEAN:
            # Выносливость/подвижность — среднее за день.
            # Один замер может прийти в нескольких выгрузках:
            # считаем его по времени один раз, как давление.
            qty = entry.get("qty")
            if qty is None:
                continue
            key = _FITNESS_DAILY_MEAN[metric_name]
            seen = bucket.setdefault(f"_{key}_seen", [])
            if entry.get("date") in seen:
                continue
            seen.append(entry.get("date"))
            s = bucket.setdefault(f"_{key}_samples", [])
            s.append(qty)
            bucket[key] = round(sum(s) / len(s), 3)

        elif metric_name in _BODYCOMP_MORNING:
            # Для жира и ИМТ семантика import_fitdays: утро — раннее взвешивание.
            # Независимо придуманный пример: 13:17 +0000 и 15:17 +0200 —
            # один момент. Сравниваем моменты, а не строки.
            # В базу — только в пустые ячейки (metrics_db, COALESCE).
            qty = entry.get("qty")
            try:
                t = datetime.strptime(entry.get("date") or "", "%Y-%m-%d %H:%M:%S %z")
            except ValueError:
                continue
            if qty is None:
                continue
            key = _BODYCOMP_MORNING[metric_name]
            prev = bucket.get(f"_{key}_t")
            if prev is None or t < prev:
                bucket[f"_{key}_t"] = t
                bucket[key] = round(qty, 1)

        elif metric_name == "physical_effort":
            # MET (метаболический эквивалент) — среднее за день
            qty = entry.get("qty")
            if qty is not None:
                bucket.setdefault("_met_samples", []).append(qty)
                s = bucket["_met_samples"]
                bucket["met_avg"] = round(sum(s) / len(s), 2)


def process_hae_json(filepath):
    """
    Parse a Health Auto Export JSON file.
    Returns dict[date_str → daily_summary].
    """
    log.info(f"Processing: {filepath}")
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)

    daily = {}
    metrics = data.get("data", {}).get("metrics", [])
    log.info(f"  Found {len(metrics)} metrics")

    for metric in metrics:
        name = metric.get("name", "")
        entries = metric.get("data", [])
        aggregate_metric_by_day(name, entries, daily)

    # clean up helper sample arrays from output
    _sample_keys = [
        "hrv_samples", "spo2_samples", "resp_rate_samples",
        "bp_sys_samples", "bp_dia_samples", "_bp_seen",
        "_walking_speed_samples", "_step_length_samples",
        "_asymmetry_samples", "_met_samples", "_double_support_samples",
    ] + [f"_{k}_{t}" for k in _FITNESS_DAILY_MEAN.values() for t in ("seen", "samples")] \
      + [f"_{k}_t" for k in _BODYCOMP_MORNING.values()]
    for ds, bucket in daily.items():
        for k in _sample_keys:
            bucket.pop(k, None)

    return daily


def save_daily_summaries(daily, mode="merge"):
    """
    Save daily summaries to HEALTH_DATA/YYYY-MM-DD.json.
    mode='merge': merge with existing file (new data wins on key conflicts)
    mode='overwrite': replace existing file

    Атомарная запись: temp-файл в той же директории → os.replace() → финальный файл.
    Гарантирует отсутствие частичного состояния при сбое (§7.3.1 Таненбаум).
    """
    HEALTH_DATA.mkdir(parents=True, exist_ok=True)
    saved = 0
    for ds, summary in sorted(daily.items()):
        if not summary:
            continue
        out_path = HEALTH_DATA / f"{ds}.json"
        if mode == "merge" and out_path.exists():
            with open(out_path) as f:
                existing = json.load(f)
            # UC-A-03: filter None, чтобы HAE null не затирал Oura-данные.
            filtered = {k: v for k, v in summary.items() if v is not None}
            # Variant B guard (2026-06-01): не перезаписывать Oura-owned ключи
            # если они уже есть в файле. Apple Health данные хранятся под
            # ключом apple_health, а не в flat-пространстве raw JSON.
            _OURA_OWNED = {
                "hrv", "sleep", "resting_heart_rate", "spo2", "stress",
                "resilience", "readiness_score", "hrv_balance",
                "body_temperature", "recovery_index", "breathing_disturbance",
            }
            for k in _OURA_OWNED:
                if k in existing and existing[k] is not None:
                    filtered.pop(k, None)
            existing.update(filtered)
            summary = existing
        # Атомарная запись: temp в той же директории (same filesystem)
        tmp_path = HEALTH_DATA / f".tmp_{ds}.json"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
            os.replace(str(tmp_path), str(out_path))  # атомарно на POSIX
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

        # W5K-B3: также пишем в SQLite с source='AppleHealth' — без ожидания migrate_all_json
        try:
            from health_db import upsert_metrics_from_json as _upsert
            _upsert(ds, summary, source="AppleHealth")
        except Exception as e:
            log.warning(f"  sqlite upsert {ds}: {e}")

        saved += 1
    log.info(f"Saved {saved} daily summaries to {HEALTH_DATA}")
    return saved


def import_historical():
    """Import the historical JSON export."""
    path = historical_export()
    if path is None:
        log.warning("Historical JSON not found")
        return 0

    log.info(f"Importing historical data from: {path.name} ({path.stat().st_size // 1024}KB)")
    daily = process_hae_json(path)
    log.info(f"  Parsed {len(daily)} days of data")
    return save_daily_summaries(daily, mode="merge")


def import_daily_new_automation(archive=True):
    """Import new daily JSON files from HAE New Automation folder."""
    if not HAE_DAILY_DIR.exists():
        log.warning(f"New Automation folder not found: {HAE_DAILY_DIR}")
        return 0

    files = sorted(HAE_DAILY_DIR.glob("HealthAutoExport-*.json"))
    if not files:
        log.info("No new files in New Automation/")
        return 0

    log.info(f"Found {len(files)} daily file(s) in New Automation/")
    total = 0
    for f in files:
        dest = HAE_ARCHIVE_DIR / f.name
        if dest.exists():
            log.info(f"  Skipping (already imported): {f.name}")
            continue
        try:
            daily = process_hae_json(f)
        except OSError as e:
            import errno as _errno
            if e.errno == _errno.EDEADLK:
                # iCloud placeholder — принудительно скачиваем и повторяем
                log.info(f"  EDEADLK: принудительное скачивание {f.name}")
                import subprocess as _sp
                _sp.run(["brctl", "download", str(f)],
                        capture_output=True, timeout=60)
                import time as _t; _t.sleep(3)
                try:
                    daily = process_hae_json(f)
                except OSError as e2:
                    log.warning(f"  Skipping после download: {f.name} — {e2}")
                    continue
            else:
                log.warning(f"  Skipping (iCloud): {f.name} — {e}")
                continue
        total += save_daily_summaries(daily, mode="merge")
        if archive:
            HAE_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(str(f), str(dest))
                log.info(f"  Archived → {dest.name}")
            except (FileNotFoundError, OSError) as e:
                log.warning(f"  Archive copy failed (iCloud): {f.name} — {e}")

    return total


def print_summary():
    """Print recent daily summaries to verify import."""
    files = sorted(HEALTH_DATA.glob("*.json"))[-5:]
    print(f"\n{'='*60}")
    print(f"Последние {len(files)} дней:")
    for fp in files:
        with open(fp) as f:
            d = json.load(f)
        date_str = fp.stem
        parts = []
        if "steps" in d: parts.append(f"шаги: {int(d['steps'])}")
        if "heart_rate" in d: parts.append(f"ЧСС: {d['heart_rate'].get('avg')} уд/мин")
        if "hrv" in d: parts.append(f"ВСР: {d['hrv'].get('avg')} мс")
        if "spo2" in d: parts.append(f"SpO2: {d['spo2'].get('avg')}%")
        if "sleep" in d: parts.append(f"сон: {d['sleep'].get('totalSleep')}ч")
        if "weight_kg" in d: parts.append(f"вес: {d['weight_kg']}кг")
        if "bp_systolic" in d:
            dia = d.get("bp_diastolic", 0)
            parts.append(f"АД: {d['bp_systolic']:.0f}/{dia:.0f}")
        print(f"  {date_str}: {', '.join(parts) or '(нет данных)'}")
    print(f"{'='*60}\n")


# ── Main ───────────────────────────────────────────────────────────────────

def _export_is_fresh(max_age_days: int = 2) -> bool:
    """True, если телефон ещё экспортирует биометрию: есть HAE-файл за последние max_age_days.

    Дедуп с Oura (осознанное решение): дублирующие Oura метрики из apple_health НЕ импортируются,
    поэтому свежие HAE-файлы дают 0 новых записей. Это НОРМА, не обрыв. Свежесть apple_health
    меряем по факту «телефон ещё шлёт экспорт», а не по числу новых НЕ-Oura дней. Нет свежих
    файлов вообще → реальный обрыв экспорта (телефон/приложение), и сенсор обязан это видеть.
    """
    from datetime import date as _date, timedelta as _td
    import re as _re
    cutoff = (get_today() - _td(days=max_age_days)).isoformat()
    for d in (HAE_DAILY_DIR, HAE_ARCHIVE_DIR):
        if not d.exists():
            continue
        for f in d.glob("HealthAutoExport-*.json"):
            m = _re.search(r"(\d{4}-\d{2}-\d{2})", f.name)
            if m and m.group(1) >= cutoff:
                return True
    return False


def _mark_apple_health_fresh() -> None:
    import health_db as _hdb_s
    _hdb_s.set_import_status("apple_health")


if __name__ == "__main__":
    # Tenant-safety (2026-07-02): источник Apple Health — ХАРДКОД владельца (его
    # iCloud HealthExport/HISTORICAL_JSON). Запись в чужой тенант = утечка данных
    # владельца (инцидент: Oura/AppleHealth владельца осели в БД партнёра). Разрешаем
    # импорт ТОЛЬКО для owner-тенанта (каталог .../health); у партнёра своего
    # apple-health источника нет → выходим.
    if _HEALTH_DIR.name != "health":
        log.warning(f"apple-health: источник принадлежит владельцу; тенант "
                    f"'{_HEALTH_DIR.name}' пропущен (нет отдельного apple-health источника)")
        sys.exit(0)

    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"

    if mode == "historical":
        log.info("=== Импорт исторических данных ===")
        count = import_historical()
        log.info(f"Готово. Импортировано дней: {count}")
        if count > 0:
            _mark_apple_health_fresh()
        print_summary()

    elif mode == "daily":
        # ⚰ 26.09: ежедневный путь через iCloud-каталог HAE снят вместе с задачей
        # com.larry.health.daily — каталог пуст с перехода на REST 06.07 (последний файл
        # HealthAutoExport-2026-07-06.json), приём идёт через /hae/ingest. Режим оставлен,
        # чтобы случайный запуск сказал это вслух, а не молча отчитался «обновлено дней: 0».
        log.error("режим daily снят 26.09: Apple Health приходит через REST /hae/ingest "
                  "(dashboard_routers/api_hae_ingest.py); iCloud-каталог не читается")
        sys.exit(2)
    elif mode == "daily-icloud-legacy":
        log.info("=== Ежедневный импорт из New Automation/ ===")
        count = import_daily_new_automation(archive=True)
        log.info(f"Готово. Обновлено дней: {count}")
        # Метку свежести двигаем при новых днях ИЛИ когда телефон ещё экспортирует (свежий HAE-файл);
        # дедуп с Oura → recent файлы дают 0 новых записей, но данные свежи. Нет файлов → не двигаем.
        if count > 0 or _export_is_fresh():
            _mark_apple_health_fresh()
        if count > 0:
            print_summary()

    elif mode == "all":
        log.info("=== Полный импорт: история + ежедневные ===")
        h = import_historical()
        d = import_daily_new_automation(archive=False)  # не архивируем при полном импорте
        log.info(f"Готово. История: {h} дней, текущие: {d} дней")
        if h > 0 or d > 0 or _export_is_fresh():
            _mark_apple_health_fresh()
        print_summary()

    else:
        print(f"Использование: {sys.argv[0]} [historical|daily|all]")
        sys.exit(1)
