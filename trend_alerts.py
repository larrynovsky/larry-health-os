#!/usr/bin/env python3.11
"""
trend_alerts.py — оконные/трендовые алерты (N дней подряд ИЛИ скользящее среднее).

Дополняет однодневные absolute floors (services/recommendations): конституции
формулируют часть порогов как тренды («sleep_score <70 три ночи подряд»,
«14-дн среднее <70 → онколог», «sleep_deep <0.6ч три ночи»), которые single-day
floor выразить не может. См. BL-ALERT-TREND-1.

§9: НОРМЫ (metric/value/window/mode/level) — в таблице `trend_thresholds` (данные);
оконная ЛОГИКА — здесь (код). Значения меняются UPDATE'ом в БД, не в коде.

Чистая _breached тестируется без БД (health_db импортится лениво в evaluate_trends).
"""
from __future__ import annotations


def _last_n(conn, metric: str, n: int) -> list[float]:
    """Последние n НЕ-NULL значений метрики из daily_metrics, свежие первыми.
    metric — имя колонки daily_metrics (валидируется по whitelist в get_trend_thresholds)."""
    rows = conn.execute(
        f'SELECT "{metric}" FROM daily_metrics '
        f'WHERE "{metric}" IS NOT NULL ORDER BY date DESC LIMIT ?',
        (n,),
    ).fetchall()
    return [float(r[0]) for r in rows]


def _breached(values: list[float], n: int, direction: str, value: float,
              mode: str) -> tuple[bool, float | None]:
    """values — свежие первыми. Возврат (нарушено?, вычисленное_значение).

    consecutive: нужно РОВНО n значений и ВСЕ нарушают (floor: <value; ceiling: >value).
    avg: скользящее среднее последних n нарушает порог.
    Недобор данных (<n) → не срабатывает (консервативно).
    """
    if len(values) < n:
        return False, None
    window = values[:n]
    if mode == "consecutive":
        if direction == "floor":
            return (all(v < value for v in window), min(window))
        return (all(v > value for v in window), max(window))
    # mode == "avg"
    m = sum(window) / len(window)
    if direction == "floor":
        return (m < value, m)
    return (m > value, m)


def evaluate_trends(domain: str | None = None, conn=None) -> list[dict]:
    """Активные trend_thresholds для домена (t.domain пустой = не привязан).

    Возврат: [{metric, level, window_days, mode, reason}, ...] — только сработавшие.
    """
    import health_db as _hdb   # ленивый импорт: чистый _breached тестируется без БД
    own = conn is None
    conn = conn or _hdb.get_conn()
    try:
        out = []
        _active_conds = None   # ленивый матч состояний тенанта (только если есть гейт-правило)
        for t in _hdb.get_trend_thresholds_for_person():
            if t.get("domain") and domain and t["domain"] != domain:
                continue
            # brief-neutralization Фаза 3a-2: condition_key гейтит правило по состоянию тенанта
            # (generic, без имени болезни в коде). Онко-специфичное правило (14д medical) уходит
            # только онко-тенанту. Пусто = не гейтится.
            _ck = t.get("condition_key") or ""
            if _ck:
                if _active_conds is None:
                    try:
                        import clinical_kb as _ckb
                        _active_conds = _ckb.active_conditions(conn)
                    except Exception:  # noqa: BLE001 — clinical_kb недоступна → не гейтим (fail-open по алерту, не по безопасности)
                        _active_conds = set()
                if _ck not in _active_conds:
                    continue   # состояния у тенанта нет → онко-специфичное правило не срабатывает
            vals = _last_n(conn, t["metric"], t["window_days"])
            hit, computed = _breached(vals, t["window_days"], t["direction"],
                                      t["value"], t["mode"])
            if hit:
                out.append({
                    "metric": t["metric"], "level": t["level"],
                    "window_days": t["window_days"], "mode": t["mode"],
                    "reason": t["reason_template"].format(
                        val=computed, n=t["window_days"], thr=t["value"]),
                })
        return out
    finally:
        if own:
            conn.close()
