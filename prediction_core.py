"""prediction_core.py — ядро прогноза, который оценивает код: база, масштаб, окно наблюдения, суд.

# INTENT: prediction_core — замысел и инварианты в subsystem_intent.yaml.

Вынесено из protocol_outcomes (нить hypothesis-predictions, 11.11, Э1 плана plsi), чтобы тем же
судом судить прогнозы протоколов и гипотез. Поведение перенесено без изменений.
Правила: базу считает код, модель называет только изменение, интервал и горизонт; в оценку идут
значения строго после даты прогноза (без ретродикции); суд сравнивает с «ничего не изменится»;
интервал не шире protocols.max_change_spread обычных разбросов показателя.
Числа политики — общие ключи protocols.* (одна политика для протоколов и гипотез).
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import fmean, stdev

import config_db
import health_db
from _time_inject import get_today


class PredictionRejected(ValueError):
    """Прогноз модели не принят: неизвестный показатель, нет базы, кривые числа."""


def cfg(key: str) -> int:
    """Числа политики — в system_config (миграция кладёт значения), не литералом в коде."""
    value = config_db.get_config(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value) or value < 1:
        raise ValueError(f"{key}: ожидается целое число дней или точек ≥ 1")
    return int(value)


def daily_metrics(conn) -> list[str]:
    """Числовые колонки daily_metrics — единственные допустимые имена в SQL ниже."""
    return [r[1] for r in conn.execute("PRAGMA table_info(daily_metrics)")
            if r[1] != "date" and str(r[2]).upper() in ("REAL", "INTEGER")]


def predictable_metrics(created: date | None = None) -> list[str]:
    """Дневные показатели, у которых есть база на дату прогноза — их и предлагаем модели."""
    created = created or get_today()
    start = created - timedelta(days=cfg("protocols.baseline_days"))
    need = cfg("protocols.score_min_points")
    with health_db.get_conn() as conn:
        return [m for m in daily_metrics(conn) if conn.execute(
            f"SELECT COUNT({m}) FROM daily_metrics WHERE date>=? AND date<?",
            (str(start), str(created))).fetchone()[0] >= need]


def _has_labs(conn) -> bool:
    """lab_results рождается при первом анализе, не в init_db (свежая установка)."""
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lab_results'").fetchone())


def _num(x) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise PredictionRejected("число прогноза не число")
    return float(x)


def _baseline(conn, metric: str, kind: str, created: date) -> tuple[float, str, str, str | None]:
    """База прогноза. Дневной показатель — среднее до дня прогноза. Анализ — последний забор по день
    прогноза ВКЛЮЧИТЕЛЬНО: пересмотр гипотезы запускает приход анализа, и текст пишется уже с ним
    (замер 11.11, #795: версия от 29.08 цитирует HCT 47,6 от 29.08, а «строго до» давало 46,3 от 17.07).
    С наблюдением не пересекается: observed берёт заборы строго после дня прогноза."""
    if kind == "daily":
        span = cfg("protocols.baseline_days")
        start = created - timedelta(days=span)
        vals = [r[0] for r in conn.execute(
            f"SELECT {metric} FROM daily_metrics WHERE date>=? AND date<? AND {metric} IS NOT NULL",
            (str(start), str(created)))]
        if len(vals) < cfg("protocols.score_min_points"):
            raise PredictionRejected(f"мало точек для базы {metric}: {len(vals)}")
        return fmean(vals), str(start), str(created - timedelta(days=1)), None
    row = conn.execute(
        "SELECT value, date, unit FROM lab_results WHERE test_name=? AND date<=? AND value IS NOT NULL "
        "AND value_op IS NULL ORDER BY date DESC LIMIT 1", (metric, str(created))).fetchone()
    if not row:
        raise PredictionRejected(f"нет значения {metric} до прогноза")
    return float(row["value"]), row["date"], row["date"], row["unit"]


def _spread(conn, metric: str, kind: str, created: date) -> float | None:
    """Обычный разброс показателя до прогноза (стандартное отклонение): масштаб, в котором
    судится интервал прогноза. Уровень для этого не годится — у температурных отклонений и
    глубокого сна он меньше единицы (замер 11.11: 6 из 37 показателей). None — мерить не по чему."""
    if kind == "daily":
        start = created - timedelta(days=cfg("protocols.baseline_days"))
        vals = [r[0] for r in conn.execute(
            f"SELECT {metric} FROM daily_metrics WHERE date>=? AND date<? AND {metric} IS NOT NULL",
            (str(start), str(created)))]
    else:
        vals = [r[0] for r in conn.execute(
            "SELECT value FROM lab_results WHERE test_name=? AND date<? AND value IS NOT NULL "
            "AND value_op IS NULL", (metric, str(created)))]
    if len(vals) < 3:
        return None
    sd = stdev(vals)
    return sd if sd > 0 else None


def metric_scales(created: date | None = None) -> dict[str, tuple[float, float | None]]:
    """Показатель → (нынешний уровень, обычный разброс) для промпта: модель называет изменение
    в единицах показателя и видит его масштаб (баг 11.11: «+45» сна при уровне 5,85 часа)."""
    created = created or get_today()
    out = {}
    with health_db.get_conn() as conn:
        for m in predictable_metrics(created):
            base = _baseline(conn, m, "daily", created)[0]
            out[m] = (base, _spread(conn, m, "daily", created))
    return out


def _resolve(conn, metric: str) -> tuple[str, str]:
    """Имя показателя → (каноническое имя, вид daily|lab). Имя аналита — через lab_canon."""
    metric = str(metric or "").strip()
    if metric not in daily_metrics(conn):
        import lab_canon   # «Ферритин» → Ferritin: имя аналита живёт в одном доме
        metric = lab_canon.normalize(metric)
    if metric in daily_metrics(conn):
        return metric, "daily"
    if _has_labs(conn) and conn.execute(
            "SELECT 1 FROM lab_results WHERE test_name=? LIMIT 1", (metric,)).fetchone():
        return metric, "lab"
    raise PredictionRejected(f"неизвестный показатель {metric!r}")


def _satisfied(value: float, bound: str, threshold: float) -> bool:
    return value >= threshold if bound == "at_least" else value <= threshold


def _mid(raw: dict, key: str, low: float, high: float) -> float:
    """Точка прогноза; в тексте назван только интервал («на 5–10%») — его середина."""
    return (low + high) / 2 if raw.get(key) is None else _num(raw.get(key))


def accept_prediction(conn, raw, created: date, thresholds: bool = False) -> dict:
    """Прогноз модели → поля для хранения. Базу считает код; модель называет одно из четырёх:
    изменение с интервалом (change/low/high); проценты от базы (change_pct/low_pct/high_pct) — в единицы
    переводит код; цель «до X–Y» (target_low/target_high) — разницу с базой считает код; порог
    «не меньше / не больше X» (threshold + bound) — только где вызывающий хранит пороги (thresholds=True)."""
    if not isinstance(raw, dict):
        raise PredictionRejected("прогноза нет")
    raw = {k: v for k, v in raw.items() if v is not None}   # модель пишет ключи всех форм с null (#868, 11.11)
    days = raw.get("horizon_days")
    if (isinstance(days, bool) or not isinstance(days, int)
            or not cfg("protocols.horizon_min_days") <= days <= cfg("protocols.horizon_max_days")):
        raise PredictionRejected("горизонт вне допустимых границ")
    metric, kind = _resolve(conn, raw.get("metric"))
    base, bfrom, bto, unit = _baseline(conn, metric, kind, created)
    form, bound, threshold = "change", None, None
    if "threshold" in raw:
        bound, threshold = raw.get("bound"), _num(raw.get("threshold"))
        if not thresholds or bound not in ("at_least", "at_most"):
            raise PredictionRejected("пороговый прогноз здесь не принимается или не назван вид порога")
        form, change = "threshold", threshold - base
        low = high = change
    elif "change_pct" in raw or "low_pct" in raw:
        lo, hi = _num(raw.get("low_pct")), _num(raw.get("high_pct"))
        # показатель сам измеряется в процентах (HbA1c, HCT): «снизится на 0,1–0,2%» — это пункты, не доля
        # от уровня (замер 12.11: 4 из 8 прогонов читали HbA1c −0,1…−0,2% как −0,006…−0,012)
        scale = 1.0 if unit == "%" else base / 100
        parts = [scale * x for x in (lo, _mid(raw, "change_pct", lo, hi), hi)]
        low, change, high = min(parts[0], parts[2]), parts[1], max(parts[0], parts[2])
    elif "target_low" in raw:
        lo, hi = sorted((_num(raw.get("target_low")), _num(raw.get("target_high"))))
        low, change, high = lo - base, (lo + hi) / 2 - base, hi - base
    else:
        low, high = sorted((_num(raw.get("low")), _num(raw.get("high"))))
        change = _mid(raw, "change", low, high)
    if not low <= change <= high:
        raise PredictionRejected("изменение вне собственного интервала")
    spread = _spread(conn, metric, kind, created)
    limit = cfg("protocols.max_change_spread")
    if spread is not None and max(abs(low), abs(high)) > limit * spread:
        raise PredictionRejected(
            f"интервал {low:+.4g}…{high:+.4g} больше {limit} обычных разбросов {metric} "
            f"(разброс {spread:.3g}, уровень {base:.4g}) — вероятно, не те единицы")
    return dict(metric=metric, kind=kind, baseline=base, baseline_from=bfrom, baseline_to=bto, unit=unit,
                change=change, low=low, high=high, horizon=str(created + timedelta(days=days)),
                created=str(created), form=form, bound=bound, threshold=threshold)


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def judge_prediction(p: dict, observed: float) -> dict:
    """Попадание, направление и победа над «ничего не изменится». Порог: попал — наблюдение по нужную
    сторону; обыграл инерцию — только если на дату прогноза показатель был по другую сторону («удержится»
    при уже выполненном пороге инерция предсказывает сама — подтвердить нельзя, опровергнуть можно)."""
    y = observed - p["baseline"]
    if p.get("form") == "threshold":
        ok = _satisfied(observed, p["bound"], p["threshold"])
        held = _satisfied(p["baseline"], p["bound"], p["threshold"])
        return dict(observed=observed, within=int(ok), beat_persistence=int(ok and not held),
                    direction_correct=None if held else int(_sign(y) == _sign(p["change"])))
    error = y - p["change"]
    return dict(observed=observed, within=int(p["low"] <= y <= p["high"]),
                beat_persistence=int(abs(error) < abs(y)),
                direction_correct=None if p["change"] == 0 else int(_sign(y) == _sign(p["change"])))


def refutes(p: dict) -> bool:
    """Оценённый прогноз опровергает: мимо и показатель ушёл в противоположную сторону; у порога —
    ещё и когда выполненный на дату прогноза порог перестал выполняться. «Не сдвинулось» — не опровержение."""
    if p.get("status") != "scored" or p["within"]:
        return False
    moved = p["observed"] - p["baseline"]
    if p.get("form") == "threshold" and _satisfied(p["baseline"], p["bound"], p["threshold"]):
        return True
    return moved * p["change"] < 0


def observed(conn, p: dict, today: date) -> tuple[float | None, int, bool]:
    """(значение, число точек, окно закрыто). Окно daily — [горизонт−W, горизонт], ждёт горизонта;
    окно lab — горизонт ± W, ближайший к горизонту забор в той же единице, что и база: имя —
    через lab_canon.normalize, единица — равенством (тренд по веществу через lab_name_loinc здесь
    не нужен: оценивается одна точка против одной базы, и чужая единица честнее пропуска)."""
    horizon, created = date.fromisoformat(p["horizon"]), date.fromisoformat(p["created"])
    if p["kind"] == "daily":
        if p["metric"] not in daily_metrics(conn):
            raise ValueError(f"показатель {p['metric']!r} исчез из daily_metrics")
        start = max(horizon - timedelta(days=cfg("protocols.score_window_days")), created + timedelta(days=1))
        vals = [r[0] for r in conn.execute(
            f"SELECT {p['metric']} FROM daily_metrics WHERE date>=? AND date<=? AND {p['metric']} IS NOT NULL",
            (str(start), str(horizon)))]
        closed = today >= horizon
        enough = len(vals) >= cfg("protocols.score_min_points")
        return (fmean(vals) if closed and enough else None), len(vals), closed
    w = timedelta(days=cfg("protocols.lab_window_days"))
    rows = conn.execute(
        "SELECT value, date FROM lab_results WHERE test_name=? AND date>? AND date>=? AND date<=? "
        "AND date<=? AND value IS NOT NULL AND value_op IS NULL AND COALESCE(unit,'')=COALESCE(?,'')",
        (p["metric"], str(created), str(horizon - w), str(horizon + w), str(today), p["unit"])).fetchall()
    if not rows:
        return None, 0, today > horizon + w
    best = min(rows, key=lambda r: (abs((date.fromisoformat(r["date"]) - horizon).days), r["date"]))
    return float(best["value"]), 1, True
