#!/usr/bin/env python3.11
"""
hai_analysis — индекс восстановления, детекция дрейфа метрик.
Зависимости: health_db.
Не импортирует другие hai_* модули.
"""

from datetime import date, timedelta
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
import health_db as db
from _time_inject import get_today


# ── Recovery Index ────────────────────────────────────────────────────────

def compute_recovery_index(target: date = None, window_days: int = 7, conn=None) -> dict:
    """
    Индекс восстановления: сравнивает текущие показатели с доболезненным
    baseline — доболезненный период тенанта.

    Независимо выдуманный пример результата (не наблюдения человека):
    {
      "date": "2042-05-19",
      "window_days": 11,
      "metrics": {
        "deep_min":  {"current": 54.0, "baseline": 90.0, "pct": 60.0},
        "rem_min":   {"current": 72.0, "baseline": 120.0, "pct": 60.0},
        "hrv_ms":    {"current": 42.0, "baseline": 70.0, "pct": 60.0},
        "readiness": {"current": 88.0, "baseline": 80.0, "pct": 100.0},
        "steps":     {"current": 3600, "baseline": 6000, "pct": 60.0},
      },
      "composite": 65.0,
      "n_days": 9,
      "baseline_kind": "recent_year",
    }
    """
    WEIGHTS = {
        "deep_min": 2, "rem_min": 2, "hrv_ms": 2, "readiness": 1, "steps": 1,
    }

    if target is None:
        target = get_today()

    since = target - timedelta(days=window_days)
    _own = conn is None            # recovery_series передаёт общий conn на 60 вызовов
    if _own:
        conn = db.get_conn()
    base = recovery_baseline(conn)
    if base is None:
        if _own:
            conn.close()
        return {"date": str(target), "window_days": window_days, "metrics": {}, "composite": None,
                "n_days": 0, "baseline_kind": None}
    rows = conn.execute(
        """SELECT sleep_deep, sleep_rem, hrv, readiness, steps
           FROM daily_metrics
           WHERE date > ? AND date <= ? AND sleep_deep > 0
           ORDER BY date""",
        (str(since), str(target))
    ).fetchall()
    if _own:
        conn.close()

    if not rows:
        return {"date": str(target), "window_days": window_days, "metrics": {}, "composite": None,
                "n_days": 0, "baseline_kind": base["kind"]}

    n         = len(rows)
    avg_deep  = sum(r[0] * 60 for r in rows if r[0]) / n
    avg_rem   = sum(r[1] * 60 for r in rows if r[1]) / n
    avg_hrv   = sum(r[2]      for r in rows if r[2]) / max(1, sum(1 for r in rows if r[2]))
    avg_ready = sum(r[3]      for r in rows if r[3]) / max(1, sum(1 for r in rows if r[3]))
    avg_steps = sum(r[4]      for r in rows if r[4]) / max(1, sum(1 for r in rows if r[4]))

    currents = {
        "deep_min":  round(avg_deep,  1),
        "rem_min":   round(avg_rem,   1),
        "hrv_ms":    round(avg_hrv,   1),
        "readiness": round(avg_ready, 1),
        "steps":     round(avg_steps, 0),
    }

    metrics = {}
    for key in WEIGHTS:
        baseline_val = base["values"][key]
        current_val = currents[key]
        pct = min(100.0, round(current_val / baseline_val * 100, 1))
        metrics[key] = {"current": current_val, "baseline": baseline_val, "pct": pct}

    total_weight = sum(WEIGHTS.values())
    composite    = round(
        sum(metrics[k]["pct"] * WEIGHTS[k] for k in WEIGHTS) / total_weight, 1
    )

    return {
        "date":        str(target),
        "window_days": window_days,
        "metrics":     metrics,
        "composite":   composite,
        "n_days":      n,
        "baseline_kind": base["kind"],
    }


_BASELINE_MIN_DAYS = 30   # меньше месяца — не «обычный уровень», индекс не считаем
_baseline_cache: dict = {}


def _baseline_window(conn) -> tuple:
    """(from, to, kind) окна «доболезненного/обычного» уровня ТЕНАНТА — из его данных:
    1) system_config recovery.baseline_window {"from","to"} — явная настройка установки;
    2) его период type='baseline' в таблице periods (дом «до болезни»);
    3) иначе — последний год его ряда («обычный уровень»)."""
    try:
        row = conn.execute("SELECT value_json FROM system_config "
                           "WHERE key='recovery.baseline_window'").fetchone()
        if row and row[0]:
            import json as _json
            w = _json.loads(row[0])
            return str(w["from"]), str(w["to"]), "config"
    except Exception as e:  # noqa: BLE001 — нет таблицы/битый JSON → следующий источник, громко
        print(f"hai_analysis: recovery.baseline_window не прочитан: {e}", file=sys.stderr)
    try:
        row = conn.execute("SELECT start_date, COALESCE(end_date, date('now')) FROM periods "
                           "WHERE type='baseline' AND deleted_at IS NULL "
                           "ORDER BY start_date LIMIT 1").fetchone()
        if row:
            return str(row[0]), str(row[1]), "baseline_period"
    except Exception as e:  # noqa: BLE001 — нет таблицы periods → последний год
        print(f"hai_analysis: periods не прочитан: {e}", file=sys.stderr)
    today = get_today()
    return str(today - timedelta(days=365)), str(today), "recent_year"


def recovery_baseline(conn) -> dict | None:
    """Средние тенанта за его окно baseline; см. _baseline_window.
    Общий baseline подменял бы данные разных тенантов. None — < 30 дней."""
    lo, hi, kind = _baseline_window(conn)
    try:   # ключ — файл БД тенанта (процесс служит одному тенанту; в тестах баз много)
        dbfile = conn.execute("PRAGMA database_list").fetchone()[2]
    except Exception:  # noqa: BLE001 — нет PRAGMA (мок) → без кэша по файлу
        dbfile = str(id(conn))
    key = (dbfile, lo, hi)
    if key in _baseline_cache:
        return _baseline_cache[key]
    rows = conn.execute(
        """SELECT sleep_deep, sleep_rem, hrv, readiness, steps FROM daily_metrics
           WHERE date >= ? AND date <= ? AND sleep_deep > 0""", (lo, hi)).fetchall()
    out = None
    if len(rows) >= _BASELINE_MIN_DAYS:
        def _mean(i, k=1.0):
            v = [r[i] * k for r in rows if r[i]]
            return round(sum(v) / len(v), 2) if v else None
        vals = {"deep_min": _mean(0, 60), "rem_min": _mean(1, 60), "hrv_ms": _mean(2),
                "readiness": _mean(3), "steps": _mean(4)}
        if all(vals.values()):
            out = {"values": vals, "kind": kind, "window": (lo, hi), "n": len(rows)}
    _baseline_cache[key] = out
    return out


def recovery_series(target: date, days: int = 60, window_days: int = 7) -> list:
    """Trailing композиты (НОВЕЙШИЙ первым) за `days` дней — ряд для личной полосы
    восстановления (promote 2026-08-05, нить profile-staleness). Дни без данных
    (composite None) пропускаются. Ре-использует compute_recovery_index — единственный
    дом формулы композита (§дубль); один conn на все вызовы, не 60 соединений."""
    out: list = []
    conn = db.get_conn()
    try:
        for i in range(days):
            ri = compute_recovery_index(target=target - timedelta(days=i),
                                        window_days=window_days, conn=conn)
            c = ri.get("composite")
            if c is not None:
                out.append(c)
    finally:
        conn.close()
    return out


def format_recovery_index(ri: dict) -> str:
    """
    Текстовая строка для инжекта в контекст GP:
    Для выдуманного примера compute_recovery_index:
    'Индекс восстановления (11д avg, n=9): deep 60% | REM 60% | HRV 60% | readiness 100% | steps 60% → composite 65% от обычного уровня за последний год'
    """
    if not ri.get("metrics"):
        return ""
    m     = ri["metrics"]
    parts = [
        f"deep {m['deep_min']['pct']:.0f}%",
        f"REM {m['rem_min']['pct']:.0f}%",
        f"HRV {m['hrv_ms']['pct']:.0f}%",
        f"readiness {m['readiness']['pct']:.0f}%",
        f"steps {m['steps']['pct']:.0f}%",
    ]
    return (
        f"Индекс восстановления ({ri['window_days']}д avg, n={ri['n_days']}): "
        + " | ".join(parts)
        + f" → composite {ri['composite']:.0f}% "
        + ("от обычного уровня за последний год" if ri.get("baseline_kind") == "recent_year"
           else "от доболезненного уровня")
    )


# ── Drift Detection ───────────────────────────────────────────────────────

# Колонки пяти исходных метрик дрейфа (у них свои имена и единицы: deep_min, hrv_ms…).
_DRIFT_LEGACY_COLS = ("sleep_deep", "sleep_rem", "hrv", "readiness", "steps")
DRIFT_LEGACY_METRICS = ("deep_min", "rem_min", "hrv_ms", "readiness", "steps")
# Время суток: сдвиг «на 15%» у него смысла не имеет (текстовые колонки и так дают None).
_DRIFT_SKIP = ("sleep_start", "sleep_end")

def detect_metric_drift(target: date = None, window: int = 7, streak_threshold: int = 3) -> list[dict]:
    """
    Детектирует устойчивый дрейф метрик относительно личного rolling baseline.

    Алгоритм:
    - Берём последние 60 дней данных
    - Для каждого дня вычисляем 7-дневное скользящее среднее
    - Если скользящее среднее отклоняется от 30-дневного baseline >15%
      на протяжении streak_threshold дней подряд → дрейф зафиксирован

    Независимо выдуманный пример элемента результата:
    [
      {
        "metric": "deep_min",
        "direction": "down",
        "streak_days": 9,
        "current_7d": 91.0,
        "baseline_30d": 130.0,
        "delta_pct": -30.0,
        "severity": "moderate",   # "mild" (15-25%) | "moderate" (25-40%) | "severe" (>40%)
      },
    ]
    """
    if target is None:
        target = get_today()

    since = target - timedelta(days=60)
    conn  = db.get_conn()
    # BL-DATA-PARITY-1 (24.09): под наблюдением дрейфа — ВСЕ числовые колонки, список из
    # схемы (не литерал). Пять исходных метрик сохраняют свои имена и единицы (на них
    # завязаны ключи карточек брифа drift:deep_min:down и т.д.); остальные идут под
    # именем колонки. Правило одно для всех: >15% от личной базы ≥streak дней подряд.
    extra = [c for c in db.metric_columns(conn)
             if c not in _DRIFT_LEGACY_COLS and c not in _DRIFT_SKIP]
    rows  = conn.execute(
        f"""SELECT date, sleep_deep, sleep_rem, hrv, readiness, steps
                   {''.join(', ' + c for c in extra)}
           FROM daily_metrics
           WHERE date >= ? AND date <= ?
           ORDER BY date""",
        (str(since), str(target))
    ).fetchall()
    conn.close()

    if len(rows) < 14:
        return []

    data: dict[str, dict] = {}
    for r in rows:
        data[r[0]] = {
            "deep_min":  r[1] * 60 if r[1] and r[1] > 0 else None,
            "rem_min":   r[2] * 60 if r[2] and r[2] > 0 else None,
            "hrv_ms":    r[3]       if r[3] and r[3] > 0 else None,
            "readiness": float(r[4]) if r[4] else None,
            "steps":     float(r[5]) if r[5] and r[5] > 0 else None,
        }
        for i, c in enumerate(extra, start=6):
            v = r[i]
            data[r[0]][c] = float(v) if isinstance(v, (int, float)) else None

    all_dates = sorted(data.keys())
    metrics   = [*DRIFT_LEGACY_METRICS, *extra]
    drifts    = []

    for metric in metrics:
        series = [(d, data[d][metric]) for d in all_dates if data[d][metric] is not None]
        if len(series) < 14:
            continue

        baseline_vals = [v for _, v in series[:30]]
        if len(baseline_vals) < 10:
            continue
        baseline_30d = sum(baseline_vals) / len(baseline_vals)
        if baseline_30d == 0:
            continue

        recent     = [v for _, v in series[-window:]]
        if len(recent) < 3:
            continue
        current_7d = sum(recent) / len(recent)
        delta_pct  = (current_7d - baseline_30d) / baseline_30d * 100

        THRESHOLD = 15.0
        streak    = 0
        for _, v in reversed(series):
            day_delta_pct = (v - baseline_30d) / baseline_30d * 100
            if abs(day_delta_pct) > THRESHOLD and (
                (day_delta_pct < 0) == (delta_pct < 0)
            ):
                streak += 1
            else:
                break

        if streak < streak_threshold:
            continue

        abs_delta = abs(delta_pct)
        severity  = "mild" if abs_delta < 25 else ("moderate" if abs_delta < 40 else "severe")

        drifts.append({
            "metric":       metric,
            "direction":    "down" if delta_pct < 0 else "up",
            "streak_days":  streak,
            "current_7d":   round(current_7d, 1),
            "baseline_30d": round(baseline_30d, 1),
            "delta_pct":    round(delta_pct, 1),
            "severity":     severity,
        })

    return drifts


def format_drift_report(drifts: list[dict]) -> str:
    """
    Краткий текст для инжекта в контекст GP.
    Возвращает пустую строку если дрейфов нет.
    """
    if not drifts:
        return ""

    LABELS = {
        "deep_min":  "Глубокий сон",
        "rem_min":   "REM",
        "hrv_ms":    "ВСР",
        "readiness": "Восстановление",
        "steps":     "Шаги",
    }
    POSITIVE_UP = {"deep_min", "rem_min", "hrv_ms", "readiness", "steps"}

    pos, neg, neutral = [], [], []
    for d in drifts:
        if d["metric"] not in LABELS:
            # BL-DATA-PARITY-1: у новых колонок «хорошее направление» не объявлено — это
            # клиническое суждение, код его не выдумывает; сдвиг показывается нейтрально.
            neutral.append(d)
            continue
        going_up = d["direction"] == "up"
        good_dir = (d["metric"] in POSITIVE_UP and going_up) or \
                   (d["metric"] not in POSITIVE_UP and not going_up)
        (pos if good_dir else neg).append(d)

    lines = []
    if neg:
        lines.append("ДРЕЙФ МЕТРИК (устойчивое снижение, >3д):")
        for d in neg:
            label = LABELS.get(d["metric"], d["metric"])
            lines.append(
                f"  ↓ {label}: {d['current_7d']:.1f} vs baseline {d['baseline_30d']:.1f}"
                f" ({d['delta_pct']:+.0f}%, {d['streak_days']}д, {d['severity']})"
            )
    if pos:
        lines.append("ПОЗИТИВНЫЙ ДРЕЙФ (устойчивый рост, >3д):")
        for d in pos:
            label = LABELS.get(d["metric"], d["metric"])
            lines.append(
                f"  ↑ {label}: {d['current_7d']:.1f} vs baseline {d['baseline_30d']:.1f}"
                f" ({d['delta_pct']:+.0f}%, {d['streak_days']}д)"
            )
    if neutral:
        import metrics_db
        lines.append("СДВИГ ПОКАЗАТЕЛЕЙ (устойчиво >3д; хорошо это или плохо — решает врач):")
        for d in neutral:
            label, unit = metrics_db.METRIC_LABELS.get(d["metric"], (d["metric"], ""))
            arrow = "↑" if d["direction"] == "up" else "↓"
            lines.append(
                f"  {arrow} {label}: {d['current_7d']:.1f} vs baseline {d['baseline_30d']:.1f}"
                f"{' ' + unit if unit else ''} ({d['delta_pct']:+.0f}%, {d['streak_days']}д)"
            )
    return "\n".join(lines)


# ── Корреляции между парами метрик (Wave 4-CORRELATIONS C-2) ──────────────

def detect_correlation_drift(
    target: date = None,
    window: int = 90,
    baseline_window: int = 90,
    threshold_r: float = 0.5,
    delta_threshold_pct: float = 20.0,
    min_pairs: int = 60,
) -> list[dict]:
    """Детектирует пары метрик с резко изменившейся корреляцией.

    Wave 4-CORRELATIONS C-2 (2026-05-12).

    Алгоритм:
    1. recent window: последние `window` дней.
    2. baseline window: предыдущие `baseline_window` дней (days window..window+baseline_window).
    3. Для каждой пары daily_metrics-колонок считаем Spearman r в обоих окнах.
    4. Возвращаем пары где:
       - |r_recent| >= threshold_r (значимая корреляция сейчас)
       - |r_recent - r_baseline| >= |r_baseline| * delta_threshold_pct/100 (значимый сдвиг)
       - n_pairs_recent >= min_pairs (достаточно валидных пар, не шум)

    Returns: list of {metric_a, metric_b, r_recent, r_baseline, delta_pct, n_pairs, severity}
    """
    import _time_inject
    from scipy.stats import spearmanr
    import sqlite3
    if target is None:
        target = _time_inject.get_today()

    METRICS = [
        "hrv", "resting_hr", "readiness",
        "sleep_total", "sleep_deep", "sleep_rem", "sleep_score", "sleep_efficiency",
        "steps", "active_kcal", "spo2_avg", "weight",
    ]

    # Recent window: target - window..target
    # Baseline window: target - window - baseline_window..target - window
    cols = ", ".join(METRICS)
    start_recent = target - timedelta(days=window)
    start_baseline = target - timedelta(days=window + baseline_window)

    conn = sqlite3.connect(str(db.DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        recent_rows = conn.execute(
            f"SELECT date, {cols} FROM daily_metrics "
            f"WHERE date >= ? AND date < ?",
            (start_recent.isoformat(), target.isoformat()),
        ).fetchall()
        baseline_rows = conn.execute(
            f"SELECT date, {cols} FROM daily_metrics "
            f"WHERE date >= ? AND date < ?",
            (start_baseline.isoformat(), start_recent.isoformat()),
        ).fetchall()
    finally:
        conn.close()

    def _extract_pair(rows, m_a, m_b):
        """Возвращает (x_list, y_list) с парами где обе метрики не NULL."""
        xs, ys = [], []
        for r in rows:
            va, vb = r[m_a], r[m_b]
            if va is not None and vb is not None:
                xs.append(va)
                ys.append(vb)
        return xs, ys

    drifts = []
    for i, m_a in enumerate(METRICS):
        for m_b in METRICS[i + 1:]:
            xs_r, ys_r = _extract_pair(recent_rows, m_a, m_b)
            xs_b, ys_b = _extract_pair(baseline_rows, m_a, m_b)
            if len(xs_r) < min_pairs:
                continue
            if len(xs_b) < min_pairs:
                continue
            r_recent, _ = spearmanr(xs_r, ys_r)
            r_baseline, _ = spearmanr(xs_b, ys_b)
            # NaN-guard (constant variable → spearmanr returns NaN)
            try:
                if r_recent != r_recent or r_baseline != r_baseline:
                    continue
            except Exception:  # silent-ok: NaN check edge cases
                continue

            if abs(r_recent) < threshold_r:
                continue
            # delta как абсолютная разница r, нормированная на |baseline|
            if abs(r_baseline) < 0.01:
                # baseline почти ноль — любой ненулевой recent считается дрейфом
                delta_pct = 100.0 * abs(r_recent)
            else:
                delta_pct = 100.0 * (r_recent - r_baseline) / abs(r_baseline)
            if abs(delta_pct) < delta_threshold_pct:
                continue

            severity = "mild" if abs(delta_pct) < 40 else (
                "moderate" if abs(delta_pct) < 80 else "strong"
            )
            drifts.append({
                "metric_a": m_a,
                "metric_b": m_b,
                "r_recent": round(r_recent, 3),
                "r_baseline": round(r_baseline, 3),
                "delta_pct": round(delta_pct, 1),
                "n_pairs_recent": len(xs_r),
                "n_pairs_baseline": len(xs_b),
                "severity": severity,
            })

    # Сортировка по убыванию |delta_pct| — наиболее значимые первыми
    drifts.sort(key=lambda d: abs(d["delta_pct"]), reverse=True)
    return drifts


def format_correlation_drift_report(drifts: list[dict]) -> str:
    """Краткий текст для инжекта в контекст GP. См. C-4 (P5-future)."""
    if not drifts:
        return ""
    lines = ["Изменившиеся корреляции (90д vs prev 90д):"]
    for d in drifts[:5]:
        sign_recent = "+" if d["r_recent"] >= 0 else ""
        sign_baseline = "+" if d["r_baseline"] >= 0 else ""
        lines.append(
            f"  {d['metric_a']} ↔ {d['metric_b']}: "
            f"r={sign_recent}{d['r_recent']} (было {sign_baseline}{d['r_baseline']}, "
            f"Δ{d['delta_pct']:+.0f}%, n={d['n_pairs_recent']}, {d['severity']})"
        )
    return "\n".join(lines)

