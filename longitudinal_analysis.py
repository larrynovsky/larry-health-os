#!/usr/bin/env python3.11
"""
longitudinal_analysis.py — полный продольный анализ здоровья.

Что делает:
  1. Загружает клинические фазы из таблицы periods
  2. Аннотирует daily_metrics по фазам
  3. Погодовой тренд по доступным данным
  4. Сравнение фаз: среднее/медиана для всех метрик
  5. Матрица корреляций (Spearman) по всем метрикам + лаги
  6. Корреляции метрик с лабораторными данными
  7. Траектория восстановления vs pre-illness baseline
  8. Экспорт: Excel (4 листа) + JSON-саммари для ИИ
  9. Сохраняет результат в agent_reports

Запуск:
  python3.11 longitudinal_analysis.py [--out /path/to/output.xlsx]
"""

from _time_inject import get_now, get_today  # seam
import argparse
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
import openpyxl
import i18n

import lab_canon               # канон имён аналитов: семья объявляет имя, БД хранит вариант
import signal_family as _sf   # замороженная семья сигналов (§12.3): methodology/validation_gate/signal_family.yaml
from belief_contract import BELIEF_SCHEMA_VERSION, GATE_RUN_RECEIPT, artifact_path  # контракт веры (единый источник приёмки)
from openpyxl.styles import (
    Font, PatternFill, Alignment, Border, Side
)
from openpyxl.utils import get_column_letter
from openpyxl.formatting.rule import ColorScaleRule

DB_PATH = __import__("health_db").DB_PATH  # respects HEALTH_DATA_DIR



DEFAULT_OUT = artifact_path("outputs/longitudinal_analysis.xlsx")


# ── Метрики для анализа ───────────────────────────────────────────────────────

# Метки для отображения (presentation). Состав семьи (membership) — из signal_family.yaml.
_DAILY_LABELS = {
    "sleep_total":    'longitudinal.label.sleep_total',
    "sleep_deep":     'longitudinal.label.sleep_deep',
    "sleep_rem":      'longitudinal.label.sleep_rem',
    "sleep_score":    "Sleep score",
    "sleep_efficiency": "Sleep efficiency",
    "hrv":            'longitudinal.label.hrv',
    "resting_hr":     'safety.label.resting_hr',
    "readiness":      "Readiness",
    "steps":          'experiments.metric.steps',
    "active_kcal":    'longitudinal.label.active_kcal',
    "spo2_avg":       "SpO2 %",
    "weight":         'dashboard.profile.label.identity.weight_kg',
    "readiness_hrv_balance":  "HRV balance",
    "readiness_recovery_idx": "Recovery idx",
}
# Membership — из замороженной семьи (§12.3). Под-индексы readiness исключены (производные).
DAILY_METRICS = {k: _DAILY_LABELS.get(k, k) for k in _sf.DAILY_METRICS}

LAB_METRICS = list(_sf.LAB_METRICS)

# Only the mixed-language legacy label needs a separate read-compatibility alias.
_LONGITUDINAL_LEGACY_LABELS = {"Deep sleep, ч": "longitudinal.label.sleep_deep"}
_LONGITUDINAL_LABEL_KEYS = (
    "longitudinal.label.sleep_total",
    "longitudinal.label.sleep_deep",
    "longitudinal.label.sleep_rem",
    "longitudinal.label.hrv",
    "safety.label.resting_hr",
    "experiments.metric.steps",
    "longitudinal.label.active_kcal",
    "dashboard.profile.label.identity.weight_kg",
)


def _longitudinal_label(metric: str, lang: str | None = None) -> str:
    """Resolve metric keys and labels from earlier report rows in the tenant's language."""
    lang = lang or i18n.lang_of()
    value = DAILY_METRICS.get(metric, metric)
    key = _LONGITUDINAL_LEGACY_LABELS.get(value)
    if key:
        return i18n.t(key, lang)
    for key in _LONGITUDINAL_LABEL_KEYS:
        if value in (key, i18n.t(key, "ru"), i18n.t(key, "en")):
            return i18n.t(key, lang)
    return value


# ── Загрузка данных ───────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def load_phases(*, lang: str | None = None) -> list[dict]:
    """Загружает клинические фазы из periods + синтезирует пробелы.

    PERIODS-SEMANTICS (2026-06-19): мигрировано на historical_periods().
    include_deleted=False по дефолту — soft-deleted не попадают в longitudinal.
    """
    lang = lang or i18n.lang_of()
    import health_db as _db
    rows = _db.historical_periods()

    phases = []
    for r in rows:
        phases.append({
            "name":       r["name"],
            "type":       r["type"],
            "start":      r["start_date"],
            "end":        r["end_date"] or str(get_today()),
            "notes":      (r["notes"] or "")[:200],
        })

    # Добавляем pre-illness как отдельную фазу если нет в periods
    # Начало ряда — из данных тенанта (до 27.09 литерал начала ряда владельца, BL-PUB-16 а)
    with get_conn() as _c:
        earliest_data = (_c.execute("SELECT MIN(date) FROM daily_metrics").fetchone()[0]
                         or str(get_today()))
    first_medical = min((p["start"] for p in phases if p["type"] in
                         ("diagnostic","treatment","surgery")), default=str(get_today()))
    if earliest_data < first_medical:
        phases.insert(0, {
            "name":  "Pre-illness baseline",
            "type":  "baseline",
            "start": earliest_data,
            "end":   str((datetime.strptime(first_medical, "%Y-%m-%d") - timedelta(days=1)).date()),
            "notes": i18n.t("longitudinal.phase.before_procedure", lang),
        })

    return sorted(phases, key=lambda x: x["start"])


def load_daily_df() -> pd.DataFrame:
    """Загружает все daily_metrics в DataFrame."""
    conn = get_conn()
    cols = list(DAILY_METRICS.keys()) + ["date", "raw"]
    query = f"SELECT {', '.join(cols)} FROM daily_metrics ORDER BY date"
    df = pd.read_sql_query(query, conn)
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    # числовые столбцы — принудительно float
    import metrics_db   # время суток (семья v9): ISO-строка → минуты от полудня, один перевод с блоком врачей
    for col in DAILY_METRICS.keys():
        if col in df.columns:
            if col in metrics_db._CLOCK_COLUMNS:
                df[col] = df[col].map(metrics_db._clock_minutes)
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def annotate_phases(df: pd.DataFrame, phases: list[dict]) -> pd.DataFrame:
    """Добавляет колонку phase_name и phase_type."""
    df = df.copy()
    df["phase_name"] = "unknown"
    df["phase_type"] = "unknown"
    for ph in phases:
        mask = (df["date"] >= ph["start"]) & (df["date"] <= ph["end"])
        df.loc[mask, "phase_name"] = ph["name"]
        df.loc[mask, "phase_type"] = ph["type"]
    return df


def load_labs_df() -> pd.DataFrame:
    """Загружает lab_results в DataFrame."""
    conn = get_conn()
    df = pd.read_sql_query(
        "SELECT date, test_name, value, unit FROM lab_results WHERE specimen='blood' ORDER BY date",
        conn
    )
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df


# ── Аналитические функции ─────────────────────────────────────────────────────

def yearly_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Погодовой тренд по всем метрикам."""
    df2 = df.copy()
    df2["year"] = df2["date"].dt.year
    metric_cols = [c for c in DAILY_METRICS.keys() if c in df2.columns]
    agg = df2.groupby("year")[metric_cols].agg(["mean", "count"])
    agg.columns = ["_".join(c) for c in agg.columns]
    agg = agg.reset_index()
    return agg


def phase_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Среднее/медиана по каждой клинической фазе."""
    metric_cols = [c for c in DAILY_METRICS.keys() if c in df.columns]
    rows = []
    for phase_name, grp in df.groupby("phase_name"):
        row = {
            "phase":      phase_name,
            "phase_type": grp["phase_type"].iloc[0],
            "start":      str(grp["date"].min().date()),
            "end":        str(grp["date"].max().date()),
            "n_days":     len(grp),
        }
        for col in metric_cols:
            valid = grp[col].dropna()
            if len(valid) >= 5:
                row[f"{col}_mean"]   = round(float(valid.mean()), 2)
                row[f"{col}_median"] = round(float(valid.median()), 2)
                row[f"{col}_std"]    = round(float(valid.std()), 2)
            else:
                row[f"{col}_mean"]   = None
                row[f"{col}_median"] = None
                row[f"{col}_std"]    = None
        rows.append(row)
    out = pd.DataFrame(rows)
    # Сортируем по дате начала фазы
    out = out.sort_values("start").reset_index(drop=True)
    return out


def _typed_frame(records: list, schema: "dict[str, str]") -> pd.DataFrame:
    """DataFrame из записей; при ПУСТОМ входе — кадр с теми же колонками и типами, не `KeyError`.

    VG-R4-07: `pd.DataFrame([]).sort_values("spearman_r")` роняет `KeyError: 'spearman_r'` — и
    падает это ДО гейта, то есть до всякой квитанции. «Данных для корреляций не набралось»
    (законное состояние: новая установка, узкий срез, отфильтрованный аналит) выглядело как
    крэш неизвестной природы, а прогон не оставлял ни строки веры, ни следа отказа. Пустой
    типизированный кадр проходит весь путь честно: гейт видит 0 пар, квитанция закрывается,
    читатель узнаёт «вера не опубликована, потому что тестировать было нечего».

    Типы важны не меньше имён: `significant`/`strong` обязаны быть bool, иначе булева маска
    на пустой object-колонке ведёт себя иначе, чем на непустой — и тест на пустом входе
    перестал бы говорить что-либо о боевом пути."""
    if records:
        return pd.DataFrame(records)
    return pd.DataFrame({c: pd.Series(dtype=t) for c, t in schema.items()})


_CORR_SCHEMA = {"metric_a": "object", "label_a": "object", "metric_b": "object",
                "label_b": "object", "spearman_r": "float64", "p_value": "float64",
                "n": "int64", "significant": "bool", "strong": "bool"}
_LAGGED_SCHEMA = {"predictor": "object", "target": "object", "lag_days": "int64",
                  "spearman_r": "float64", "p_value": "float64", "n": "int64",
                  "significant": "bool"}
_LABCORR_SCHEMA = {"lab": "object", "metric": "object", "label": "object",
                   "spearman_r": "float64", "p_value": "float64", "n": "int64",
                   "significant": "bool", "strong": "bool"}


def correlation_matrix(df: pd.DataFrame, min_pairs: int = _sf.PAIR_MIN_OVERLAP) -> pd.DataFrame:
    """
    Spearman-корреляция между всеми парами метрик.
    Возвращает длинный DataFrame: metric_a, metric_b, r, p, n.
    """
    metric_cols = [c for c in DAILY_METRICS.keys() if c in df.columns]
    records = []
    for i, a in enumerate(metric_cols):
        for b in metric_cols[i+1:]:
            paired = df[[a, b]].dropna()
            if len(paired) < min_pairs:
                continue
            r, p = stats.spearmanr(paired[a], paired[b])
            records.append({
                "metric_a": a, "label_a": _longitudinal_label(a),
                "metric_b": b, "label_b": _longitudinal_label(b),
                "spearman_r": round(float(r), 3),
                "p_value":    round(float(p), 5),
                "n":          len(paired),
                "significant": p < 0.05,
                "strong":      abs(r) >= 0.3,
            })
    return _typed_frame(records, _CORR_SCHEMA).sort_values("spearman_r", key=abs, ascending=False)


def lagged_correlations(df: pd.DataFrame, lags: "list[int] | None" = None) -> pd.DataFrame:
    """
    Корреляции с лагом: метрика X в день T vs метрика Y в день T+lag.
    Ищем опережающие индикаторы. Семья (predictors/targets/lags) — из signal_family.yaml
    (status pending_harness_validation: считаются для отчёта, в веру НЕ подаются до валидации).
    """
    if lags is None:
        lags = list(_sf.LAGGED["lags_days"])
    PREDICTORS = list(_sf.LAGGED["predictors"])
    TARGETS    = list(_sf.LAGGED["targets"])
    df_s = df.set_index("date").sort_index()
    records = []
    for lag in lags:
        for pred in PREDICTORS:
            for tgt in TARGETS:
                if pred == tgt:
                    continue
                if pred not in df_s.columns or tgt not in df_s.columns:
                    continue
                x = df_s[pred]
                y = df_s[tgt].shift(-lag)
                paired = pd.concat([x, y], axis=1).dropna()
                paired.columns = ["x", "y"]
                if len(paired) < _sf.PAIR_MIN_OVERLAP:
                    continue
                r, p = stats.spearmanr(paired["x"], paired["y"])
                records.append({
                    "predictor":   pred,
                    "target":      tgt,
                    "lag_days":    lag,
                    "spearman_r":  round(float(r), 3),
                    "p_value":     round(float(p), 5),
                    "n":           len(paired),
                    "significant": p < 0.05,
                })
    return _typed_frame(records, _LAGGED_SCHEMA).sort_values("spearman_r", key=abs,
                                                             ascending=False)


LAB_WINDOW_DAYS = _sf.LAB_WINDOW_DAYS  # A3 (22.09): дом окна лаб↔daily — signal_family.yaml; был второй литерал 7


def lab_metric_correlations(daily_df: pd.DataFrame, labs_df: pd.DataFrame,
                             window_days: int = LAB_WINDOW_DAYS) -> pd.DataFrame:
    """
    Корреляция лабораторных показателей с ежедневными метриками.
    Для каждого лабораторного значения берём среднее дневных метрик за ±window_days.
    """
    metric_cols = [c for c in DAILY_METRICS.keys() if c in daily_df.columns]
    records = []
    obs_n: dict[str, int] = {}   # аналит → сколько наблюдений увидел PRODUCER (паритет с гейтом)
    daily_indexed = daily_df.set_index("date").sort_index()

    # Матчинг по канону объединяет варианты написания имени. Строгое строковое
    # равенство может скрыть существующие точки и дать ложное «данных нет».
    _canon_col = (labs_df["test_name"].map(lab_canon.normalize)
                  if "test_name" in labs_df.columns else pd.Series(dtype=object))
    for lab_name in LAB_METRICS:
        _canon_lab = lab_canon.normalize(lab_name)
        lab_vals = labs_df[_canon_col == _canon_lab].dropna(subset=["value"])
        obs_n[lab_name] = int(len(lab_vals))
        if len(lab_vals) < 6:
            continue
        rows_for_lab = []
        for _, lab_row in lab_vals.iterrows():
            lab_date = lab_row["date"]
            window_start = lab_date - timedelta(days=window_days)
            window_end   = lab_date + timedelta(days=window_days)
            window_data  = daily_indexed.loc[
                (daily_indexed.index >= window_start) &
                (daily_indexed.index <= window_end)
            ]
            if len(window_data) < 2:
                continue
            row_agg = {"lab_value": lab_row["value"], "lab_date": lab_date}
            for m in metric_cols:
                if m in window_data.columns:
                    row_agg[m] = window_data[m].mean()
            rows_for_lab.append(row_agg)

        if len(rows_for_lab) < 5:
            continue
        df_lab = pd.DataFrame(rows_for_lab)
        for m in metric_cols:
            if m not in df_lab.columns:
                continue
            paired = df_lab[["lab_value", m]].dropna()
            if len(paired) < 5:
                continue
            r, p = stats.spearmanr(paired["lab_value"], paired[m])
            records.append({
                "lab":        lab_name,
                "metric":     m,
                "label":      _longitudinal_label(m),
                "spearman_r": round(float(r), 3),
                "p_value":    round(float(p), 5),
                "n":          len(paired),
                "significant": p < 0.05,
                "strong":     abs(r) >= 0.35,
            })
    out = _typed_frame(records, _LABCORR_SCHEMA).sort_values("spearman_r", key=abs,
                                                             ascending=False)
    # Провенанс выборки: сколько наблюдений увидел producer по каждому аналиту. Гейт кладёт
    # своё такое же число в attrs["lab_obs_n"]; расхождение = producer и гейт считали разные
    # выборки, и строка веры несёт несопоставимые числа (P2-01). Сверяется тестом паритета.
    out.attrs["producer_obs_n"] = obs_n
    return out


def _recent_cutoff_iso(days: int) -> str:
    """Порог «последние N дней» от get_today() как ISO-строка (для df-фильтра).
    Вынесено из recovery_trajectory для тестируемости time-contract: boundary-тест
    морозит клок и проверяет границу окна напрямую."""
    return str(get_today() - timedelta(days=days))


def recovery_trajectory(df: pd.DataFrame, phases: list[dict]) -> dict:
    """
    Сравнение текущего состояния с pre-illness baseline.
    Возвращает dict {metric: {baseline_mean, current_mean, pct_recovery, trend}}.
    """
    baseline_phase = next((p for p in phases if p["type"] == "baseline"), None)
    if not baseline_phase:
        return {}

    baseline_df = df[
        (df["date"] >= baseline_phase["start"]) &
        (df["date"] <= baseline_phase["end"])
    ]
    # "Текущее" = последние 90 дней
    cutoff_90 = _recent_cutoff_iso(90)
    current_df = df[df["date"] >= cutoff_90]

    result = {}
    for col in DAILY_METRICS.keys():
        if col not in df.columns:
            continue
        b_vals = baseline_df[col].dropna()
        c_vals = current_df[col].dropna()
        if len(b_vals) < 30 or len(c_vals) < 14:
            continue
        b_mean = float(b_vals.mean())
        c_mean = float(c_vals.mean())
        b_p25  = float(b_vals.quantile(0.25))
        b_p75  = float(b_vals.quantile(0.75))
        # pct_recovery: 100% = вернулся на baseline, 0% = нет движения,
        # может быть >100% если превысил
        if b_mean != 0:
            pct = round(c_mean / b_mean * 100, 1)
        else:
            pct = None
        # Тренд последних 90 дней (линейная регрессия)
        c_vals_sorted = current_df[["date", col]].dropna()
        trend_slope = None
        if len(c_vals_sorted) >= 14:
            xs = np.arange(len(c_vals_sorted))
            ys = c_vals_sorted[col].values.astype(float)
            slope, _, _, _, _ = stats.linregress(xs, ys)
            trend_slope = round(float(slope), 4)
        result[col] = {
            "label":          _longitudinal_label(col),
            "baseline_mean":  round(b_mean, 2),
            "baseline_p25":   round(b_p25, 2),
            "baseline_p75":   round(b_p75, 2),
            "current_mean":   round(c_mean, 2),
            "pct_of_baseline": pct,
            "trend_slope":    trend_slope,
            "trend_dir":      "↑" if trend_slope and trend_slope > 0 else ("↓" if trend_slope and trend_slope < 0 else "→"),
        }
    return result


def phase_correlations(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Матрица корреляций внутри каждой фазы."""
    result = {}
    for phase_name, grp in df.groupby("phase_name"):
        if len(grp) < 20:
            continue
        corr = correlation_matrix(grp, min_pairs=10)
        if not corr.empty:
            result[phase_name] = corr
    return result


# ── Excel-экспорт ─────────────────────────────────────────────────────────────

PHASE_COLORS = {
    "baseline":    "D6EAF8",
    "diagnostic":  "FDEBD0",
    "treatment":   "FADBD8",
    "surgery":     "F9EBEA",
    "travel":      "EBF5EB",
    "observation": "E8F8F5",
    "unknown":     "F2F3F4",
}

def _header_row(ws, headers: list[str], row: int = 1):
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=row, column=col, value=h)
        cell.font = Font(bold=True, color="FFFFFF", size=10)
        cell.fill = PatternFill("solid", fgColor="2C3E50")
        cell.alignment = Alignment(horizontal="center", wrap_text=True)


def _auto_width(ws, min_w=8, max_w=40):
    for col in ws.columns:
        max_len = max_w
        col_letter = get_column_letter(col[0].column)
        lens = []
        for cell in col:
            if cell.value is not None:
                lens.append(min(len(str(cell.value)), max_w))
        if lens:
            max_len = max(min_w, min(max(lens) + 2, max_w))
        ws.column_dimensions[col_letter].width = max_len


def write_sheet_yearly(wb: openpyxl.Workbook, yearly: pd.DataFrame, phases: list[dict], *, lang: str | None = None):
    lang = lang or i18n.lang_of()
    ws = wb.create_sheet(i18n.t("longitudinal.sheet.yearly", lang))
    key_metrics = ["sleep_total_mean", "sleep_deep_mean", "hrv_mean",
                   "resting_hr_mean", "readiness_mean", "steps_mean",
                   "sleep_score_mean", "weight_mean", "spo2_avg_mean"]
    display_cols = ["year"] + [c for c in key_metrics if c in yearly.columns]
    headers = [i18n.t("longitudinal.column.year", lang)] + [c.replace("_mean","").replace("_"," ") for c in display_cols[1:]]
    _header_row(ws, headers)

    # Добавим строку "кол-во дней" из count колонки
    phase_by_year = {}
    for ph in phases:
        for yr in range(
            int(ph["start"][:4]),
            int(ph["end"][:4]) + 1
        ):
            phase_by_year.setdefault(yr, []).append(ph["type"])

    for row_idx, (_, row) in enumerate(yearly.iterrows(), 2):
        yr = int(row["year"])
        ph_types = phase_by_year.get(yr, [])
        # Цвет строки по доминирующей фазе
        dominant = "baseline"
        for pt in ["treatment", "surgery", "diagnostic", "observation"]:
            if pt in ph_types:
                dominant = pt
                break
        fill_color = PHASE_COLORS.get(dominant, "FFFFFF")

        for col_idx, col_name in enumerate(display_cols, 1):
            val = row.get(col_name)
            cell = ws.cell(row=row_idx, column=col_idx)
            if pd.isna(val) if val is not None else True:
                cell.value = "—"
            elif col_name == "year":
                cell.value = int(val)
            elif "steps" in col_name:
                cell.value = int(val) if not pd.isna(val) else "—"
            else:
                cell.value = round(float(val), 2) if not pd.isna(val) else "—"
            cell.fill = PatternFill("solid", fgColor=fill_color)
            cell.alignment = Alignment(horizontal="center")

    # Легенда фаз
    ws.cell(row=row_idx+2, column=1, value=i18n.t("longitudinal.legend.row_colours", lang)).font = Font(bold=True)
    for i, (pt, color) in enumerate(PHASE_COLORS.items(), 3):
        c = ws.cell(row=row_idx+i, column=1, value=pt)
        c.fill = PatternFill("solid", fgColor=color)

    _auto_width(ws)


def write_sheet_phases(wb: openpyxl.Workbook, phase_df: pd.DataFrame, *, lang: str | None = None):
    lang = lang or i18n.lang_of()
    ws = wb.create_sheet(i18n.t("longitudinal.sheet.phases", lang))
    key_cols = ["phase", "phase_type", "start", "end", "n_days"]
    metric_cols = [f"{m}_mean" for m in DAILY_METRICS.keys()
                   if f"{m}_mean" in phase_df.columns]
    all_cols = key_cols + metric_cols
    headers = [i18n.t("longitudinal.column.phase", lang), i18n.t("longitudinal.column.type", lang), i18n.t("longitudinal.column.start", lang), i18n.t("longitudinal.column.end", lang), i18n.t("longitudinal.column.days", lang)] + \
              [c.replace("_mean","").replace("_"," ") for c in metric_cols]
    _header_row(ws, headers)

    for row_idx, (_, row) in enumerate(phase_df.iterrows(), 2):
        pt = row.get("phase_type", "unknown")
        fill_color = PHASE_COLORS.get(pt, "FFFFFF")
        for col_idx, col_name in enumerate(all_cols, 1):
            val = row.get(col_name)
            cell = ws.cell(row=row_idx, column=col_idx)
            if val is None or (isinstance(val, float) and pd.isna(val)):
                cell.value = "—"
            elif col_name in ("start","end","phase","phase_type"):
                cell.value = str(val)
            elif col_name == "n_days":
                cell.value = int(val)
            elif "steps" in col_name:
                cell.value = int(val) if not pd.isna(val) else "—"
            else:
                cell.value = round(float(val), 2)
            cell.fill = PatternFill("solid", fgColor=fill_color)
            cell.alignment = Alignment(horizontal="center")
    _auto_width(ws)


def write_sheet_correlations(wb: openpyxl.Workbook,
                              corr_all: pd.DataFrame,
                              corr_lagged: pd.DataFrame,
                              lab_corr: pd.DataFrame, *, lang: str | None = None):
    lang = lang or i18n.lang_of()
    ws = wb.create_sheet(i18n.t("longitudinal.sheet.correlations", lang))
    row = 1

    # 1. Общая матрица
    ws.cell(row=row, column=1, value=i18n.t("longitudinal.heading.spearman", lang)).font = Font(bold=True, size=12)
    row += 1
    strong = corr_all[corr_all["spearman_r"].abs() >= 0.2].head(50)
    if not strong.empty:
        headers = [i18n.t("longitudinal.column.metric_a", lang), i18n.t("longitudinal.column.metric_b", lang), "Spearman r", "p-value", "n", i18n.t("longitudinal.column.significant", lang), i18n.t("longitudinal.column.gate", lang)]
        _header_row(ws, headers, row)
        row += 1
        for _, r2 in strong.iterrows():
            if r2.get("derived"):
                g = "derived"
            elif r2.get("gate_pass"):
                g = i18n.t("longitudinal.gate.passed", lang)
            elif pd.notna(r2.get("p_perm")):
                g = i18n.t("longitudinal.gate.phantom", lang)
            else:
                g = "—"
            vals = [_longitudinal_label(r2["metric_a"], lang),
                    _longitudinal_label(r2["metric_b"], lang),
                    r2["spearman_r"], r2["p_value"],
                    r2["n"], "✓" if r2["significant"] else "", g]
            for col, v in enumerate(vals, 1):
                cell = ws.cell(row=row, column=col, value=v)
                if r2["spearman_r"] >= 0.3:
                    cell.fill = PatternFill("solid", fgColor="D5F5E3")
                elif r2["spearman_r"] <= -0.3:
                    cell.fill = PatternFill("solid", fgColor="FADBD8")
            row += 1

    # 2. Лаговые корреляции
    row += 2
    ws.cell(row=row, column=1, value=i18n.t("longitudinal.heading.lagged", lang)).font = Font(bold=True, size=12)
    row += 1
    sig_lagged = corr_lagged[corr_lagged["significant"]].head(30)
    if not sig_lagged.empty:
        headers = [i18n.t("longitudinal.column.predictor", lang), i18n.t("longitudinal.column.target", lang), i18n.t("longitudinal.column.lag", lang), "Spearman r", "p-value", "n"]
        _header_row(ws, headers, row)
        row += 1
        for _, r2 in sig_lagged.iterrows():
            vals = [r2["predictor"], r2["target"], r2["lag_days"],
                    r2["spearman_r"], r2["p_value"], r2["n"]]
            for col, v in enumerate(vals, 1):
                ws.cell(row=row, column=col, value=v)
            row += 1

    # 3. Лаб-метрики
    row += 2
    ws.cell(row=row, column=1, value=i18n.t("longitudinal.heading.labs", lang)).font = Font(bold=True, size=12)
    row += 1
    if not lab_corr.empty:
        sig_lab = lab_corr[lab_corr["significant"]].sort_values("spearman_r", key=abs, ascending=False).head(40)
        headers = [i18n.t("longitudinal.column.lab", lang), i18n.t("longitudinal.column.metric", lang), "Spearman r", "p-value", "n", i18n.t("longitudinal.column.strong", lang), i18n.t("longitudinal.column.gate", lang)]
        _header_row(ws, headers, row)
        row += 1
        for _, r2 in sig_lab.iterrows():
            g = i18n.t("longitudinal.gate.passed", lang) if r2.get("gate_pass") else ("trend/n" if pd.notna(r2.get("p_perm")) else "—")
            vals = [r2["lab"], _longitudinal_label(r2["metric"], lang), r2["spearman_r"],
                    r2["p_value"], r2["n"], "★" if r2["strong"] else "", g]
            for col, v in enumerate(vals, 1):
                cell = ws.cell(row=row, column=col, value=v)
                if r2["strong"]:
                    cell.fill = PatternFill("solid", fgColor="FEF9E7")
            row += 1
    _auto_width(ws)


def write_sheet_recovery(wb: openpyxl.Workbook, recovery: dict, *, lang: str | None = None):
    lang = lang or i18n.lang_of()
    ws = wb.create_sheet(i18n.t("longitudinal.sheet.recovery", lang))
    headers = [i18n.t("longitudinal.column.metric", lang), i18n.t("longitudinal.column.baseline", lang), "P25 baseline", "P75 baseline",
               i18n.t("longitudinal.column.current", lang), i18n.t("longitudinal.column.percent_baseline", lang), i18n.t("longitudinal.column.trend", lang)]
    _header_row(ws, headers)

    for row_idx, (col, info) in enumerate(recovery.items(), 2):
        pct = info["pct_of_baseline"]
        vals = [_longitudinal_label(col, lang), info["baseline_mean"], info["baseline_p25"],
                info["baseline_p75"], info["current_mean"],
                f"{pct}%" if pct else "—", info["trend_dir"]]
        for col_idx, v in enumerate(vals, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=v)
        # Цвет по % восстановления
        if pct is not None:
            if pct >= 95:
                color = "D5F5E3"   # зелёный — восстановлен
            elif pct >= 75:
                color = "FEF9E7"   # жёлтый — частично
            else:
                color = "FADBD8"   # красный — далеко от нормы
            for col_idx in range(1, 8):
                ws.cell(row=row_idx, column=col_idx).fill = PatternFill("solid", fgColor=color)
    _auto_width(ws)


# ── JSON-саммари для ИИ ───────────────────────────────────────────────────────

def build_ai_summary(yearly: pd.DataFrame, phase_df: pd.DataFrame,
                     recovery: dict, corr_all: pd.DataFrame,
                     lab_corr: pd.DataFrame, phases: list[dict],
                     gate_meta: dict | None = None,
                     quarantined: "dict | set | None" = None) -> dict:
    """
    Компактный JSON для загрузки в generate_constitutions.py.
    Всё что AI должен знать о долгосрочных паттернах.

    quarantined: РЕШЕНИЕ по каждой паре карантина — `{pair: "pending"|"admitted"|"rejected"}`
    (формат пары «a×b» и «pred→tgt+Nд»). `pending` → `online_status=pending_adjudication`, читатели
    подают «состав менялся, ждёт вердикта». `rejected` → пара НЕ попадает в саммари вообще: человек
    (или контроллер) назвал её артефактом, и подать её находкой значит обойти последний гейт
    (ревью R5, VG-R5-01 — воспроизведено). `admitted` → обычная находка с провенансом вердикта.
    Множество (устаревшая форма) трактуется как «все pending» — совместимость с вызывающими,
    которые ещё не знают о решении. None → без карантина.
    """
    # Нормализуем к решению: множество = «все pending». Так старый вызывающий не становится
    # молча «все допущены» — ошибка в безопасную сторону.
    _quar = ({p: "pending" for p in quarantined} if isinstance(quarantined, (set, frozenset, list))
             else dict(quarantined or {}))
    _rejected = {p for p, s in _quar.items() if s == "rejected"}

    # Погодовые тренды — только ключевые метрики
    year_rows = []
    for _, row in yearly.iterrows():
        yr_entry = {"year": int(row["year"])}
        for m in ["sleep_total_mean","sleep_deep_mean","hrv_mean",
                  "resting_hr_mean","readiness_mean","steps_mean"]:
            v = row.get(m)
            if v is not None and not pd.isna(v):
                yr_entry[m.replace("_mean","")] = round(float(v), 2)
        year_rows.append(yr_entry)

    # Фазы — только mean
    phase_rows = []
    for _, row in phase_df.iterrows():
        p = {"phase": row["phase"], "type": row["phase_type"],
             "start": row["start"], "end": row["end"], "n": int(row["n_days"])}
        for m in DAILY_METRICS.keys():
            v = row.get(f"{m}_mean")
            if v is not None and not pd.isna(v):
                p[m] = round(float(v), 2)
        phase_rows.append(p)

    # Топ-корреляции — ТОЛЬКО пережившие статистический гейт. Legacy-ветка strong&significant
    # убрана 2026-07-26: она была не запасным путём, а дырой — при отказе гейта сырая пара
    # уходила в конституции (P1-01). Нет gate_pass → веры нет, это отказ, а не деградация.
    if corr_all.empty:
        sel = corr_all
    elif "gate_pass" in corr_all.columns:
        sel = corr_all[corr_all["gate_pass"]].sort_values("p_perm")
        if _rejected:
            # Отклонённая вердиктом пара выбрасывается ДО отбора топ-20 (VG-R5-01). Не метка, а
            # исключение: любая метка означала бы, что читатель может её не понять и подать связь.
            _keys = sel["metric_a"].astype(str) + "×" + sel["metric_b"].astype(str)
            sel = sel[~_keys.isin(_rejected)]
    else:
        raise ValueError(
            "build_ai_summary: в corr_all нет колонки gate_pass — саммари без гейта не строится. "
            "Вызывающий обязан проверить _publication_decision(gate_meta) ДО вызова.")
    top_corr = []
    for _, r in sel.head(20).iterrows():
        item = {"a": r["metric_a"], "b": r["metric_b"],
                "r": r["spearman_r"], "p": r["p_value"]}
        if "p_perm" in corr_all.columns:
            item["p_perm"] = None if pd.isna(r["p_perm"]) else round(float(r["p_perm"]), 4)
            item["verdict"] = "gated"
        # Профиль по эпохам (Gate 2 «Period Profile», словарь methodology/validation_gate/
        # gates.yaml; наше прежнее имя «Gate 3» отдано другому механизму). ЧИСЛА, не вердикт: общий |r|
        # не отличает «связь есть каждый день» от «обе переменные десять лет ползут вниз», и
        # разводит эти случаи только разрез по периодам. Порог «держится» ждёт калибровки на
        # синтетике; до неё читатель получает факт, а не суждение — и это НЕ молчание.
        # Нет колонки (тенант без эпох / старый прогон) → полей нет (backward-compat).
        if "r_epoch_median" in corr_all.columns and not pd.isna(r.get("r_epoch_median")):
            item["epochs_n"] = int(r["epochs_n"])
            item["r_epoch_median"] = float(r["r_epoch_median"])
            item["r_epoch_weakest"] = float(r["r_epoch_weakest"])
            # Доля согласного знака (2026-08-04, достройка Gate 2 до «знака и величины»).
            # Старый прогон без колонки → поля нет (backward-compat, как соседние).
            if "r_epoch_sign_share" in corr_all.columns and not pd.isna(r.get("r_epoch_sign_share")):
                item["r_epoch_sign_share"] = float(r["r_epoch_sign_share"])
        # Карантин мерцающих (Ф4 предохранитель): пара впервые вошла в pass-set → НЕ находка до
        # вердикта. Метка ЗДЕСЬ один раз; читатели подают «состав менялся», не рычаг/связь.
        # `rejected` не метится, а ВЫБРАСЫВАЕТСЯ выше — вердикт «артефакт» не должен доезжать
        # до читателя ни в каком виде (VG-R5-01).
        _dec = _quar.get(f"{r['metric_a']}×{r['metric_b']}")
        if _dec == "pending":
            item["online_status"] = "pending_adjudication"
        elif _dec == "admitted":
            item["online_status"] = "admitted"      # провенанс: связь пропущена вердиктом
        # Семья A/D (owner-only): единая метка кладётся ЗДЕСЬ, читатели (конституции + gp_context)
        # берут готовое, не пересчитывают. Нет колонки (тенант/старый прогон) → поля нет (backward-compat).
        if "verdict_family" in corr_all.columns:
            _vf = r.get("verdict_family")
            if isinstance(_vf, str) and _vf:
                item["verdict_family"] = _vf
        elif gate_meta and gate_meta.get("gate_applied") and "gate_pass" in corr_all.columns:
            # Тенант без стратификации (нет своих эпох): gated-пара, но A/D различить НЕЛЬЗЯ →
            # честная пометка «совместное движение, не рычаг» (Группа 3 nostrat, 2026-07-23).
            item["verdict_family"] = "D-nostrat"
        # Причинное уточнение (Группа 2, owner-only): рычаг|совпадение|не_знаю поверх A-рычага.
        # ОПИСАТЕЛЬНОЕ — кладётся ЗДЕСЬ один раз, читатели берут через causal_label. Провенанс p0
        # для оракула владельца. Нет колонки/пусто (не A-рычаг/тенант/старый прогон) → поля нет (backward-compat).
        if "verdict_causal" in corr_all.columns:
            _vc = r.get("verdict_causal")
            if isinstance(_vc, str) and _vc:
                item["verdict_causal"] = _vc
                _cp0 = r.get("causal_p0")
                if _cp0 is not None and not pd.isna(_cp0):
                    item["causal_p0"] = round(float(_cp0), 5)
        top_corr.append(item)

    # Топ лаб-метрики — ТОЛЬКО пережившие детренд-гейт. Пустой lab_corr законен (лабов нет);
    # непустой без gate_pass — тот же отказ, что и на daily-пути.
    #
    # Scope проверяется ЗДЕСЬ, на последней точке перед верой, а не только внутри гейта
    # (ревью R3, VG-R3-04): раньше кадр с уже проставленным `gate_pass=True` проходил мимо
    # запрета — контракт держался на одном вызывающем, а объявлен был как сквозной.
    if not _sf.LABS_ACTIVE and not lab_corr.empty and "gate_pass" in lab_corr.columns \
            and bool(lab_corr["gate_pass"].fillna(False).any()):
        raise ValueError(
            f"build_ai_summary: labs.scope={_sf.LABS_SCOPE}, но в lab_corr есть gate_pass=True — "
            "кадр противоречит манифесту; лаб-путь вне release-scope не может дать находку")
    if lab_corr.empty or not _sf.LABS_ACTIVE:
        sel_lab = lab_corr.iloc[0:0] if not lab_corr.empty else lab_corr
    elif "gate_pass" in lab_corr.columns:
        sel_lab = lab_corr[lab_corr["gate_pass"]].sort_values("p_perm")
    else:
        raise ValueError(
            "build_ai_summary: в lab_corr нет колонки gate_pass — саммари без гейта не строится.")
    top_lab = []
    for _, r in sel_lab.head(15).iterrows():
        item = {"lab": r["lab"], "metric": r["metric"],
                "r": r["spearman_r"], "p": r["p_value"]}
        if "r_detrended" in lab_corr.columns:
            item["r_detrended"] = None if pd.isna(r["r_detrended"]) else r["r_detrended"]
            item["p_perm"] = None if pd.isna(r["p_perm"]) else round(float(r["p_perm"]), 4)
        top_lab.append(item)

    # Восстановление
    recovery_summary = {
        k: {
            "baseline": v["baseline_mean"],
            "current":  v["current_mean"],
            "pct":      v["pct_of_baseline"],
            "trend":    v["trend_dir"],
        }
        for k, v in recovery.items()
        if v.get("pct_of_baseline") is not None
    }

    # Направленная лаг-семья q_lag (owner-only): из gate_meta (СВОЙ набор пар, не corr_all).
    # Честная формулировка «предшествование во времени» через lag_label у читателей. Нет meta/пусто →
    # ключа нет (backward-compat: тенант/старый прогон рендерятся как раньше).
    lagged_rows = []
    for c in ((gate_meta or {}).get("stratified", {}).get("lagged", []) or [])[:15]:
        _lrow = {"predictor": c.get("predictor"), "target": c.get("target"),
                 "lag_days": c.get("lag_days"), "p_lag": c.get("p_lag"),
                 "verdict_lag": c.get("verdict_lag")}
        _lkey = f"{c.get('predictor')}→{c.get('target')}+{int(c.get('lag_days'))}д"
        _ldec = _quar.get(_lkey)
        if _ldec == "rejected":
            continue                                          # отклонённая лаг-пара не едет вовсе
        if _ldec == "pending":
            _lrow["online_status"] = "pending_adjudication"   # мерцающая лаг-пара — не находка (Ф4)
        elif _ldec == "admitted":
            _lrow["online_status"] = "admitted"
        lagged_rows.append(_lrow)

    out = {
        "schema_version": BELIEF_SCHEMA_VERSION,   # читатель отказывает неизвестной схеме
        "generated_at": str(get_now())[:19],
        "data_range": {
            "start": str(yearly["year"].min()),
            "end":   str(yearly["year"].max()),
            "total_years": int(yearly["year"].max() - yearly["year"].min() + 1),
        },
        "phases": phase_rows,
        "yearly_trend": year_rows,
        "top_correlations": top_corr,
        "lab_metric_correlations": top_lab,
        "recovery_vs_baseline": recovery_summary,
    }
    if lagged_rows:
        out["top_lagged"] = lagged_rows
    if _quar:
        # Исключение отклонённых обязано быть ВИДИМЫМ. Молча укороченный список неотличим от
        # «таких связей не нашлось» — тот же класс, что чинил весь этот ремонт: отсутствие как
        # норма. Читателю нужен не список отклонённых (это его не касается), а факт и число.
        from collections import Counter as _C
        _cnt = _C(_quar.values())
        out["quarantine"] = {"pending": _cnt.get("pending", 0),
                             "admitted": _cnt.get("admitted", 0),
                             "rejected_excluded": _cnt.get("rejected", 0)}
    return out


def _belief_committed(run_id: str, conn=None) -> "bool | None":
    """Лежит ли в agent_reports строка веры ЭТОГО прогона. `None` = выяснить не удалось.

    Read-back вместо догадки (ревью R5, VG-R5-08). Исход транзакции определяется чтением по
    `run_id`, а не тем, вернулся ли вызов записи: между успешным `commit` и возвратом управления
    соединение может умереть. Три исхода, и «неизвестно» — законный третий: выдуманный `failed`
    при записанной вере хуже честного «не знаю», потому что провоцирует повторный прогон и дубль."""
    if not run_id:
        return None
    try:
        c = conn or get_conn()
        row = c.execute(
            "SELECT 1 FROM agent_reports WHERE agent_type='longitudinal_analysis' "
            "AND findings LIKE ? ORDER BY id DESC LIMIT 1",
            (f'%"run_id": "{run_id}"%',)).fetchone()
        return row is not None
    except Exception as exc:                     # noqa: BLE001 — БД недоступна: это и есть «не знаю»
        print(f"  ⚠️ read-back веры не удался ({type(exc).__name__}: {exc})")
        return None


def _resolve_commit_outcome(run_id: str, exc: BaseException, gate_meta: dict, conn=None) -> str:
    """Исход записи веры после исключения: `committed` | `failed` | `indeterminate`.

    Вынесено функцией, чтобы у контракта был исполняемый оракул, а не повтор логики в пробе
    (ревью R5, VG-R5-08). Мутирует `gate_meta` — тот же общий конверт, что читает `finally`."""
    _committed = _belief_committed(run_id, conn=conn)
    if _committed is True:
        gate_meta["published"] = True
        gate_meta["commit_note"] = (f"подтверждение записи потеряно ({type(exc).__name__}), "
                                    f"но строка веры run_id={run_id} найдена read-back'ом")
        return "committed"
    if _committed is False:
        return "failed"
    gate_meta["failure"] = "belief_commit_indeterminate"
    gate_meta["error"] = (f"исход записи веры НЕИЗВЕСТЕН: {exc!r}; read-back по run_id={run_id} "
                          f"тоже не удался. Проверь agent_reports руками ДО повторного прогона — "
                          f"иначе возможен дубль.")
    return "indeterminate"


def save_to_agent_reports(summary: dict):
    """Сохраняет JSON-саммари в agent_reports для использования в конституциях.

    Гейт приёмки продублирован ЗДЕСЬ намеренно (не дубль значения, а защита точки записи):
    вера попадает в БД только через эту функцию, и она обязана отказывать сама, даже если
    вызывающий забыл спросить `_publication_decision`."""
    if not _publication_decision((summary or {}).get("gate") or {}):
        raise ValueError(
            "save_to_agent_reports: саммари без применённого гейта в веру не пишется "
            f"(gate={(summary or {}).get('gate')})")
    conn = get_conn()
    findings_text = json.dumps(summary, ensure_ascii=False, indent=2)
    # append-семантика намеренно (решение 2026-07-12: хранить историю прогонов).
    # Дедупа нет: `ON CONFLICT DO NOTHING` был мёртвой заглушкой (в agent_reports нет
    # UNIQUE-ключа → не срабатывал никогда, но врал про «один отчёт в день»). Убран.
    # Read-your-writes обеспечивают читатели: ORDER BY ... , id DESC (последняя запись).
    conn.execute("""
        INSERT INTO agent_reports
            (date, agent_type, agent_name, findings, created_at)
        VALUES (?, 'longitudinal_analysis', 'LongitudinalAnalyst', ?, datetime('now'))
    """, (str(get_today()), findings_text))
    conn.commit()
    conn.close()


# ── main ──────────────────────────────────────────────────────────────────────

def _apply_gate(daily_df, labs_df, corr_all, lab_corr):
    """Прогоняет correlation_gate. При сбое возвращает исходные df и status="failed".

    Исходные df возвращаются РАДИ EXCEL (человеческий артефакт аналитика), а не ради веры:
    решение о публикации принимает `_publication_decision`, и при status="failed" вера не
    строится вовсе. До 2026-07-26 отличия не было — сырьё уезжало в конституции (P1-01)."""
    try:
        import correlation_gate
        import secrets_paths
        # Gate 0.5 контроль: время в постели (sleep_inbed) — независимый замер Oura, лежит в
        # daily_metrics, но НЕ в DAILY_METRICS → не тестируемая метрика, только контроль Z.
        # None при недоступности → Gate 0.5 N/A (fail-open). Тенант читает СВОЙ inbed (get_conn).
        control_z = None
        try:
            _c = get_conn()
            _ib = pd.read_sql_query("SELECT date, sleep_inbed FROM daily_metrics ORDER BY date", _c)
            _c.close()
            _ib["date"] = pd.to_datetime(_ib["date"])
            control_z = pd.to_numeric(_ib.set_index("date")["sleep_inbed"], errors="coerce")
        except Exception as e:  # noqa: BLE001 — A5 22.09: fail-open законен, молчание — нет
            # Gate 0.5 выключается, и «0 заблокировано» становится неотличимо от «гейт не
            # стоял». Причина — в лог прогона, факт — в meta гейта (daily_gate05_applied).
            print(f"  ⚠️ Gate 0.5 N/A в этом прогоне: контроль sleep_inbed не прочитан "
                  f"({type(e).__name__}: {str(e)[:80]})")
            control_z = None
        # Семья A (стратифицированная разметка рычаг/описательное) — по ПРОИСХОЖДЕНИЮ
        # ДАННЫХ, а не по правам процесса: её эпохи = терапевтический таймлайн владельца,
        # для данных тенанта невалидны. Раньше здесь стоял is_owner(), и это был
        # единственный из пяти его читателей, который спрашивал про ДАННЫЕ (замер
        # 2026-08-12; остальные спрашивают про секреты, про роль человека в разговоре и
        # про доступ к файлам — им буквальное имя каталога отвечает верно). Из-за
        # подмены вопроса семья A не размечалась в клоне канона, и проба карантина не
        # могла проверить путь, ради которого написана. is_owner_data прав не даёт.
        cg, lg, meta = correlation_gate.gate_correlations(
            daily_df, labs_df, corr_all, lab_corr, lab_window_days=LAB_WINDOW_DAYS,
            enable_stratified=secrets_paths.is_owner_data(), control_z=control_z)
        meta["status"] = "applied" if meta.get("gate_applied") else "failed"
        return cg, lg, meta
    except Exception as exc:  # отказ, а не деградация — вера не публикуется
        return corr_all, lab_corr, {"gate_applied": False, "status": "failed", "error": repr(exc)}


def _publication_decision(gate_meta: dict) -> bool:
    """ЕДИНСТВЕННОЕ место, где решается, публикуется ли вера этим прогоном.

    Отдельная функция ради позитивного и негативного контроля без БД: правило приёмки на
    стороне писателя обязано быть проверяемо так же дёшево, как на стороне читателя."""
    return bool(gate_meta.get("gate_applied")) and gate_meta.get("status", "applied") == "applied"


DRYRUN_DIR = artifact_path("logs/dryrun")


def _dryrun_path(path: "Path") -> "Path":
    """Путь того же артефакта в песочнице репетиции.

    `--no-db` назывался «сухим», но писал в БОЕВЫЕ артефакты: перетирал квитанцию, снимок
    pass-set и ставил пары в карантин (VG-R4-03/04). «Сухой» прогон, меняющий состояние, —
    не репетиция, а второй боевой прогон без веры. Теперь у репетиции свой каталог; читает
    она канонические артефакты (чтобы diff был осмысленным), а пишет только сюда."""
    return DRYRUN_DIR / path.name


def _new_run_id() -> str:
    """Идентичность ОДНОГО прогона. Дата — не идентичность: два прогона в сутки (ручной перезапуск,
    dry-run, параллельная сессия) неразличимы по дате, и датчик VG-R4-03 сверял квитанцию с верой
    по дню — то есть подтверждал «вера от сегодня» квитанцией СОВСЕМ ДРУГОГО прогона."""
    import uuid
    return f"{get_today()}-{uuid.uuid4().hex[:8]}"


def _write_gate_run_receipt(gate_meta: dict, published: bool, artifact: "Path | None" = None,
                            *, error: "str | None" = None, dry_run: bool = False,
                            run_id: "str | None" = None, phase: str = "closed") -> bool:
    """Квитанция ПРОГОНА (не веры): пишется и при успехе, и при отказе.

    Зачем отдельный артефакт: при отказе новой строки в agent_reports НЕТ, а «строки нет» и
    «прогона не было» неотличимы. Квитанция делает отказ событием, у которого есть читатель
    (integrity 07:50 и оба ИИ-читателя через belief_contract).

    `published` означает «вера ЗАПИСАНА», а не «разрешено писать» (ревью R3, VG-R3-02: квитанция
    писалась сразу после гейта, за 45 строк до записи в БД, и падение любого шага публикации
    оставляло `published=true` без строки веры).

    `phase="started"` открывает конверт прогона В НАЧАЛЕ, `phase="closed"` — закрывает в `finally`.
    Между ними прогон может умереть по SIGKILL/OOM; тогда на диске остаётся `status="started"`, и
    это ЧИТАЕМОЕ «прогон начался и не вернулся», а не прошлогодняя `applied` (VG-R4-03).

    `run_id` едет и в квитанцию, и в веру (`gate.run_id`) — датчик сверяет ИДЕНТИЧНОСТЬ, а не день.

    `dry_run` пишет в ОТДЕЛЬНЫЙ файл (`_dryrun_path`): раньше `--no-db` перетирал боевую квитанцию
    отказа своим `applied` — репетиция стирала улику настоящего сбоя (VG-R4-03, воспроизведено)."""
    path = artifact or GATE_RUN_RECEIPT
    if dry_run and artifact is None:
        path = _dryrun_path(path)
    _failed = error is not None or not (published or dry_run)
    rec = {"status": "started" if phase == "started" else ("failed" if _failed else "applied"),
           "phase": phase,
           "run_id": run_id or (gate_meta or {}).get("run_id"),
           "at": str(get_now())[:19],
           "published": bool(published),
           "schema_version": BELIEF_SCHEMA_VERSION}
    if dry_run:
        rec["dry_run"] = True
    if _failed and phase != "started":
        rec["error"] = error or gate_meta.get("error") or "гейт не применён (gate_applied=False)"
    if (gate_meta or {}).get("failure"):
        rec["failure"] = gate_meta["failure"]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError as exc:
        # Печатаем, но не валим прогон: пропавшая квитанция — сама по себе сигнал liveness
        # для читателя, подменять её содержимое молча нельзя.
        print(f"  ⚠️ квитанция прогона не записана ({type(exc).__name__}): {path}")
        return False


from belief_contract import MC_GAP_ARTIFACT  # noqa: E402  дом — belief_contract


def _write_mc_gap_artifact(gate_meta: dict, artifact: "Path | None" = None) -> bool:
    """Кросс-процессный артефакт для integrity_tests.check_mc_gap (Коммит3, триггер (а) Фаз 1/3-а).

    Гейт пишет ночью, integrity читает 07:50 → штамп (дата через _time_inject + HEAD sha)
    даёт читателю freshness-гвард против stale-read И против «артефакт от старого кода».
    Сбой записи НЕ валит прогон (print, не raise): пропавший/устаревший артефакт — сам по
    себе сигнал liveness-ветки читателя, содержимое не подменяется молча."""
    if not gate_meta.get("gate_applied") or "mc_gap" not in gate_meta:
        return False
    path = artifact or MC_GAP_ARTIFACT
    try:
        import subprocess
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=Path(__file__).parent, capture_output=True,
                             text=True, timeout=10).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001 — sha опционален, freshness держится на дате
        sha = "unknown"
    payload = {"date": str(get_today()), "head_sha": sha, "mc_gap": gate_meta["mc_gap"]}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        return True
    except OSError as exc:
        print(f"  ⚠️ mc_gap артефакт не записан: {exc!r}")
        return False


from belief_contract import PASSSET_ARTIFACT  # noqa: E402  дом — belief_contract
PASSSET_HISTORY_MAX = 120  # ~2.3 года недельных прогонов. Было 16 (~4 мес): хватало на стройку
# («мерцает ли сейчас»), мало на ДЕЖУРСТВО — пара, входящая раз в полгода, в 4-месячном окне видна
# как одиночное событие, а не как узор. Файл ~20 пар × 30 байт × 120 ≈ 100 КБ (2026-07-25).
EPOCH_BREAK_MARK = artifact_path("logs/passset_epoch_break.txt")


def _head_sha() -> str:
    """Короткий HEAD sha (штамп кода в артефакте); 'unknown' если и git, и манифеста нет.

    Через `git_facts` (2026-08-10): в песочнице `.git` нет по построению, и снимок
    pass-set писал туда `unknown`. Штамп кода — это то, чем снимок отличается от
    анонимного набора пар: без него нельзя сказать, КАКАЯ версия гейта его считала.
    """
    try:
        import git_facts
        return git_facts.head_sha()
    except Exception:  # noqa: BLE001 — sha опционален, но молчать о причине нельзя
        return "unknown"


def _passset_members(cg, gate_meta: dict) -> dict:
    """Членство pass-set трёх семей из ЖИВОГО gate-выхода (не из top_correlations — тот обрезан
    до 20). D=gate_pass, A=a_lever (owner-only), q_lag из meta. Отсутствие колонки (тенант/старый
    прогон) → пустой список, не крэш. Пары отсортированы — детерминизм для diff."""
    def _pairs(col):
        if col not in cg.columns:
            return []
        sub = cg[cg[col].fillna(False).astype(bool)]
        return sorted(f"{r.metric_a}×{r.metric_b}" for r in sub.itertuples())
    lagged = (gate_meta or {}).get("stratified", {}).get("lagged", []) or []
    q_lag = sorted(f"{c.get('predictor')}→{c.get('target')}+{int(c.get('lag_days'))}д"
                   for c in lagged if c.get("predictor") and c.get("target"))
    return _drop_structural({"D": _pairs("gate_pass"), "A": _pairs("a_lever"), "q_lag": q_lag})


def _member_pair(member: str) -> frozenset:
    """Строка членства → пара метрик: «a×b» (D/A) или «a→b+Nд» (q_lag)."""
    if "→" in member:
        a, _, rest = member.partition("→")
        return frozenset((a, rest.rsplit("+", 1)[0]))
    a, _, b = member.partition("×")
    return frozenset((a, b))


def _drop_structural(members: dict) -> dict:
    """Членство без структурных пар (STRUCTURAL_PAIRS: часть-целое + арифметика прибора), 28.09.

    Такие пары в веру не доходят (режутся на рендере), значит их вход/выход в pass-set — не событие:
    ни мерцание для алерта, ни прибытие для карантина. До 28.09 снимок брал a_lever ДО среза, и в
    карантине ждали вердикта владельца 22 пары из 69, уже объявленные им арифметикой прибора (C-79,
    второй экземпляр). Реестр читается на КАЖДОМ вызове, поэтому новая объявленная пара уходит из
    членства сама, без правки кода (C-54)."""
    return {fam: [m for m in (v or []) if _member_pair(m) not in _sf.STRUCTURAL_PAIRS]
            for fam, v in (members or {}).items()}


def _flicker_diff(prev_members: dict, cur_members: dict) -> dict:
    """ЕДИНЫЙ источник «что замерцало»: членство D/A/q_lag prev↔cur. Считается ОДИН раз при записи
    снимка и хранится в нём (`flicker_vs_prev`) → и integrity-алерт (check_passset_flicker), и
    карантин конституций читают одно поле, а не пересчитывают порознь (split-brain-гард, Коммит B).
    Возвращает {fam: {"entered":[...], "left":[...]}} только для семей со сменой; иначе {}."""
    # Срез с ОБЕИХ сторон: прошлый снимок мог быть записан до объявления пары структурной (или до
    # 28.09, когда срез вообще не делался) — без этого объявление выглядело бы как «пара ушла».
    prev_members, cur_members = _drop_structural(prev_members), _drop_structural(cur_members)
    out = {}
    for fam in ("D", "A", "q_lag"):
        p = set((prev_members or {}).get(fam, []))
        c = set((cur_members or {}).get(fam, []))
        entered, left = sorted(c - p), sorted(p - c)
        if entered or left:
            out[fam] = {"entered": entered, "left": left}
    return out


class QuarantineCarrierUnreadable(RuntimeError):
    """Носитель события карантина (снимок pass-set) непригоден для чтения.

    Отдельный тип, потому что последствие особое: без него НЕЛЬЗЯ узнать, какие пары прибыли
    впервые, а значит нельзя решить, кого держать в карантине. Пустой ответ здесь означал бы
    «прибытий не было» — утверждение, которого мы не знаем (ревью R3, находка VG-R3-01)."""


def _entered_pairs(artifact: "Path | None" = None) -> list:
    """СОБЫТИЕ прибытия: пары, впервые вошедшие в pass-set в последнем прогоне.

    Читает `flicker_vs_prev.entered` последнего снимка (единый источник с check_passset_flicker).
    «Убывшие» не карантинятся — их в top_correlations уже нет. Возвращает [{"pair","family"}].
    Это ТОЛЬКО событие: состояние карантина живёт в БД (см. `_quarantined_pairs`).

    Отсутствие файла законно (первый прогон вообще) → []. Любая другая нечитаемость —
    `QuarantineCarrierUnreadable`: до 2026-07-26 (ревью R3) она возвращала пустой список, и
    прошедшая пара уходила в веру БЕЗ `pending_adjudication`, то есть мимо человеческого гейта.
    Соседний датчик кричал о порче в 07:50 — через пять часов после публикации веры."""
    path = artifact or PASSSET_ARTIFACT
    if not path.exists():
        return []
    try:
        hist = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise QuarantineCarrierUnreadable(
            f"{path.name}: {type(exc).__name__}: {exc}") from exc
    if not isinstance(hist, list) or not hist or not isinstance(hist[-1], dict):
        # Пустая история недостижима штатно: писатель всегда добавляет снимок. Значит файл
        # усечён/подменён — это порча, а не «прибытий нет».
        raise QuarantineCarrierUnreadable(f"{path.name}: снимок пуст или неформатен")
    return _entered_from_snap(hist[-1])


def _entered_from_snap(snap: dict) -> list:
    """Прибытия из УЖЕ ВЫЧИСЛЕННОГО снимка — единый источник с `_entered_pairs`.

    Появилось в ремонте VG-R5-02: событие прибытия нужно ДО того, как снимок лёг на диск,
    иначе постановку в карантин нельзя сделать предусловием продвижения pass-set."""
    fv = (snap or {}).get("flicker_vs_prev") or {}
    return [{"pair": pair, "family": fam}
            for fam, d in fv.items() for pair in (d or {}).get("entered", [])]


def _last_d_members(artifact: "Path | None" = None) -> "list[str] | None":
    """Состав семьи D из последнего снимка pass-set ДО этого прогона (None — истории нет/нечитаема)."""
    try:
        hist = json.loads(Path(artifact or PASSSET_ARTIFACT).read_text(encoding="utf-8"))
        return list(((hist[-1] or {}).get("members") or {}).get("D") or []) if hist else None
    except Exception as e:  # noqa: BLE001 — без прошлого снимка сообщение не отметит новое/ушедшее
        print(f"  ⚠️ прошлый снимок pass-set не прочитан ({type(e).__name__}) — "
              "в сообщении о связях не будет пометок «новая/ушла»")
        return None


def links_note(prev_d: "list[str] | None", cur: "list[tuple[str, str, str, float]]",
               m: int, when: str, *, lang: str | None = None) -> str:
    """Person-facing summary of confirmed links, in the current tenant's language."""
    lang = lang or i18n.lang_of()
    head = i18n.t("longitudinal.links.header", lang, when=when, m=m)
    if not cur:
        tail = i18n.t("longitudinal.links.none", lang)
        if prev_d:
            tail += i18n.t("longitudinal.links.previous_gone", lang, pairs=", ".join(prev_d))
        return head + tail
    prev = set(prev_d or [])
    lines = [head, i18n.t("longitudinal.links.confirmed", lang)]
    for key, la_, lb_, r in cur:
        mark = i18n.t("longitudinal.links.new", lang) if prev_d is not None and key not in prev else ""
        word = i18n.t("longitudinal.links.positive" if r > 0 else "longitudinal.links.negative", lang)
        la_, lb_ = _longitudinal_label(la_, lang), _longitudinal_label(lb_, lang)
        lines.append(f"• {la_} ↔ {lb_}: r={r:+.2f} ({word}){mark}")
    gone = sorted(prev - {k for k, *_ in cur})
    if gone:
        lines.append(i18n.t("longitudinal.links.gone", lang, pairs=", ".join(gone)))
    lines.append(i18n.t("longitudinal.links.caution", lang))
    return "\n".join(lines)


def _validate_prior_passset(artifact: "Path | None" = None) -> dict:
    """ЧИТАЕТ И ПРОВАЛИДИРУЕТ прежнее состояние ДО того, как писатель его тронет.

    Корень VG-R4-01: порядок был обратный. `_write_passset_snapshot` лечил повреждённую историю
    (сброс + улика) ПЕРВЫМ, и только потом `_entered_pairs` читал файл — уже вылеченный. Читатель
    видел валидный список с `flicker_vs_prev={}`, то есть «прибытий не было», исключение не
    возникало, пара уходила в веру мимо карантина. Воспроизведено: писатель `true`, читатель `[]`,
    один снимок, `flicker={}`. Лечение само по себе правильное — неправильно было лечить ДО того,
    как факт порчи попал в решение о публикации.

    Возвращает `{"ok": bool, "reason": str|None}`. Отсутствие файла законно (первый прогон) → ok."""
    path = artifact or PASSSET_ARTIFACT
    if not path.exists():
        return {"ok": True, "reason": None}
    try:
        hist = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return {"ok": False, "reason": f"{path.name}: {type(exc).__name__}: {exc}"}
    if not isinstance(hist, list):
        return {"ok": False, "reason": f"{path.name}: артефакт не список ({type(hist).__name__})"}
    if not hist:
        return {"ok": False, "reason": f"{path.name}: история пуста (усечена или подменена)"}
    if not isinstance(hist[-1], dict):
        return {"ok": False, "reason": f"{path.name}: последний снимок не объект"}
    return {"ok": True, "reason": None}


def _method_epoch() -> str:
    """Эпоха метода = версия замороженной семьи. Смена метода — НОВЫЙ вопрос о той же паре:
    прошлый вердикт к ней не относится, и карантин ставится заново.

    ДЕЛЕГИРУЕТ в quarantine_db: формула была здесь, а CLI вердиктов считал эпоху по-своему
    (точнее — вовсе не считал, брал дефолт `''`) и не мог снять ни одной пары. Один источник."""
    import quarantine_db
    return quarantine_db.method_epoch()


def _quarantined_pairs(artifact: "Path | None" = None, conn=None, *, commit: bool = True,
                       entered: "list | None" = None) -> dict:
    """Пары в карантине СЕЙЧАС — состояние, а не diff последней недели.

    До 2026-07-26 функция возвращала ровно `entered` последнего снимка. Через неделю та же
    пара уже не была `entered`, множество пустело, и метка `pending_adjudication` снималась
    САМА — один стабильный прогон работал как вердикт (аудит P1-03). Теперь прибытие ставится
    в состояние (`queue_quarantine`), а снимает его только явный вердикт человека или
    контроллера (`scripts/adjudicate_quarantine.py`).

    `commit=False` (репетиция `--no-db`) НЕ ставит прибытия в состояние: до VG-R4-04 «сухой»
    прогон писал в `passset_quarantine` и коммитил — репетиция создавала пары, которые потом
    ждали человеческого вердикта, хотя веры за ними не стояло. Чтение состояния остаётся: без
    него саммари репетиции врало бы, будто карантина нет.
    """
    import quarantine_db
    epoch = _method_epoch()
    # `entered` передаётся из уже вычисленного снимка, когда постановка в карантин обязана
    # случиться ДО продвижения pass-set (VG-R5-02). Без него читаем из артефакта, как раньше.
    entered = _entered_pairs(artifact) if entered is None else entered
    if entered and commit:
        n = quarantine_db.queue_quarantine(entered, method_epoch=epoch, conn=conn)
        if n:
            print(f"  ⏳ в карантин поставлено пар: {n} (ждут явного вердикта)")
    elif entered:
        print(f"  🧪 репетиция: {len(entered)} прибытий НЕ поставлено в карантин (--no-db)")
    # ВОЗВРАЩАЕМ РЕШЕНИЕ по каждой паре, а не множество pending (ревью R5, VG-R5-01).
    # Было: множество pending — и `rejected` становился неотличим от «вердикта не было», то есть
    # отклонённая человеком пара возвращалась в веру обычной находкой. Три состояния едут типом.
    return quarantine_db.quarantine_decisions(method_epoch=epoch, conn=conn)


def _epoch_mark_fp(p: "Path") -> str:
    """Отпечаток маркера эпохи (mtime+размер). F2-04: одноразовость не может держаться на unlink —
    если удаление не удалось, тот же файл подавлял бы diff каждую неделю. Отпечаток едет в снимок."""
    try:
        st = p.stat()
        return f"{int(st.st_mtime_ns)}:{st.st_size}"
    except OSError:
        return ""


def _atomic_write_json(path: "Path", payload) -> None:
    """Атомарная запись: читатель видит либо ПРЕЖНИЙ файл, либо НОВЫЙ, но никогда — отсутствие.
    F2-02: раньше между rename повреждённого и записью нового канонического файла не существовало,
    и другой процесс в этом окне читал отсутствие истории как норму."""
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _free_backup_path(path: "Path", stamp: str) -> "Path":
    """Collision-safe имя улики. F2-05: два сброса в одну дату затирали первую улику — теперь
    ищем свободный суффикс, существующую цель НЕ перезаписываем никогда."""
    base = path.with_name(f"{path.stem}.corrupt-{stamp}{path.suffix}")
    if not base.exists():
        return base
    for n in range(2, 100):
        cand = path.with_name(f"{path.stem}.corrupt-{stamp}-{n}{path.suffix}")
        if not cand.exists():
            return cand
    return path.with_name(f"{path.stem}.corrupt-{stamp}-{os.getpid()}{path.suffix}")


def _write_passset_snapshot(cg, gate_meta: dict, artifact: "Path | None" = None,
                            *, write_to: "Path | None" = None, before_commit=None) -> dict:
    """Снимок членства pass-set. ВОЗВРАЩАЕТ СОСТОЯНИЕ ПЕРЕХОДА, а не bool (ревью R4).

    `{"ok": bool, "corrupt": str|None, "conflict": bool}`. Раньше возвращался bool, и
    вызывающий его игнорировал: отказ записи (OSError) и лечение повреждённой истории были
    невидимы для решения о публикации — вера уходила так, будто состояние перешло успешно
    (VG-R4-01). Лечение порчи сохранено (улика + сброс + громко), но теперь оно ЕЩЁ И
    сообщается наверх: этой недели вера не публикуется.

    `conflict=True` — между нашим чтением истории и записью файл изменил кто-то ещё
    (VG-R4-02: read-modify-write терял чужое обновление, оба процесса выходили с кодом 0).
    Проигравший НЕ пишет и НЕ публикует.

    Append-only снимок членства pass-set для integrity_tests.check_passset_flicker
    (Фаза 4 предохранитель). Делает МЕРЦАНИЕ ЧЛЕНСТВА наблюдаемым (раньше логировался только счёт →
    замена пары была невидима). Сравнение — с ПРЕДЫДУЩИМ снимком любой даты; head_sha остаётся
    в снимке как КОНТЕКСТ для читателя алерта, но НЕ как ключ сравнимости (2026-07-25: ключ на
    sha репо делал детектор немым навсегда). «Методология, а не данные» помечается вручную
    маркером EPOCH_BREAK_MARK. Один снимок на дату (повторный прогон в тот же день замещает).
    Сбой записи НЕ валит прогон, но и НЕ даёт публиковать веру."""
    if not gate_meta.get("gate_applied"):
        return {"ok": False, "corrupt": None, "conflict": False, "skipped": True}
    path = artifact or PASSSET_ARTIFACT      # ОТКУДА читаем историю (канон даже в репетиции)
    wpath = write_to or path                 # КУДА пишем (репетиция — в свою песочницу)
    _seen_txt = None            # что мы прочитали — база сравнения для CAS перед записью
    snap = {"date": str(get_today()), "head_sha": _head_sha(),
            "members": _passset_members(cg, gate_meta)}
    try:
        hist = []
        _reset = None
        _raw = None                                # сырое повреждённое содержимое (улика)
        if path.exists():
            try:
                _txt = path.read_text(encoding="utf-8")
                _seen_txt = _txt
                hist = json.loads(_txt)
                if not isinstance(hist, list):
                    hist, _reset, _raw = [], "артефакт не список", _txt
            except ValueError as _exc:             # битый JSON — читаемо, но не парсится
                hist, _reset = [], f"артефакт не читается: {type(_exc).__name__}"
                try:
                    _raw = path.read_text(encoding="utf-8", errors="replace")
                except OSError as _e0:
                    print(f"  ⚠️ улику не перечитать ({type(_e0).__name__}) — сохранять нечего")
                    _raw = None
            except OSError as _exc:                # не прочитать вовсе (каталог/права)
                hist, _reset, _raw = [], f"артефакт не читается: {type(_exc).__name__}", None
        if _reset is not None:
            # F2-02 (ревью раунд 2): ПОРЯДОК ВАЖЕН. Раньше повреждённый файл ПЕРЕИМЕНОВЫВАЛСЯ до
            # записи нового — и между rename и записью канонического файла НЕ СУЩЕСТВОВАЛО. Другой
            # процесс в этом окне видел отсутствие истории как норму и молчал; при сбое записи
            # история терялась целиком. Теперь улика сохраняется ПОСЛЕ успешной атомарной записи,
            # а до неё читатель видит прежний (пусть и битый) файл — на нём check_passset_flicker
            # кричит fail-loud. На любой точке отказа наблюдаемость сохраняется.
            print(f"  ⚠️ pass-set история сброшена: {_reset}")
            snap["history_reset"] = _reset
        # Флик-diff против предыдущего прогона (ДРУГАЯ дата — не сегодняшний повтор). Считаем
        # ЗДЕСЬ один раз и храним в снимке (единый источник для алерта и карантина).
        #
        # Ключ сравнимости БЕЗ head_sha (2026-07-25, аудит датчиков). Было `head_sha == head_sha`
        # с замыслом «сравнивать только снимки одного гейта». Замер: между воскресными прогонами
        # ложится 104–298 коммитов → sha ВСЕГДА другой → prev не находился НИКОГДА → детектор
        # два месяца рапортовал «стабильно», карантин был пуст по конструкции. Тихий отказ.
        # Теперь: сравниваем всегда, а «это методология, не данные» помечается ВРУЧНУЮ маркером
        # (EPOCH_BREAK_MARK) в момент правки решающего правила. Забыл маркер → ложная тревога,
        # опознаётся за секунды; у маркера нет тихой ветки, у sha-ключа она была.
        _brk, _fp = None, ""
        try:
            if EPOCH_BREAK_MARK.exists():
                _brk = EPOCH_BREAK_MARK.read_text(encoding="utf-8").strip()[:200] or "без причины"
                _fp = _epoch_mark_fp(EPOCH_BREAK_MARK)
        except OSError as _e1:
            print(f"  ⚠️ маркер эпохи не прочитан ({_e1!r}) — diff будет посчитан как обычно")
            _brk, _fp = None, ""
        # F2-04 (ревью раунд 2): «одноразовый» маркер становился МНОГОРАЗОВЫМ, если unlink не
        # удался (права/ФС) — тот же файл подавлял diff каждую неделю, а печать в stdout машинную
        # семантику не меняла. Теперь одноразовость держится на ОТПЕЧАТКЕ (mtime+размер), который
        # едет в снимок: маркер с уже потреблённым отпечатком игнорируется и кричит.
        if _brk and _fp and any(h.get("epoch_break_fp") == _fp for h in hist if isinstance(h, dict)):
            print(f"  ⚠️ маркер эпохи УЖЕ был потреблён (отпечаток {_fp}) — diff считается как "
                  f"обычно; удали {EPOCH_BREAK_MARK.name} вручную")
            _brk, _fp = None, ""
        # Маркер ПОТРЕБЛЯЕТСЯ только после успешной записи снимка (находка ревью 2026-07-25, P2):
        # раньше unlink стоял здесь, и при сбое записи маркер сгорал, не подавив ни одного diff.
        prev = next((h for h in reversed(hist) if h.get("date") != snap["date"]), None)
        if _brk:
            snap["epoch_break"] = _brk             # эпоха закрыта вручную → базы нет намеренно
            snap["epoch_break_fp"] = _fp           # отпечаток: второй раз тот же файл не сработает
        snap["flicker_vs_prev"] = ({} if (_brk or not prev)
                                   else _flicker_diff(prev.get("members") or {}, snap["members"]))
        if prev:                                   # контекст для читателя алерта (не для сравнения)
            snap["prev"] = {"date": prev.get("date"), "head_sha": prev.get("head_sha")}
        if hist and isinstance(hist[-1], dict) and hist[-1].get("date") == snap["date"]:
            # Повторный прогон в тот же день замещает снимок — но НЕ должен стирать факт сброса
            # истории (найдено мной 2026-07-25 при разборе раунда 2): иначе фикс F2-02 держался
            # ровно один прогон, а ручной перезапуск возвращал тихий отказ.
            _prior_reset = hist[-1].get("history_reset")
            if _prior_reset and "history_reset" not in snap:
                snap["history_reset"] = f"{_prior_reset} (унаследовано от снимка той же даты)"
            _prior_fp = hist[-1].get("epoch_break_fp")
            if _prior_fp and "epoch_break_fp" not in snap:
                snap["epoch_break_fp"] = _prior_fp          # отпечаток не теряем при замещении
            hist[-1] = snap
        else:
            hist.append(snap)
        hist = hist[-PASSSET_HISTORY_MAX:]
        # ПРЕДУСЛОВИЕ ПРОДВИЖЕНИЯ (ревью R5, VG-R5-02 — воспроизведено). Раньше pass-set
        # продвигался ПЕРВЫМ, а событие прибытия ставилось в карантин после. Если постановка
        # падала (SQLite недоступен), сегодняшний снимок всё равно оставался в истории — и на
        # следующей календарной дате та же пара уже не была `entered`: событие исчезало, пара
        # получала право войти в веру без чьего-либо вердикта. Теперь наоборот: сначала durable
        # ack события, и только потом продвижение. Исключение здесь означает, что снимок НЕ
        # записан, следующий прогон увидит то же прибытие снова.
        if before_commit is not None:
            before_commit(snap)
        wpath.parent.mkdir(parents=True, exist_ok=True)
        # VG-R4-02: CAS (compare-and-swap) по сырому тексту. Между нашим чтением и записью файл мог
        # изменить другой процесс — read-modify-write затирал его снимок, и ОБА выходили с кодом 0
        # («потерянное обновление» без единого следа). Сравниваем то, что читали, с тем, что лежит
        # СЕЙЧАС; расхождение → проигравший не пишет и не публикует. Это не транзакция (окно между
        # re-read и rename остаётся ~микросекунды), но окно сжимается с «весь прогон» до записи, а
        # главное — конфликт перестаёт быть тихим. В репетиции (wpath != path) CAS не нужен:
        # песочница наша единолично, а канон мы не трогаем вовсе.
        if wpath == path:
            try:
                _now_txt = path.read_text(encoding="utf-8") if path.exists() else None
            except OSError as _e4:                 # не перечитать → считаем расхождением, не пишем
                print(f"  ⚠️ CAS-перечитывание не удалось ({type(_e4).__name__}) — снимок не пишем")
                return {"ok": False, "corrupt": _reset, "conflict": True, "skipped": False}
            if _now_txt != _seen_txt:
                print("  ⚠️ pass-set снимок изменён параллельным процессом между чтением и записью "
                      "— наш снимок НЕ пишем, веру НЕ публикуем (VG-R4-02)")
                return {"ok": False, "corrupt": _reset, "conflict": True, "skipped": False}
        _atomic_write_json(wpath, hist)            # F2-02: либо старое, либо новое, без окна пустоты
        if _raw is not None and wpath == path:     # улика — ПОСЛЕ успешной записи канона
            try:
                _bak = _free_backup_path(path, snap["date"])
                _bak.write_text(_raw, encoding="utf-8")
                print(f"  ⚠️ повреждённая история сохранена как {_bak.name}")
            except OSError as _e2:
                print(f"  ⚠️ улику сохранить не удалось ({type(_e2).__name__}) — содержимое утеряно")
        if _brk and wpath == path:                 # снимок лёг — только теперь маркер потрачен
            try:                                   # (репетиция чужой маркер не тратит — VG-R4-04)
                EPOCH_BREAK_MARK.unlink()
            except OSError as _e3:
                print(f"  ⚠️ маркер эпохи не удалён ({_e3!r}); повторно он НЕ сработает "
                      f"(отпечаток записан в снимок), но сними файл вручную")
        # Запись состоялась. `corrupt` НЕ гасится успехом записи: история была потеряна, и вера
        # этой недели опирается на неполный ряд — публикация запрещена, хотя снимок лёг (VG-R4-01).
        return {"ok": True, "corrupt": _reset, "conflict": False, "skipped": False}
    except OSError as exc:
        print(f"  ⚠️ pass-set снимок не записан: {exc!r}")
        return {"ok": False, "corrupt": None, "conflict": False, "skipped": False}


class OutPathRefused(ValueError):
    """`--out` указывает не на артефакт аналитика. Отказ ДО работы, а не после."""


class RunLockBusy(RuntimeError):
    """Боевой прогон уже идёт. Второй не начинается — и НЕ трогает квитанцию первого."""


RUN_LOCK = artifact_path("logs/gate_run.lock")


def _acquire_run_lock(dry_run: bool):
    """Единственный писатель боевого состояния на весь переход (ревью R5, VG-R5-03/VG-R5-07).

    Что чинится. CAS по сырому тексту сравнивал прочитанное с лежащим на диске, но между
    сравнением и `os.replace` оставалось окно: ревьюер поставил два писателя барьером ровно в
    него — ОБА вернули `ok=True, conflict=False`, одно обновление потерялось молча
    (воспроизведено мной независимо, `plans/verify_r5_findings_2026-07-26.py`). Плюс квитанция —
    singleton: второй прогон затирал конверт первого, и отказ параллельного прогона исчезал.

    Почему лок, а не транзакция на четыре носителя. Отказу нужны два писателя в одни
    микросекунды; здесь один ночной launchd на Studio плюс редкий ручной перезапуск.
    Лок делает второго писателя невозможным и громким — §13, ступень 2 (fail-closed вместо
    эскалации), — и стоит десяти строк вместо переписывания персистентности. Настоящий ledger
    остаётся в follow-ups: у него своя цена и свои новые способы отказать.

    `flock` (а не файл-флаг) выбран сознательно: ядро снимает его при смерти процесса, поэтому
    SIGKILL не оставляет вечного замка, который пришлось бы снимать человеку.
    Репетиция (`--no-db`) лока НЕ берёт: она пишет только в свою песочницу."""
    if dry_run:
        return None
    import fcntl
    RUN_LOCK.parent.mkdir(parents=True, exist_ok=True)
    fh = RUN_LOCK.open("a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            _held = fh.read() or "?"
        except OSError as _e:
            _held = "?"
            print(f"  ⚠️ замок занят, но кем — не прочитать ({type(_e).__name__})")
        fh.close()
        raise RunLockBusy(
            f"боевой прогон уже идёт (замок {RUN_LOCK.name} держит {_held.strip()[:120]}). "
            f"Второй прогон НЕ начат: квитанция и снимок первого не тронуты. Дождись его "
            f"окончания или запусти репетицию `--no-db`.") from None
    fh.seek(0)
    fh.truncate()
    fh.write(f"pid={os.getpid()} at={str(get_now())[:19]}")
    fh.flush()
    return fh


def _release_run_lock(fh) -> None:
    if fh is None:
        return
    try:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        # Ядро снимет замок при закрытии файла и при смерти процесса — потери безопасности нет,
        # но молчать всё равно нельзя: неснятый замок объяснит будущий отказ следующего прогона.
        print(f"  ⚠️ замок прогона не снят явно ({type(exc).__name__}) — снимется при закрытии")
    finally:
        fh.close()


def _protected_paths() -> list:
    """Файлы, которые `--out` не имеет права затронуть: состояние гейта и БД.

    БД берём через health_db лениво: он тяжёлый и на не-Studio бросает по §8 — отсутствие
    ответа не должно снимать защиту с остального (fail-closed по частям, не по всему)."""
    paths = [GATE_RUN_RECEIPT, PASSSET_ARTIFACT, MC_GAP_ARTIFACT, EPOCH_BREAK_MARK]
    paths += [_dryrun_path(p) for p in (GATE_RUN_RECEIPT, PASSSET_ARTIFACT, MC_GAP_ARTIFACT)]
    try:
        import health_db as _hdb
        paths.append(Path(_hdb.DB_PATH))
    except Exception as exc:                   # noqa: BLE001 — §8-гвард на не-Studio: не молчим
        print(f"  ⚠️ путь БД не определён ({type(exc).__name__}) — из-под защиты --out он выпал, "
              f"остальные артефакты состояния защищены")
    return paths


def _validate_out_path(out_path: "Path") -> "Path":
    """Проверка ПУТИ ЗАПИСИ до любой работы (ревью R5, VG-R5-04).

    Воспроизведено ревьюером: `longitudinal_analysis.py --no-db --out <любой файл>` молча
    заменял этот файл ZIP-контейнером xlsx и выходил с кодом 0. `--no-db` при этом создавал
    ложное чувство безопасности («репетиция ничего не меняет»), а целью мог быть канонический
    `health.db`. Исполнитель здесь чаще всего не человек, а агент, который собрал команду.

    Правила (все — до записи): расширение ровно `.xlsx`; путь после раскрытия симлинков не
    совпадает с состоянием гейта или БД; существующий файл не является базой SQLite. Побочный
    `.json` пишется рядом с тем же стволом, поэтому защищается тем же расширением.
    Отказ = `OutPathRefused` до загрузки данных: «fail before work»."""
    p = Path(out_path).expanduser()
    if p.suffix.lower() != ".xlsx":
        raise OutPathRefused(
            f"--out должен указывать на .xlsx, получено «{p.name}». Отчёт — артефакт аналитика; "
            f"любой другой суффикс означает, что целью стал чужой файл.")
    rp = p.resolve()                                    # симлинк-алиасы раскрываем ДО сравнения
    for prot in _protected_paths():
        try:
            if rp == Path(prot).resolve() or (rp.exists() and Path(prot).exists()
                                              and os.path.samefile(rp, prot)):
                raise OutPathRefused(
                    f"--out указывает на состояние гейта или БД ({Path(prot).name}) — "
                    f"этот файл отчётом перезаписан не будет.")
        except OSError as exc:
            # Недостижимый защищаемый путь не снимает защиту с остальных, но и не молчит:
            # «сравнить не смог» и «не совпало» — разные вещи (класс, который чинил весь ремонт).
            print(f"  ⚠️ защищаемый путь {Path(prot).name} не проверен ({type(exc).__name__})")
            continue
    if rp.exists():
        try:
            if rp.open("rb").read(16).startswith(b"SQLite format 3"):
                raise OutPathRefused(
                    f"--out указывает на существующую базу SQLite ({rp.name}), пусть даже с "
                    f"расширением .xlsx. Запись отчёта уничтожила бы её.")
        except OSError as exc:
            raise OutPathRefused(f"--out нечитаем для проверки ({type(exc).__name__}): {rp}") from exc
    return p


def run(out_path: Path = DEFAULT_OUT, save_db: bool = True) -> dict:
    """Прогон как МАШИНА СОСТОЯНИЙ, а не как последовательность шагов (ревью R4).

    Порядок фаз обязателен и проверяется тестами полного пути:
      1. прочитать и провалидировать ПРЕЖНЕЕ состояние (до любой записи);
      2. вычислить переход (гейт, снимок членства, прибытия);
      3. решить о публикации ОДИН раз (`_publication_decision`);
      4. закоммитить состояние, потом веру;
      5. закрыть конверт прогона в `finally`.
    Раньше шаги 1 и 2 были переставлены (писатель лечил историю до читателя — VG-R4-01), а
    квитанция закрывалась не на всех выходах (VG-R4-03)."""
    _dry = not save_db
    # Порядок этих двух строк — часть контракта (ревью R5). Путь записи проверяется ДО загрузки
    # данных («fail before work», VG-R5-04), а замок берётся ДО первой квитанции: проигравший
    # второй прогон не должен затирать конверт первого (VG-R5-07).
    out_path = _validate_out_path(out_path)
    _lock = _acquire_run_lock(_dry)
    run_id = _new_run_id()
    # gate_meta — ОБЩИЙ изменяемый конверт: тело мутирует его на месте, `finally` читает итог.
    # Пересоздавать словарь в теле нельзя (иначе finally увидит пустой) — см. `_apply_gate` ниже.
    gate_meta: dict = {"run_id": run_id}
    _fatal: "str | None" = None
    _write_gate_run_receipt(gate_meta, False, run_id=run_id, phase="started", dry_run=_dry)
    try:
        return _run_body(out_path, save_db, run_id, gate_meta)
    except Exception as exc:
        _fatal = f"прогон упал: {exc!r}"
        raise
    finally:
        # Конверт закрывается ВСЕГДА и здесь единственный раз. `published` берём из gate_meta,
        # куда его кладёт тело сразу после фактической записи веры — не «намеревались писать».
        _write_gate_run_receipt(gate_meta, bool(gate_meta.get("published")), run_id=run_id,
                                phase="closed", dry_run=_dry,
                                error=_fatal or gate_meta.get("error"))
        _release_run_lock(_lock)


def _run_body(out_path: Path, save_db: bool, run_id: str, gate_meta: dict) -> dict:
    _dry = not save_db
    print("Загружаю данные...")
    phases    = load_phases()
    daily_df  = load_daily_df()
    labs_df   = load_labs_df()

    print(f"  daily_metrics: {len(daily_df)} строк, {daily_df['date'].min().date()} → {daily_df['date'].max().date()}")
    print(f"  lab_results:   {len(labs_df)} строк")
    print(f"  phases:        {len(phases)}")

    print("Аннотирую фазы...")
    daily_df = annotate_phases(daily_df, phases)

    print("Вычисляю погодовой тренд...")
    yearly = yearly_summary(daily_df)

    print("Вычисляю статистику по фазам...")
    phase_df = phase_summary(daily_df)

    print("Матрица корреляций (все данные)...")
    corr_all = correlation_matrix(daily_df)
    print(f"  {len(corr_all)} пар метрик")

    print("Лаговые корреляции...")
    corr_lagged = lagged_correlations(daily_df)

    print("Корреляции с лабораторными данными...")
    lab_corr = lab_metric_correlations(daily_df, labs_df)
    print(f"  {len(lab_corr)} пар лаб-метрика")

    # ── Фаза 1: прочитать и провалидировать ПРЕЖНЕЕ состояние ────────────────────────────
    # Строго ДО писателя. Порядок и есть фикс VG-R4-01: писатель лечит порчу, и если он ходит
    # первым, факт порчи исчезает до того, как о нём узнает решение о публикации.
    _prior = _validate_prior_passset()
    _prev_d = _last_d_members()          # до записи нового снимка — для «новая/ушла» в сообщении
    if not _prior["ok"]:
        print(f"  ⛔ прежнее состояние pass-set непригодно: {_prior['reason']}")

    print("Применяю статистический гейт (что попадёт в конституции)...")
    corr_all, lab_corr, _gm = _apply_gate(daily_df, labs_df, corr_all, lab_corr)
    gate_meta.update(_gm)                       # МУТИРУЕМ конверт, а не подменяем (см. run())
    if not _prior["ok"]:
        gate_meta["status"] = "failed"
        gate_meta["error"] = f"прежнее состояние pass-set непригодно: {_prior['reason']}"
        gate_meta["failure"] = "passset_prior"
    if gate_meta.get("gate_applied"):
        print(f"  гейт: daily {gate_meta['daily_pass']} прошло / "
              f"{gate_meta.get('daily_family_m', '?')} в семье BY / {gate_meta['daily_tested']} посчитано, "
              f"lab {gate_meta['lab_pass']}/{gate_meta['lab_tested']}")
    else:
        print(f"  ⛔ гейт НЕ применён ({gate_meta.get('error')}) — вера НЕ будет опубликована")
    # ── Фаза 2: вычислить и ЗАКОММИТИТЬ переход состояния ────────────────────────────────
    _mcgap_path = _dryrun_path(MC_GAP_ARTIFACT) if _dry else MC_GAP_ARTIFACT
    if _write_mc_gap_artifact(gate_meta, _mcgap_path):
        _n_flagged = sum(len((f or {}).get("flagged", []))
                         for f in gate_meta["mc_gap"]["families"].values() if f)
        print(f"  mc_gap артефакт записан ({_n_flagged} флагов)")
    _pass_w = _dryrun_path(PASSSET_ARTIFACT) if _dry else PASSSET_ARTIFACT
    # Карантин ставится ВНУТРИ перехода, до продвижения снимка (VG-R5-02). Решение по каждой
    # паре (pending/admitted/rejected) читается тут же — множество pending решением не является.
    _quar: dict = {}
    _carrier_err: "str | None" = None

    def _ack_arrivals(snap: dict) -> None:
        nonlocal _quar
        _quar = _quarantined_pairs(_pass_w, commit=not _dry, entered=_entered_from_snap(snap))

    try:
        _state = _write_passset_snapshot(corr_all, gate_meta, write_to=_pass_w,
                                         before_commit=_ack_arrivals)
    except QuarantineCarrierUnreadable as exc:
        _carrier_err = f"носитель карантина нечитаем — {exc}"
        print(f"  ⛔ {_carrier_err} — снимок НЕ продвинут, вера НЕ будет опубликована")
        _state = {"ok": False, "corrupt": None, "conflict": False, "skipped": True}
    if _state.get("ok"):
        _m = _passset_members(corr_all, gate_meta)
        print(f"  pass-set снимок: D={len(_m['D'])} A={len(_m['A'])} q_lag={len(_m['q_lag'])}")
    # Возврат писателя ТЕПЕРЬ участвует в решении (VG-R4-01b: раньше он молча отбрасывался, и
    # отказ записи состояния был неотличим от успеха — вера уходила поверх непереведённого
    # состояния). `skipped` — гейт не применён, отказ уже зафиксирован выше, второй раз не пишем.
    if not _state.get("skipped"):
        _why = ("снимок pass-set изменён параллельным прогоном (конфликт CAS)" if _state["conflict"]
                else f"история pass-set была повреждена: {_state['corrupt']}" if _state["corrupt"]
                else "снимок pass-set не записан" if not _state["ok"] else None)
        if _why:
            gate_meta["status"] = "failed"      # gate_applied НЕ трогаем: гейт-то отработал
            gate_meta["error"] = _why
            gate_meta["failure"] = ("passset_conflict" if _state["conflict"]
                                    else "passset_corrupt" if _state["corrupt"]
                                    else "passset_write")
            print(f"  ⛔ {_why} — вера НЕ будет опубликована")

    # Нечитаемый носитель события карантина = отказ прогона, та же ветка, что отказ гейта
    # (ревью R3, VG-R3-01). Само чтение и постановка теперь произошли ВЫШЕ, внутри перехода.
    if _carrier_err:
        gate_meta["status"] = "failed"          # gate_applied НЕ трогаем: гейт-то отработал
        gate_meta["error"] = _carrier_err
        gate_meta["failure"] = "quarantine_carrier"
        print(f"  ⛔ {gate_meta['error']} — вера НЕ будет опубликована")

    # ── Фаза 3: решение о публикации — ОДНО, здесь, после всех переходов состояния ────────
    _publish = _publication_decision(gate_meta)

    print("Траектория восстановления...")
    recovery = recovery_trajectory(daily_df, phases)

    print("Строю Excel...")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # удаляем пустой лист

    write_sheet_yearly(wb, yearly, phases)
    write_sheet_phases(wb, phase_df)
    write_sheet_correlations(wb, corr_all, corr_lagged, lab_corr)
    write_sheet_recovery(wb, recovery)

    wb.save(out_path)
    print(f"  Сохранено: {out_path}")

    if not _publish:
        # Fail-closed: прошлая вера остаётся как есть (решение владельца 2026-07-26 — её
        # возраст читатели показывают явно), новая НЕ публикуется. Excel выше сохранён:
        # это артефакт аналитика, не вера. Квитанцию НЕ пишем здесь — её закроет `finally`
        # в run() (VG-R4-03: раньше выходов было четыре, а квитанция стояла не на всех).
        print(f"\n⛔ ВЕРА НЕ ОПУБЛИКОВАНА: {gate_meta.get('error')}. "
              f"Квитанция отказа: {GATE_RUN_RECEIPT.name}. Прошлый отчёт остался последним.")
        return None

    # ── Фаза 4: коммит веры. `published` ставится ПОСЛЕ фактической записи, не до ─────────
    print("Строю AI-саммари...")
    # Карантин мерцающих (Ф4): множество прочитано выше, до решения о публикации.
    summary = build_ai_summary(yearly, phase_df, recovery, corr_all, lab_corr, phases,
                               gate_meta, quarantined=_quar)
    summary["gate"] = gate_meta                 # run_id едет в веру: датчик сверит идентичность

    # Сохраняем JSON рядом с xlsx. Сюда изоляция репетиции НЕ распространяется намеренно:
    # xlsx/json — артефакты АНАЛИТИКА, их путь задаёт вызывающий через --out (пробы кладут их
    # в $TMPDIR). В песочницу уводится только СОСТОЯНИЕ: квитанция, снимок, mc_gap, карантин.
    json_path = out_path.with_suffix(".json")
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"  Сохранено: {json_path}")

    if save_db:
        try:
            save_to_agent_reports(summary)
            gate_meta["published"] = True       # ← ЕДИНСТВЕННОЕ место, где это становится True
            print("  Сохранено в agent_reports (LongitudinalAnalyst)")
        except Exception as _exc:               # noqa: BLE001 — исход коммита выясняем, не гадаем
            print(f"  ⚠️ запись веры вернула ошибку ({type(_exc).__name__}) — выясняю исход "
                  f"read-back'ом по run_id, а не гадаю")
            # VG-R5-08 (ревью R5, воспроизведено): `commit` мог пройти, а подтверждение до нас
            # не дойти (соединение умерло). Раньше это давало квитанцию `failed` при ЗАПИСАННОЙ
            # вере — контракт «published означает вера записана» врал в обратную сторону, и
            # повторный прогон дописал бы дубль. Исход коммита теперь ЧИТАЕТСЯ, а не выдумывается.
            _outcome = _resolve_commit_outcome(run_id, _exc, gate_meta)
            if _outcome == "committed":
                print(f"  ⚠️ {gate_meta['commit_note']} — вера ЕСТЬ, повторять прогон не нужно")
            else:
                if _outcome == "indeterminate":
                    print(f"  ⛔ {gate_meta['error']}")
                raise                            # failed/indeterminate — прежняя ветка отказа
    else:
        print("  🧪 репетиция (--no-db): вера НЕ записана, артефакты в logs/dryrun/")

    if gate_meta.get("published"):
        # Человеку — одной строкой что нашёл поиск (решение владельца 25.09). До этого результат видели
        # только промпты врачей, и с 14.08 там было пусто — никто не знал, работает ли поиск.
        try:
            _cur = []
            if "gate_pass" in corr_all.columns:
                for _r in corr_all[corr_all["gate_pass"].fillna(False).astype(bool)].itertuples():
                    _cur.append((f"{_r.metric_a}×{_r.metric_b}", _r.label_a, _r.label_b, float(_r.spearman_r)))
            import notify
            notify.notify(links_note(_prev_d, _cur, int(gate_meta.get("daily_family_m") or 0),
                                     str(get_today())), fallback=False)
        except Exception as _e:  # silent-ok: вера записана; сообщение — удобство, не канон
            print(f"  ⚠️ сообщение о связях не отправлено: {type(_e).__name__}: {_e}")

    # Быстрый вывод ключевых находок
    print("\n=== КЛЮЧЕВЫЕ НАХОДКИ ===")
    print("\nТоп-5 корреляций (прошедшие гейт):")
    _top = corr_all[corr_all["gate_pass"]] if "gate_pass" in corr_all.columns \
        else corr_all[corr_all["significant"]]
    for _, r in _top.head(5).iterrows():
        print(f"  {r['label_a']} ↔ {r['label_b']}: r={r['spearman_r']}, p={r['p_value']}")

    print("\nВосстановление vs baseline:")
    for metric, info in sorted(recovery.items(), key=lambda x: x[1].get("pct_of_baseline") or 0):
        pct = info["pct_of_baseline"]
        if pct:
            print(f"  {info['label']:30} baseline={info['baseline_mean']:.1f}  current={info['current_mean']:.1f}  ({pct}%) {info['trend_dir']}")

    print("\nЛаб-корреляции (прошедшие гейт):")
    _lab = lab_corr[lab_corr["gate_pass"]] if "gate_pass" in lab_corr.columns \
        else lab_corr[lab_corr["strong"] & lab_corr["significant"]]
    for _, r in _lab.head(8).iterrows():
        print(f"  {r['lab']:12} ↔ {r['label']:25}: r={r['spearman_r']}, p={r['p_value']:.4f}")

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Longitudinal health analysis")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output Excel path")
    parser.add_argument("--no-db", action="store_true", help="Don't save to agent_reports")
    args = parser.parse_args()
    # Коды выхода — контракт для человека, launchd и будущего контроллера §5. Голый traceback
    # сообщает Python-тип, а не что делать (тот же урок, что в CLI карантина, VG-R4-08).
    try:
        run(out_path=Path(args.out), save_db=not args.no_db)
    except OutPathRefused as _exc:
        print(f"⛔ путь отчёта отклонён: {_exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except RunLockBusy as _exc:
        print(f"⛔ {_exc}", file=sys.stderr)
        raise SystemExit(4) from None
