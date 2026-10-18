"""
services/recommendations.py — domain-need evaluator (без Telegram-привязки).

evaluate_domain_need читает DOMAIN_SIGNALS из health.db (system_config conit) и
сверяет персональные перцентили (90д baseline) с порогами домена →
{needed, urgency, reasons, protocols, blocked_by}.

evaluate_safety_limits — пол безопасности без домена и протокола: абсолютные полы/потолки
(absolute_thresholds) и трендовые пороги (trend_thresholds) → {fired, error?}.

Используется в jobs/recommendations (cron каждые 2ч) и может быть переиспользована
в handlers/ или dashboard.

Вынесено из telegram_bot.py в Sprint 6 (C9, 2026-05-23).
"""
from __future__ import annotations

import logging
import i18n

import health_db as db

log = logging.getLogger(__name__)

# Расписание задачи рекомендаций (jobs/scheduled.check_recommendations_scheduled) — один дом:
# задача берёт его отсюда, ночная проверка живости пола выводит из него допустимое молчание.
WINDOW_HOURS = (9, 21)          # местные часы, включительно
INTERVAL_S = 7200
LIMITS_CHECKED_KEY = "recommendations.limits_checked_at"   # пульс пола: UTC ISO, пишет задача
LIMITS_SEND_KEY = "recommendations.limits_send"   # 1 — пол доставляется человеку; нет ключа — только лог


def limits_max_silence_s() -> int:
    """Самая длинная честная пауза пульса: ночь вне окна плюс два интервала задачи."""
    start, end = WINDOW_HOURS
    return (24 - (end + 1 - start)) * 3600 + 2 * INTERVAL_S


def limits_silence_s(conn, now) -> float | None:
    """Секунды с последнего пульса пола в базе тенанта conn; None — пульса нет или он не читается."""
    import config_db
    from datetime import datetime
    at = config_db.get_config(LIMITS_CHECKED_KEY, conn=conn)
    if not at:
        return None
    try:
        return (now - datetime.fromisoformat(str(at))).total_seconds()
    except ValueError:
        log.warning("пульс пола не читается: %r", at)
        return None


def limits_stale(conn, now) -> bool:
    """Пол у тенанта молчит дольше честной паузы расписания (или не бежал ни разу)."""
    s = limits_silence_s(conn, now)
    return s is None or s > limits_max_silence_s()



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
        # DOMAIN_SIGNALS читаются из system_config (health.db) — единый conit.
        # Fallback: пустой список → домен не поддерживается.
        _raw_signals = db.get_domain_signals(domain)
        # r сигнала не читается (15.10): на срабатывание он не влиял, а его копия в таблице была
        # снимком калибровки мимо гейта; у силы связи один дом — принятая вера (BL-CORR-PROVENANCE-1 п.6).
        signals_def = [
            (s["metric"], s["label"], s["threshold_pct"])
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
        for metric, target_label, threshold in signals_def:
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
                    "reason":   _signal_reason(metric, val, mean, pct, domain=domain),
                })

        # Абсолютные полы/потолки и трендовые пороги отсюда уехали в evaluate_safety_limits
        # (нить floors-live, 07.10): задача звала эту функцию только для доменов активных
        # протоколов, домен пуст у всех протоколов обоих тенантов — пороги не проверялись вживую.

        if not fired:
            return {"needed": False, "urgency": "none", "reasons": [],
                    "protocols": [], "blocked_by": None}

        min_pct  = min(f["pct"] for f in fired)
        urgency  = "required" if (len(fired) >= 2 or min_pct < 10) else "recommended"
        reasons  = [f["reason"] for f in fired]

        protocols = db.get_active_protocols(domain=domain)
        if not protocols:
            return {"needed": False, "urgency": "none", "reasons": [],
                    "protocols": [], "blocked_by": "нет активных протоколов для домена"}

        # Запрет по времени суток (patient_constraints) снят 07.10: таблица удалена 18.05,
        # get_active_constraints отдаёт action=type, «prohibit» невозможен — проверка была мертва.
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


def _pushed(rule: dict) -> bool:
    """Доходит ли сработавший порог до человека сообщением (решение владельца 18.10, вариант «а»).

    Личный порог (norm_kind='personal': свои нижние 10% или верхние 10%) срабатывает по построению
    в каждой десятой ночи; за 30 дней до 18.10 — в 9 днях из 31. Одна такая ночь — не тревога
    (замысел proactivity.trend_not_spike), её картину несёт утренний бриф (полосы сна и
    восстановления). Сообщением идёт всё остальное: клинический порог (давление) и строка без
    вида — неизвестный вид доставляется, а не глушится (fail-safe).
    """
    return (rule.get("norm_kind") or "unclassified") != "personal"


def evaluate_safety_limits() -> dict:
    """Абсолютные полы/потолки (absolute_thresholds) и трендовые пороги (trend_thresholds) —
    без домена и протокола. Пол безопасности не зависит от того, есть ли у человека протокол:
    задача рекомендаций зовёт это раз в прогон (нить floors-live, 07.10).

    → {"fired": [{metric, reason, medical, push}], "error"?: str}. push=False — личный порог:
    считается и пишется в лог, но сообщением не идёт (_pushed). Ошибка — громко в лог и ключом,
    как у evaluate_domain_need: датчик видит ключ, пайплайн не падает.
    """
    try:
        pcts = db.get_metric_percentiles(baseline_days=90)
        fired = []
        # Пороги из БД (absolute_thresholds), не из кода: seed _seed_absolute_thresholds,
        # источник — threshold_analysis (личные p10) или клинический документ.
        # Только свои строки: variant='' и kind='absolute'. Таблица общая — safety_net,
        # lifestyle-агенты и цели держат там свои варианты, а кратный порог (kind='relative':
        # «+8 к среднему за 30 дней») число абсолютным не является. Повтор 30 дней 07.10:
        # без фильтра «ЧСС +8» срабатывала каждый день (любой пульс > 8).
        def _own(kind_dir):
            return [t for t in db.get_absolute_thresholds_for_person(kind_dir)
                    if not (t.get("variant") or "") and (t.get("kind") or "absolute") == "absolute"]
        for floor in _own("floor"):
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
                    "push": _pushed(floor),
                })

        for ceil in _own("ceiling"):
            info = pcts.get(ceil["metric"]) or _raw_only_yesterday(ceil["metric"])
            if info is None:
                continue
            val_f = info["value"]
            if val_f > ceil["value"]:
                fired.append({
                    "metric": ceil["metric"], "pct": 0, "val": val_f, "mean": info["mean"],
                    "target": "абсолютный потолок", "r": 1.0,
                    "reason": ceil["reason_template"].format(val=val_f),
                    "push": _pushed(ceil),
                })

        # Трендовые/оконные пороги (N дней подряд / скользящее среднее) — BL-ALERT-TREND-1:
        # все правила тенанта (domain=None), гейт по состоянию — внутри evaluate_trends.
        result = {"fired": fired}
        try:
            import trend_alerts
            for t in trend_alerts.evaluate_trends(None):
                fired.append({
                    "metric": t["metric"], "pct": 0, "val": 0, "mean": 0,
                    "target": f"тренд/{t['level']} ({t['window_days']}д)", "r": 1.0,
                    "reason": t["reason"], "medical": t["level"] == "medical",
                })
        except Exception as _te:  # noqa: BLE001 — тренды не гасят полы; отказ наблюдаем ключом
            log.error("evaluate_safety_limits: trend_alerts упал: %s", _te, exc_info=True)
            result["error"] = f"trend_alerts: {_te}"
        return result
    except Exception as e:
        log.error("evaluate_safety_limits упал в error-path: %s", e, exc_info=True)
        return {"fired": [], "error": str(e)}
