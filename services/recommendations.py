"""
services/recommendations.py — domain-need evaluator (без Telegram-привязки).

evaluate_domain_need читает DOMAIN_SIGNALS из health.db (system_config conit),
сверяет персональные перцентили (90д baseline) с порогами, проверяет
ABSOLUTE_FLOORS/CEILINGS (из absolute_thresholds таблицы), учитывает
patient_constraints (time-of-day prohibit).

Возвращает {needed, urgency, reasons, protocols, blocked_by}.

Используется в jobs/recommendations (cron каждые 2ч) и может быть переиспользована
в handlers/ или dashboard.

Вынесено из telegram_bot.py в Sprint 6 (C9, 2026-05-23).
"""
from __future__ import annotations

import logging
import i18n
from datetime import datetime as _dt
from zoneinfo import ZoneInfo

import health_db as db

log = logging.getLogger(__name__)

import region_pack  # noqa: E402
TZ = ZoneInfo(region_pack.value("timezone", "UTC"))   # таймзона дома — пакет региона (BL-PUB-16 б)


def _signal_reason(metric: str, val: float, mean: float, pct: float, domain: str = "") -> str:
    """Человекочитаемая причина для уведомления."""
    if domain == "nutrition" and metric == "stress_high_min":
        mult = val / max(mean, 1)
        return i18n.t('recommendations.reason.nutrition_stress', val=val)
    if metric == "stress_high_min":
        mult = val / max(mean, 1)
        if mult >= 2.0:
            return i18n.t('recommendations.reason.high_stress', val=val, mult=mult)
        else:
            return i18n.t('recommendations.reason.stress', val=val, mean=mean)
    if metric == "recovery_high_min":
        return i18n.t('recommendations.reason.recovery', val=val, mean=mean)
    if metric == "resilience_daytime_pct":
        return i18n.t('recommendations.reason.daytime_recovery', val=val, mean=mean)
    if metric == "resilience_sleep_pct":
        return i18n.t('recommendations.reason.sleep_recovery', val=val, mean=mean)
    if metric == "resilience_stress_pct":
        return i18n.t('recommendations.reason.stress_resilience', val=val, mean=mean)
    if metric == "activity_score":
        return i18n.t('recommendations.reason.activity', val=val, mean=mean)
    if metric == "steps":
        return i18n.t('recommendations.reason.steps', val=val)
    if metric == "resting_hr":
        mult = val / max(mean, 1)
        return i18n.t('recommendations.reason.resting_hr', val=val, mean=mean)
    if metric == "hrv":
        return i18n.t('recommendations.reason.hrv', val=val)
    if metric == "sleep_score":
        return i18n.t('recommendations.reason.sleep_score', val=val, mean=mean)
    if metric == "sleep_rem":
        return i18n.t('recommendations.reason.sleep_rem', val=val, mean=mean)
    if metric == "sleep_efficiency":
        return i18n.t('recommendations.reason.sleep_efficiency', val=val, mean=mean)
    return i18n.t('recommendations.reason.other', metric=metric, val=val, mean=mean)


def _raw_only_yesterday(metric: str) -> dict | None:
    """Вчерашнее значение ключа, у которого НЕТ колонки в daily_metrics (живёт только в raw).

    Абсолютный порог по замыслу срабатывает «независимо от перцентиля», но значение он брал из
    get_metric_percentiles, а тот пропускает метрику с историей < 14 дней. Редкий замер (давление
    тонометром — раз в месяц) туда не попадает никогда → порог был мёртв при любых данных
    (sleep_total, sleep_awake) не трогаем: у них свой читатель в lifestyle_agents,
    поэтому расширение этой ветки может дублировать тревоги.
    """
    from datetime import timedelta
    from _time_inject import get_today
    if metric in db.metric_columns():
        return None
    val = db.get_day(str(get_today() - timedelta(days=1))).get(metric)
    if not isinstance(val, (int, float)):
        return None
    return {"value": val, "mean": val}


def evaluate_domain_need(domain: str) -> dict:
    """
    Оценка необходимости поддержки домена на основе персональных перцентилей.
    Сигналы задаются конфигурацией домена; размер личного ряда здесь не фиксируется.

    DOMAIN_SIGNALS: для каждого домена список сигналов вида
        (metric, direction, correlation_target, r, threshold_pct)
    direction='low'  → срабатывает если percentile < threshold
    direction='high' → срабатывает если percentile < threshold (уже инвертировано в get_metric_percentiles)

    urgency:
      'required'     — 2+ сигнала, или хотя бы 1 сигнал с percentile < 10
      'recommended'  — 1 сигнал
    """
    try:
        now_h = _dt.now(tz=TZ).hour

        # DOMAIN_SIGNALS читаются из system_config (health.db) — единый conit.
        # Fallback: пустой список → домен не поддерживается.
        _raw_signals = db.get_domain_signals(domain)
        signals_def = [
            (s["metric"], s["label"], s["r"], s["threshold_pct"])
            for s in _raw_signals
        ]
        # Q-2 fix (2026-05-22, roadmap / BUG-FLOORS-ORDER): убран early
        # return на пустой signals_def. ABSOLUTE_FLOORS/CEILINGS должны
        # срабатывать «независимо от перцентиля и домена» (док), но
        # early return их пропускал. Теперь signals_def[] просто даёт
        # 0 fired через signal-check, FLOORS/CEILINGS ниже всё ещё проверяются.
        # Если после всех проверок fired остаётся пустым — обычная ветка
        # `if not fired: return` сработает ниже.
        pcts = db.get_metric_percentiles(baseline_days=90)

        fired = []
        for metric, target_label, r_val, threshold in signals_def:
            info = pcts.get(metric)
            if info is None:
                continue
            pct = info["percentile"]
            val = info["value"]
            mean = info["mean"]
            if pct < threshold:
                # Формируем читаемое объяснение
                direction = "↓" if val < mean else "↑"
                fired.append({
                    "metric":   metric,
                    "pct":      pct,
                    "val":      val,
                    "mean":     mean,
                    "target":   target_label,
                    "r":        r_val,
                    "reason":   _signal_reason(metric, val, mean, pct, domain=domain),
                })

        # Sprint 2 / Р-1 step 2 fix (F-101, 2026-05-22): абсолютные пороги
        # больше не хардкодятся — читаются из БД (absolute_thresholds).
        # Seed: _seed_absolute_thresholds в health_db. Источник — threshold_analysis
        # (личные p10) или ESC/AHA (клинические). При повторе threshold_analysis
        # обновлять через UPDATE/INSERT в absolute_thresholds, не в коде.
        for floor in db.get_absolute_thresholds_for_person("floor"):
            info = pcts.get(floor["metric"])
            if info is None:
                continue
            val_f = info["value"]
            # sleep_deep хранится в часах, показываем в минутах
            display_val = val_f * 60 if floor["metric"] == "sleep_deep" else val_f
            if val_f < floor["value"]:
                fired.append({
                    "metric": floor["metric"], "pct": 0, "val": val_f, "mean": info["mean"],
                    "target": "абсолютный пол", "r": 1.0,
                    "reason": floor["reason_template"].format(val=display_val),
                })

        for ceil in db.get_absolute_thresholds_for_person("ceiling"):
            info = pcts.get(ceil["metric"]) or _raw_only_yesterday(ceil["metric"])
            if info is None:
                continue
            val_f = info["value"]
            if val_f > ceil["value"]:
                fired.append({
                    "metric": ceil["metric"], "pct": 0, "val": val_f, "mean": info["mean"],
                    "target": "абсолютный потолок", "r": 1.0,
                    "reason": ceil["reason_template"].format(val=val_f),
                })

        # Трендовые/оконные пороги (N дней подряд / скользящее среднее) — BL-ALERT-TREND-1.
        # Дополняют single-day floors: конституции формулируют часть порогов как тренды
        # (sleep_score 14-дн→онколог, sleep_deep 3-ночи, hrv/readiness 2-дня подряд).
        _trend_medical = False
        try:
            import trend_alerts
            for t in trend_alerts.evaluate_trends(domain):
                fired.append({
                    "metric": t["metric"], "pct": 0, "val": 0, "mean": 0,
                    "target": f"тренд/{t['level']} ({t['window_days']}д)", "r": 1.0,
                    "reason": t["reason"],
                })
                if t["level"] == "medical":
                    _trend_medical = True
        except Exception as _te:  # noqa: BLE001 — тренды не роняют движок
            log.error("evaluate_domain_need(%s): trend_alerts упал: %s", domain, _te)

        if not fired:
            return {"needed": False, "urgency": "none", "reasons": [],
                    "protocols": [], "blocked_by": None}

        min_pct  = min(f["pct"] for f in fired)
        urgency  = "required" if (_trend_medical or len(fired) >= 2 or min_pct < 10) else "recommended"
        reasons  = [f["reason"] for f in fired]

        protocols = db.get_active_protocols(domain=domain)
        if not protocols:
            return {"needed": False, "urgency": "none", "reasons": [],
                    "protocols": [], "blocked_by": "нет активных протоколов для домена"}

        for proto in protocols:
            for c in db.get_active_constraints(protocol_id=proto["id"]):
                if c["action"] != "prohibit":
                    continue
                cond = c.get("condition") or ""
                blocked = (
                    ("time_of_day=evening" in cond and now_h >= 18) or
                    ("time_of_day=morning" in cond and now_h < 10) or
                    ("time_of_day=night"   in cond and (now_h >= 22 or now_h < 6))
                )
                if blocked:
                    log.debug(f"evaluate_domain_need({domain}): заблокировано — {c['reason']}")
                    return {"needed": False, "urgency": "none", "reasons": [],
                            "protocols": protocols, "blocked_by": c["reason"]}

        return {"needed": True, "urgency": urgency, "reasons": reasons,
                "protocols": protocols, "blocked_by": None}

    except Exception as e:
        # Тихий отказ был багом (обзор 2026-07-02): любой KeyError/DB-сбой →
        # needed=False на debug-уровне = движок рекомендаций молча замолкает,
        # никто не узнаёт. Fail-safe для пайплайна сохраняем (не роняем утро),
        # но: (1) ГРОМКО в лог, (2) наблюдаемый ключ 'error' — датчик
        # integrity_tests.check_recommendation_engine_health его видит и
        # доставляет в triage. Fallback без датчика = баг.
        log.error("evaluate_domain_need(%s) упал в error-path: %s",
                  domain, e, exc_info=True)
        return {"needed": False, "urgency": "none", "reasons": [],
                "protocols": [], "blocked_by": None, "error": str(e)}
