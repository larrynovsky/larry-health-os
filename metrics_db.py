"""metrics_db.py — доменный модуль metrics. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import json
import sqlite3 as _sqlite3
import os
import logging
from datetime import date, timedelta
from pathlib import Path

from _time_inject import get_today

log = logging.getLogger(__name__)


def get_day(day_str: str) -> dict:
    with _hdb.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM daily_metrics WHERE date=?", (day_str,)
        ).fetchone()
    if not row:
        return {}
    raw = json.loads(row["raw"]) if row["raw"] else {}
    # Flat-колонки как fallback: добавляем если их нет в raw JSON.
    # Это нужно потому что Oura пишет steps/calories/distance в flat-колонки
    # но не в raw JSON (не входят в OURA_RAW_KEYS). Apple Health заполняет
    # raw только когда HAE-файл обработан — что бывает не каждый день.
    # Использование is not None: steps=0 тоже валидное значение.
    _row = dict(row)  # dict() защищает от IndexError на старых схемах без новых колонок

    # steps: flat-колонка ВСЕГДА авторитетна (Oura primary по дизайну).
    # Apple Health может писать своё значение steps в raw через APPLE_RAW_KEYS,
    # но оно всегда меньше и ненадёжнее Oura. Flat-колонка защищена COALESCE
    # при Oura-апсерте, поэтому там всегда правильное значение.
    if _row.get("steps") is not None:
        raw["steps"] = _row["steps"]

    # Остальные fitness-метрики: fallback только если raw не имеет ключа
    # (Apple заполняет их в raw, Oura в flat — не конфликтуют)
    for key in ("active_kcal", "distance_km", "exercise_min", "cycling_km"):
        if key not in raw and _row.get(key) is not None:
            raw[key] = _row[key]

    # ВСР: flat-колонка как fallback. Footgun-фикс 2026-07-02 (был PIN-VERDICT:bug
    # в test_metrics_characterization): раньше flat-only hrv (raw без ключа 'hrv')
    # тихо не попадала в get_day — потребитель (_build_gp_context/lifestyle) мог
    # не увидеть ВСР, записанную плоско. Оборачиваем в raw-совместимую форму
    # {avg,unit,source}, т.к. потребители читают (day.get("hrv") or {}).get("avg");
    # голое число сломало бы .get. raw-hrv (Oura) приоритетна — fallback только
    # когда ключа нет. source='flat' помечает происхождение.
    if "hrv" not in raw and _row.get("hrv") is not None:
        raw["hrv"] = {"avg": _row["hrv"], "unit": "ms", "source": "flat"}

    # Остальные vitals — тот же footgun-класс (обзор 2026-07-02, follow-up #3):
    # flat-колонка невидима, если raw без ключа. Fallback в форме, которую ждут
    # потребители (проверено по gp_context/gp_agent/lifestyle/checkin). По данным
    # 0 flat-only записей — защита по построению. Имена flat-колонок ≠ raw-ключам.
    if "resting_heart_rate" not in raw and _row.get("resting_hr") is not None:
        raw["resting_heart_rate"] = {"value": _row["resting_hr"],
                                     "unit": "count/min", "source": "flat"}
    if "readiness_score" not in raw and _row.get("readiness") is not None:
        raw["readiness_score"] = _row["readiness"]   # потребитель ждёт голое число
    if "spo2" not in raw and _row.get("spo2_avg") is not None:
        raw["spo2"] = {"avg": _row["spo2_avg"], "unit": "%", "source": "flat"}

    # Sleep НЕ реконструируем: raw['sleep'] — многоключевой dict (totalSleep/deep/
    # rem/efficiency), flat это отдельные колонки sleep_*. Частичная сборка дала бы
    # потребителям dict без под-ключей, которые они читают → хуже, чем отсутствие.
    # Если понадобится — отдельный маппер flat→nested с полным набором ключей.
    return raw


def get_window(end: date, days: int) -> list[dict]:
    rows = []
    for i in range(1, days + 1):
        d = end - timedelta(days=i)
        with _hdb.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM daily_metrics WHERE date=?", (str(d),)
            ).fetchone()
        if row:
            rows.append(dict(row))
        else:
            rows.append({"date": str(d)})
    return rows


def get_stats(days: int = 30, end: date = None) -> dict:
    """Агрегированная статистика за период."""
    if end is None:
        end = get_today() - timedelta(days=1)
    start = end - timedelta(days=days)
    with _hdb.get_conn() as conn:
        row = conn.execute("""
            SELECT
                ROUND(AVG(sleep_total),2) as avg_sleep,
                ROUND(AVG(sleep_deep),2)  as avg_deep,
                ROUND(AVG(sleep_rem),2)   as avg_rem,
                ROUND(AVG(sleep_score),1) as avg_sleep_score,
                ROUND(AVG(hrv),1)         as avg_hrv,
                ROUND(MIN(hrv),1)         as min_hrv,
                ROUND(MAX(hrv),1)         as max_hrv,
                ROUND(AVG(resting_hr),1)  as avg_rhr,
                ROUND(AVG(readiness),1)   as avg_readiness,
                ROUND(AVG(steps))         as avg_steps,
                ROUND(AVG(spo2_avg),1)    as avg_spo2,
                ROUND(AVG(weight),1)      as avg_weight,
                ROUND(AVG(bp_systolic),1) as avg_bp_systolic,
                ROUND(AVG(bp_diastolic),1) as avg_bp_diastolic,
                COUNT(*)                  as n_days
            FROM daily_metrics
            WHERE date BETWEEN ? AND ?
              AND sleep_total IS NOT NULL
        """, (str(start), str(end))).fetchone()
    # Пустое среднее — ключа НЕТ, а не None: читатели пишут `.get('avg_hrv', '—')`, а default
    # dict.get не ловит None-значение — в промпты агентов уезжало «None / None» (замер 24.09:
    # gp_context, wellally, checkin_agent, lifestyle_agents, handlers/meta). Скобочного доступа
    # к ключам нет ни у одного читателя (grep 24.09), поэтому чинится здесь, в точке генерации.
    return {k: v for k, v in dict(row).items() if v is not None} if row else {}


def get_metric_percentiles(baseline_days: int = 90) -> dict:
    """
    Возвращает перцентильный ранг вчерашних ключевых метрик
    относительно последних baseline_days дней.

    Результат:
        {metric: {value, percentile, p25, p75, mean, n}}
    percentile: 0-100, где 100 = лучшее значение (для stress_high_min инвертировано).
    Если данных < 14 дней — метрика пропускается.
    """
    from datetime import date as _date, timedelta as _td
    import statistics as _st

    METRICS = [
        "bp_systolic",
        "bp_diastolic",
        "resilience_daytime_pct",
        "resilience_sleep_pct",
        "resilience_stress_pct",
        "recovery_high_min",
        "stress_high_min",
        "hrv",
        "resting_hr",
        "readiness",
        "activity_score",
        "steps",
        "sleep_score",
        "sleep_rem",
        "sleep_deep",
        "sleep_efficiency",
    ]
    # Для этих метрик "больше = хуже", поэтому percentile инвертируем
    # resting_hr: высокая ЧСС покоя = признак стресса/воспаления
    INVERTED = {"stress_high_min", "resting_hr", "bp_systolic", "bp_diastolic"}

    today     = get_today()
    yesterday = today - _td(days=1)
    since     = today - _td(days=baseline_days + 1)

    with _hdb.get_conn() as conn:
        cols = ", ".join(METRICS)
        rows = conn.execute(
            f"SELECT date, {cols} FROM daily_metrics "
            f"WHERE date >= ? AND date <= ? ORDER BY date",
            (str(since), str(yesterday))
        ).fetchall()

    if not rows:
        return {}

    rows = [dict(r) for r in rows]
    yesterday_str = str(yesterday)

    # Вчерашние значения
    today_row = next((r for r in rows if r["date"] == yesterday_str), None)
    if today_row is None:
        return {}

    result = {}
    for m in METRICS:
        val = today_row.get(m)
        if val is None:
            continue
        try:
            val = float(val)
        except (TypeError, ValueError):
            continue

        # Исторические значения (включая вчера, для percentile rank)
        history = []
        for r in rows:
            v = r.get(m)
            if v is not None:
                try:
                    history.append(float(v))
                except (TypeError, ValueError):
                    pass

        if len(history) < 14:
            continue

        history_sorted = sorted(history)
        n = len(history_sorted)
        rank = sum(1 for x in history_sorted if x <= val) / n * 100

        if m in INVERTED:
            rank = 100 - rank  # инвертируем: низкий стресс = высокий percentile

        p25 = history_sorted[int(n * 0.25)]
        p75 = history_sorted[int(n * 0.75)]
        mean = _st.mean(history)

        result[m] = {
            "value":      round(val, 1),
            "percentile": round(rank, 1),
            "p25":        round(p25, 1),
            "p75":        round(p75, 1),
            "mean":       round(mean, 1),
            "n":          n,
        }

    return result


# Ключи дня Apple Health, которые доезжают до базы: в raw (APPLE_RAW_KEYS) или в
# raw["apple_health"] через fallback-биометрику (APPLE_BIO_INPUTS). Модульный уровень с 26.09 —
# второй читатель hae_checker.judge_payload судит «разобрано» по ним: ключ, который разборщик
# кладёт, но база не хранит, — такая же потеря, как неразобранный.
APPLE_RAW_KEYS = frozenset({
    "steps", "active_kcal", "total_kcal", "distance_km", "activity_score",
    "weight_kg", "vo2_max", "bp_systolic", "bp_diastolic",
    # максимум за день — только в raw, не колонкой: колонка вошла бы в семью сигналов
    # (сторож v8) и потребовала ре-объявления; тревоге нужен пик, поиску связей — нет
    "bp_systolic_max", "bp_diastolic_max",
    # Module 2 (2026-06-01)
    "exercise_min", "cycling_km", "walking_speed_avg",
    "walking_step_length_avg", "walking_asymmetry_avg", "stand_min", "met_avg",
    # Эти поля импортёра сохраняются в raw. Отсутствие отдельных колонок
    # не должно приводить к потере полей; семья сигналов при этом не меняется.
    "heart_rate", "walking_hr", "daylight_min", "flights", "mindful_min", "basal_kcal",
    "walking_double_support_avg",
    "cardio_recovery_bpm", "six_min_walk_m", "stair_speed_up_ms", "stair_speed_down_ms",
    "body_fat_pct", "bmi",
})
# Колонки, которые Apple Health только ДОЛИВАЕТ (COALESCE): у них есть другой писатель —
# import_fitdays (CSV тех же весов, та же семантика «утро»). Решение владельца 26.09 (вопрос 5, А):
# кто первым записал день, тот и прав; два пути не спорят за одно значение.
APPLE_FILL_ONLY = frozenset({"body_fat_pct", "bmi"})
APPLE_BIO_INPUTS = frozenset({"hrv", "resting_heart_rate", "sleep", "spo2", "respiratory_rate"})
_APPLE_BP_KEYS = ("bp_systolic", "bp_diastolic", "bp_systolic_max", "bp_diastolic_max")


def _bp_owned_by_withings(conn) -> bool:
    """Есть хоть один замер Withings — дневное давление его (import_withings), не «Здоровья»."""
    try:
        return conn.execute("SELECT 1 FROM bp_readings LIMIT 1").fetchone() is not None
    except _sqlite3.OperationalError:            # таблицы ещё нет — Withings не было никогда
        return False


def upsert_metrics_from_json(day_str: str, data: dict, source: str = "Oura"):
    """W5K-B (2026-05-14): per-source ownership.

    Пишет в daily_metrics только колонки, принадлежащие `source`.
    None никогда не пишется. Чужие колонки не трогаются.

    source ownership:
      Oura → sleep_*, hrv, resting_hr, readiness*, spo2_avg, breathing_disturbance,
             stress_*, recovery_high_min, resilience_*
      AppleHealth → steps, active_kcal, total_kcal, distance_km, activity_score,
                    weight, vo2max, bp_systolic, bp_diastolic

    Раньше: INSERT ... ON CONFLICT DO UPDATE всех 35 колонок включая None.
    Это затирало Apple Health значения Oura None-ами. См. #171.
    """
    if source not in ("Oura", "AppleHealth"):
        raise ValueError(f"Unknown source: {source!r}. Expected 'Oura' or 'AppleHealth'.")

    sleep    = data.get("sleep") or {}
    hrv      = data.get("hrv") or {}
    rhr      = data.get("resting_heart_rate") or {}
    spo2     = data.get("spo2") or {}
    sleep_c  = sleep.get("contributors") or {}
    stress   = data.get("stress") or {}
    resil    = data.get("resilience") or {}
    resil_c  = resil.get("contributors") or {}

    def _min(secs):
        return round(secs / 60) if secs else None

    if source == "Oura":
        candidates = {
            "sleep_total":              sleep.get("totalSleep"),
            "sleep_deep":               sleep.get("deep"),
            "sleep_rem":                sleep.get("rem"),
            "sleep_score":              sleep.get("sleep_score"),
            "sleep_inbed":              sleep.get("inBed"),
            "sleep_awake":              sleep.get("awake"),
            "sleep_core":               sleep.get("core"),
            "sleep_start":              sleep.get("sleepStart"),
            "sleep_end":                sleep.get("sleepEnd"),
            "sleep_efficiency":         sleep_c.get("efficiency"),
            "hrv":                      hrv.get("avg") if isinstance(hrv, dict) else None,
            "resting_hr":               rhr.get("value") if isinstance(rhr, dict) else None,
            "readiness":                data.get("readiness_score"),
            "readiness_hrv_balance":    data.get("hrv_balance"),
            "readiness_body_temp":      data.get("body_temperature"),
            "readiness_recovery_idx":   data.get("recovery_index"),
            "spo2_avg":                 spo2.get("avg") if isinstance(spo2, dict) else None,
            "breathing_disturbance":    data.get("breathing_disturbance"),
            "stress_summary":           stress.get("day_summary"),
            "stress_high_min":          _min(stress.get("stress_high")),
            "recovery_high_min":        _min(stress.get("recovery_high")),
            "resilience_level":         resil.get("level"),
            "resilience_sleep_pct":     resil_c.get("sleep_recovery"),
            "resilience_daytime_pct":   resil_c.get("daytime_recovery"),
            "resilience_stress_pct":    resil_c.get("stress"),
            # Fitness берём из Oura; наличие поля в HAE CSV зависит от входного файла.
            "steps":                    data.get("steps"),
            "active_kcal":              data.get("active_kcal"),
            "total_kcal":               data.get("total_kcal"),
            "distance_km":              data.get("distance_km"),
            "activity_score":           data.get("activity_score"),
        }
    else:  # AppleHealth — owned columns + Module 2 extended metrics
        vo2 = data.get("vo2_max")
        vo2_value = vo2.get("value") if isinstance(vo2, dict) else vo2
        candidates = {
            # Owned: Apple-only (manual entry / external device)
            "weight":                   data.get("weight_kg") or data.get("weight"),
            "vo2max":                   vo2_value,
            "bp_systolic":              data.get("bp_systolic"),
            "bp_diastolic":             data.get("bp_diastolic"),
            # Module 2: activity & gait (2026-06-01)
            "exercise_min":             data.get("exercise_min"),
            "cycling_km":               data.get("cycling_km"),
            "walking_speed_avg":        data.get("walking_speed_avg"),
            "walking_step_length_avg":  data.get("walking_step_length_avg"),
            "walking_asymmetry_avg":    data.get("walking_asymmetry_avg"),
            "stand_min":                data.get("stand_min"),
            "met_avg":                  data.get("met_avg"),
            # 26.09 (решение владельца, вариант Б) — выносливость/подвижность
            "cardio_recovery_bpm":      data.get("cardio_recovery_bpm"),
            "six_min_walk_m":           data.get("six_min_walk_m"),
            "stair_speed_up_ms":        data.get("stair_speed_up_ms"),
            "stair_speed_down_ms":      data.get("stair_speed_down_ms"),
        }

    values = {k: v for k, v in candidates.items() if v is not None}

    # W5K #177: merge raw — Oura и AppleHealth владеют разными ключами
    # внутри nested JSON. Не overwrite, а merge с existing raw.
    OURA_RAW_KEYS = {
        "sleep", "hrv", "resting_heart_rate", "spo2", "stress", "resilience",
        "readiness_score", "hrv_balance", "body_temperature", "recovery_index",
        "breathing_disturbance",
    }
    owned = OURA_RAW_KEYS if source == "Oura" else APPLE_RAW_KEYS
    incoming = {k: data[k] for k in owned if k in data and data[k] is not None}

    # ── Variant B (2026-06-01): Apple Health как fallback для биометрики ─────
    # Когда Oura мертва, Apple Health может дать HRV, sleep, resting_hr.
    # Стратегия: flat-колонки обновляются через COALESCE (не перезаписывают Oura).
    # Данные сохраняются в raw["apple_health"] для маркировки источника в GP-промпте.
    apple_bio: dict = {}
    if source == "AppleHealth":
        hrv_d   = data.get("hrv")
        rhr_d   = data.get("resting_heart_rate")
        sleep_d = data.get("sleep")
        spo2_d  = data.get("spo2")
        resp_d  = data.get("respiratory_rate")

        if isinstance(hrv_d, dict) and hrv_d.get("avg"):
            apple_bio["hrv"]         = hrv_d["avg"]
        if isinstance(rhr_d, dict) and rhr_d.get("value"):
            apple_bio["resting_hr"]  = rhr_d["value"]
        if isinstance(sleep_d, dict):
            if sleep_d.get("totalSleep"):
                apple_bio["sleep_total"] = sleep_d["totalSleep"]
            if sleep_d.get("deep"):
                apple_bio["sleep_deep"]  = sleep_d["deep"]
            if sleep_d.get("sleep_score"):
                apple_bio["sleep_score"] = sleep_d["sleep_score"]
            if sleep_d.get("source"):
                apple_bio["sleep_source"] = sleep_d["source"]
        if isinstance(spo2_d, dict) and spo2_d.get("avg"):
            apple_bio["spo2_avg"]    = spo2_d["avg"]
        if isinstance(resp_d, dict) and resp_d.get("avg"):
            apple_bio["respiratory_rate"] = resp_d["avg"]
        if apple_bio.get("hrv") or apple_bio.get("sleep_total"):
            apple_bio["_source"] = "AppleHealth"  # маркер для GP-промпта

    # Ранний выход — ПОСЛЕ сбора apple_bio (BL-HAE-OWNER-1 (г), 27.09): день, где Apple Health
    # прислал только биометрику-fallback (скажем, одну частоту дыхания), раньше уходил сюда
    # с пустыми values/incoming и терялся молча.
    if not values and not incoming and not apple_bio:
        return

    with _hdb.get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO daily_metrics(date) VALUES (?)", (day_str,))

        # Merge raw
        row = conn.execute("SELECT raw FROM daily_metrics WHERE date=?", (day_str,)).fetchone()
        existing_raw = {}
        if row and row[0]:
            try:
                existing_raw = json.loads(row[0])
                if not isinstance(existing_raw, dict):
                    existing_raw = {}
            except json.JSONDecodeError:
                existing_raw = {}
        # Записываем Apple Health биометрику под отдельным ключом
        if apple_bio:
            existing_raw["apple_health"] = apple_bio
        # Давление: у кого подключён Withings, хозяин дневного давления — он (решение владельца
        # 06.10; пишет import_withings). «Здоровье» хранит удалённые в Withings замеры и подмешивало
        # их в среднее дня (живой случай, 06.10). Значения «Здоровья» не теряются — уходят свидетелем.
        if source == "AppleHealth" and _bp_owned_by_withings(conn):
            witness = {k: incoming.pop(k) for k in _APPLE_BP_KEYS if k in incoming}
            for k in ("bp_systolic", "bp_diastolic"):
                values.pop(k, None)
            if witness:
                existing_raw["apple_health_bp"] = witness
        existing_raw.update(incoming)
        values["raw"] = json.dumps(existing_raw, ensure_ascii=False)

        # Variant B fallback: Apple биометрика → flat-колонки через COALESCE.
        # COALESCE(col, ?) сохраняет Oura-значение если оно есть, иначе пишет Apple.
        # Guard: не перезаписываем Oura данные.
        if apple_bio and source == "AppleHealth":
            fallback_map = {
                "hrv":         apple_bio.get("hrv"),
                "resting_hr":  apple_bio.get("resting_hr"),
                "sleep_total": apple_bio.get("sleep_total"),
                "sleep_deep":  apple_bio.get("sleep_deep"),
                "sleep_score": apple_bio.get("sleep_score"),
                "spo2_avg":    apple_bio.get("spo2_avg"),
            }
            fb_values = {k: v for k, v in fallback_map.items() if v is not None}
        else:
            fb_values = {}
        if source == "AppleHealth":
            fb_values.update({k: data[k] for k in APPLE_FILL_ONLY if data.get(k) is not None})
        if fb_values:
            fb_clause = ", ".join(f"{k} = COALESCE({k}, ?)" for k in fb_values)
            conn.execute(
                f"UPDATE daily_metrics SET {fb_clause} WHERE date = ?",
                list(fb_values.values()) + [day_str]
            )

        set_clause = ", ".join(f"{k} = ?" for k in values)
        params = list(values.values()) + [day_str]
        conn.execute(f"UPDATE daily_metrics SET {set_clause} WHERE date = ?", params)


def build_context(target: date) -> dict:
    """
    Собирает компактный контекст (~2000 токенов) для генерации отчёта.
    """
    stats_7  = get_stats(7, target)
    stats_30 = get_stats(30, target)
    yesterday = get_day(str(target))
    checkins  = _hdb.get_recent_checkins(3)
    exps      = _hdb.get_active_experiments()

    return {
        "date": str(target),
        "yesterday": yesterday,
        "stats_7d":  stats_7,
        "stats_30d": stats_30,
        "recent_checkins": checkins,
        "active_experiments": exps,
    }


# ── Все показатели для врачей (BL-DATA-PARITY-1, решение владельца 24.09) ─────
# Единственный дом подписей колонок daily_metrics и единственный рендер «всё, что
# собрано». Список колонок берётся из самой таблицы (PRAGMA), не из литерала: новая
# колонка без подписи краснит tests/unit/test_all_metrics_block.py, а не пропадает
# молча, как пропадали 20 колонок до 24.09 (у каждого врача был свой ручной список).
METRIC_LABELS: dict[str, tuple[str, str]] = {
    "sleep_total": ("Сон всего", "ч"),
    "sleep_deep": ("Глубокий сон", "ч"),
    "sleep_rem": ("REM-сон", "ч"),
    "sleep_core": ("Лёгкий (core) сон", "ч"),
    "sleep_awake": ("Бодрствование ночью", "ч"),
    "sleep_inbed": ("Время в постели", "ч"),
    "sleep_efficiency": ("Эффективность сна", "%"),
    "sleep_score": ("Оценка сна Oura", "баллы"),
    "sleep_start": ("Отход ко сну", "время"),
    "sleep_end": ("Подъём", "время"),
    "hrv": ("ВСР ночью", "мс"),
    "resting_hr": ("Пульс покоя", "уд/мин"),
    "readiness": ("Готовность Oura", "баллы"),
    "readiness_hrv_balance": ("Готовность: баланс ВСР", "баллы"),
    "readiness_body_temp": ("Готовность: температура тела", "баллы"),
    "readiness_recovery_idx": ("Готовность: индекс восстановления", "баллы"),
    "spo2_avg": ("SpO2 ночью", "%"),
    "breathing_disturbance": ("Индекс нарушений дыхания во сне", ""),
    "steps": ("Шаги", ""),
    "distance_km": ("Дистанция", "км"),
    "active_kcal": ("Активные ккал", "ккал"),
    "total_kcal": ("Все ккал за день", "ккал"),
    "activity_score": ("Оценка активности Oura", "баллы"),
    "exercise_min": ("Минуты упражнений", "мин"),
    "stand_min": ("Время стоя", "мин"),
    "met_avg": ("Средняя нагрузка", "MET"),
    "cycling_km": ("Велосипед", "км"),
    "walking_speed_avg": ("Скорость ходьбы", "км/ч"),
    "walking_step_length_avg": ("Длина шага", "см"),
    "walking_asymmetry_avg": ("Асимметрия ходьбы", "%"),
    "cardio_recovery_bpm": ("Восстановление пульса после нагрузки", "уд/мин"),
    "six_min_walk_m": ("Тест 6-минутной ходьбы", "м"),
    "stair_speed_up_ms": ("Скорость подъёма по лестнице", "м/с"),
    "stair_speed_down_ms": ("Скорость спуска по лестнице", "м/с"),
    "vo2max": ("VO2max", "мл/(кг·мин)"),
    "stress_high_min": ("Высокий стресс", "мин"),
    "recovery_high_min": ("Высокое восстановление", "мин"),
    "stress_summary": ("Итог дня по стрессу", ""),
    "resilience_level": ("Устойчивость к стрессу", ""),
    "resilience_sleep_pct": ("Устойчивость: вклад сна", "%"),
    "resilience_daytime_pct": ("Устойчивость: вклад дневного восстановления", "%"),
    "resilience_stress_pct": ("Устойчивость: вклад стресса", "%"),
    "weight": ("Вес", "кг"),
    "bmi": ("ИМТ", ""),
    "body_fat_pct": ("Жир", "%"),
    "skeletal_muscle_pct": ("Скелетные мышцы", "%"),
    "muscle_mass_kg": ("Мышечная масса", "кг"),
    "visceral_fat": ("Висцеральный жир", "уровень"),
    "body_water_pct": ("Вода в теле", "%"),
    "bone_mass_kg": ("Костная масса", "кг"),
    "protein_pct": ("Белок", "%"),
    "bmr_kcal": ("Базовый обмен", "ккал"),
    "bp_systolic": ("Давление верхнее", "мм рт. ст."),
    "bp_diastolic": ("Давление нижнее", "мм рт. ст."),
}
_NOT_METRICS = ("date", "raw")
_CLOCK_COLUMNS = ("sleep_start", "sleep_end")


def metric_columns(conn=None) -> list[str]:
    """Колонки daily_metrics, которые являются показателями (всё, кроме date и raw)."""
    def _cols(c):
        return [r[1] for r in c.execute("PRAGMA table_info(daily_metrics)")
                if r[1] not in _NOT_METRICS]
    if conn is not None:
        return _cols(conn)
    with _hdb.get_conn() as c:
        return _cols(c)


def _clock_minutes(v) -> int | None:
    """'2030-01-15T23:10:00+02:00' / '2030-01-15 23:10:00 +0200' → минуты от полудня.
    От полудня, а не от полуночи: отход ко сну 23:50 и 00:20 должны усредняться рядом."""
    import re
    m = re.search(r"[T ](\d{2}):(\d{2})", str(v))
    if not m:
        return None
    return (int(m.group(1)) * 60 + int(m.group(2)) - 720) % 1440


def _fmt_clock(m: float) -> str:
    m = (round(m) + 720) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def _fmt_num(v: float) -> str:
    if abs(v) >= 100:
        return f"{v:.0f}"
    s = f"{v:.1f}" if abs(v) >= 10 else f"{v:.2f}"
    return s.rstrip("0").rstrip(".")


def render_all_metrics(days: int = 30, end: date | None = None) -> str:
    """Блок «все собранные показатели» для любого врачебного контекста.

    Каждая колонка daily_metrics, по которой в окне есть хоть одно значение:
    последнее значение и дата, среднее за 7 дней и за всё окно, число дней с данными.
    Пустые колонки не печатаются — у каждого тенанта ровно то, что реально пришло.
    Пустая строка, если в окне нет ничего (вызывающий блок пропускает).
    """
    from collections import Counter
    if end is None:
        end = get_today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    week = str(end - timedelta(days=6))
    with _hdb.get_conn() as conn:
        cols = metric_columns(conn)
        rows = conn.execute(
            f"SELECT date, {', '.join(cols)} FROM daily_metrics "
            "WHERE date BETWEEN ? AND ? ORDER BY date", (str(start), str(end))
        ).fetchall()
    lines = []
    for i, col in enumerate(cols, start=1):
        pts = [(r[0], r[i]) for r in rows if r[i] is not None and r[i] != ""]
        label, unit = METRIC_LABELS.get(col, (col, ""))
        name = f"{label}, {unit}" if unit and col not in _CLOCK_COLUMNS else label
        if col in _CLOCK_COLUMNS:
            pts = [(d, _clock_minutes(v)) for d, v in pts]
            pts = [(d, m) for d, m in pts if m is not None]
            fmt = _fmt_clock
        elif pts and isinstance(pts[-1][1], str):
            common = Counter(v for _, v in pts).most_common(1)[0][0]
            d = pts[-1][0]
            lines.append(f"  {name}: {pts[-1][1]} ({d[8:10]}.{d[5:7]}) · чаще всего: {common}"
                         f" · дней {len(pts)}/{days}")
            continue
        else:
            pts = [(d, float(v)) for d, v in pts if isinstance(v, (int, float))]
            fmt = _fmt_num
        if not pts:
            continue
        d, last = pts[-1]
        w = [v for dd, v in pts if dd >= week]
        avg7 = f" · ср. 7д {fmt(sum(w) / len(w))}" if w else ""
        lines.append(f"  {name}: {fmt(last)} ({d[8:10]}.{d[5:7]}){avg7}"
                     f" · ср. {days}д {fmt(sum(v for _, v in pts) / len(pts))}"
                     f" · дней {len(pts)}/{days}")
    if not lines:
        return ""
    head = (f"ВСЕ СОБРАННЫЕ ПОКАЗАТЕЛИ ({start:%d.%m}–{end:%d.%m}, последнее · среднее · дней с"
            " данными). Справочно: комментируй только то, что важно для вопроса или заметно"
            " отклоняется от обычного для этого человека.")
    return head + "\n" + "\n".join(lines)


# Провенанс колонок: какой источник пишет колонку, которую пишет ТОЛЬКО он (системный факт
# импортёров, не клиническое число). Источник «есть», если за окно у него есть хоть одна строка.
# Читатель — пакет специалистов («Источники данных»). Ручное поле medical.devices
# может быть пустым при наличии измерений; наличие источника выводится из данных.
# Свежесть прибора: последний день старше N дней — источник замолчал (conit C1, TESTING_CONTRACTS).
# Дом числа здесь, рядом с провенансом источников: его читают ночной датчик (integrity_tests,
# DATA_FRESHNESS_DAYS) и главная дашборда (getting_started). Перенесено 02.10.2026 из
# integrity_tests, импорт которого исполняет весь монитор и поэтому недоступен читателям.
SOURCE_STALE_DAYS = 2

SOURCE_SIGNATURE: dict[str, tuple[str, ...]] = {
    "Oura": ("readiness", "sleep_score", "readiness_hrv_balance"),
    "Apple Health": ("exercise_min", "stand_min", "walking_speed_avg", "met_avg", "vo2max"),
    "весы (состав тела)": ("body_fat_pct", "muscle_mass_kg", "bmr_kcal"),
}


def data_sources(days: int = 30, end: date | None = None) -> list[str]:
    """Источники, от которых за окно реально пришли данные (по колонкам-подписям)."""
    if end is None:
        end = get_today()
    start = end - timedelta(days=days)
    with _hdb.get_conn() as conn:
        cols = set(metric_columns(conn))
        out = []
        for name, sig in SOURCE_SIGNATURE.items():
            present = [c for c in sig if c in cols]
            if not present:
                continue
            cond = " OR ".join(f"{c} IS NOT NULL" for c in present)
            n = conn.execute(f"SELECT COUNT(*) FROM daily_metrics WHERE date BETWEEN ? AND ? "
                             f"AND ({cond})", (str(start), str(end))).fetchone()[0]
            if n:
                out.append(name)
    return out


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
