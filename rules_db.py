"""rules_db.py — доменный модуль rules. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import json
import os
import logging
from datetime import date, timedelta
from pathlib import Path


log = logging.getLogger(__name__)

# INTENT: deterministic_norm_source — детерминированный слой нормы (§9): единственный
#          рантайм-источник клинической нормы + громкий отказ вместо тихого литерала.
#          Замысел и инварианты — subsystem_intent.yaml, раздел deterministic_norm_source.


def get_absolute_thresholds(direction: str | None = None) -> list[dict]:
    """Возвращает активные абсолютные пороги. Опциональный фильтр по direction."""
    with _hdb.get_conn() as conn:
        if direction:
            rows = conn.execute(
                """SELECT metric, direction, value, reason_template, source, source_date,
                          kind, baseline, band_label, variant
                   FROM absolute_thresholds WHERE active=1 AND direction=?
                   ORDER BY metric""",
                (direction,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT metric, direction, value, reason_template, source, source_date,
                          kind, baseline, band_label, variant
                   FROM absolute_thresholds WHERE active=1
                   ORDER BY direction, metric"""
            ).fetchall()
    return [dict(r) for r in rows]


_DAILY_METRIC_COLS = {
    "sleep_total", "sleep_deep", "sleep_rem", "sleep_score", "hrv", "resting_hr",
    "readiness", "steps", "active_kcal", "spo2_avg", "weight", "vo2max",
}


def get_trend_thresholds() -> list[dict]:
    """Активные трендовые/оконные пороги (N дней подряд / скользящее среднее).

    Значения-нормы (§9 данные); оконная логика — в trend_alerts.py (код). metric
    фильтруется по whitelist колонок daily_metrics — защита f-string в trend_alerts._last_n.
    """
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            """SELECT metric, direction, value, window_days, mode, level, domain,
                      reason_template, source, source_date,
                      COALESCE(condition_key,'') AS condition_key
               FROM trend_thresholds WHERE active=1
               ORDER BY metric, window_days"""
        ).fetchall()
    return [d for d in (dict(r) for r in rows) if d["metric"] in _DAILY_METRIC_COLS]


def get_lab_trend_thresholds() -> list[dict]:
    """Активные пороги ЛАБОРАТОРНЫХ трендов (safety-net-thresholds, §9-вынос E1).

    Линейка — «изменение на pct_change% за n_readings последних ИЗМЕРЕНИЙ» (не дней).
    Источник данных — lab_results (не daily_metrics), поэтому daily-whitelist сюда НЕ
    применяется (в отличие от get_trend_thresholds). Потребитель — safety_net.check_lab_trends.
    """
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            """SELECT metric, direction, pct_change, n_readings, level,
                      reason_template, source, source_date,
                      near_boundary_share, near_boundary_source
               FROM lab_trend_thresholds WHERE active=1
               ORDER BY metric"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_threshold(metric: str, direction: str,
                  band_label: str = "", variant: str = "") -> float:
    """§9: единственный рантайм-источник клинической нормы — таблица absolute_thresholds.

    Идентичность порога — (metric, direction, band_label, variant): band_label = полоса
    тяжести (very_low/low/...), variant = назначение (food/deep/lifestyle). По умолчанию
    оба пусты (единственный/безымянный порог метрики).

    Возвращает value активного порога. Бросает KeyError, если порог не засеян — это
    поломка seed-контура (_seed_absolute_thresholds), а НЕ повод тихо подставить литерал.
    Громкий отказ > молчаливо неверный порог (§9 rationale). Для relative-порога value —
    множитель baseline (t["baseline"]).
    """
    for t in get_absolute_thresholds(direction):
        if (t["metric"] == metric
                and (t.get("band_label") or "") == band_label
                and (t.get("variant") or "") == variant):
            return t["value"]
    raise KeyError(
        f"absolute_thresholds: нет активного {metric}/{direction}"
        f"/band={band_label!r}/variant={variant!r} — проверь _seed_absolute_thresholds (§9)"
    )


def get_active_constraints(protocol_id=None):
    """SX-1.8 (2026-05-18): now reads from `alerts` table.

    Возвращает активные survivorship-правила и пациентские медицинские alerts.
    Старый аргумент protocol_id игнорируется (был для удалённой patient_constraints
    таблицы — теперь правила не привязаны к protocols).

    Возвращает list[dict] с полями: id, type, severity, message, source, action,
    condition, reason — последние 4 для backward-compat с callers, ожидающими
    схему patient_constraints. Маппинг:
      - action = type (medication_interaction/allergy/DNR/other)
      - condition = '' (нет в alerts)
      - reason = message
    """
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, type, severity, message, source, active "
            "FROM alerts WHERE active=1 "
            "ORDER BY id DESC"
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        # backward-compat aliases для старых callers
        d["action"]    = d.get("type")
        d["condition"] = ""
        d["reason"]    = d.get("message")
        d["protocol_id"] = None
        d["expires_at"]  = None
        result.append(d)
    return result


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
