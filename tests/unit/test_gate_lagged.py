"""Промоут направленной лаг-семьи q_lag (Группа 2 ч.2 fdr-online) — в вере, owner-only.

_gate_lagged (strat_hi на pred@T→tgt@T+lag, BY на q_lag) + проводка в meta["stratified"]["lagged"] →
build_ai_summary top_lagged → рендеры (lag_label). Guard'ы: owner-only, derived-исключение, детерминизм,
ФЕНС наивного (naive lagged_correlations вне веры — build_ai_summary берёт из gate_meta, не corr_lagged).
Golden на каноне со skip. Полная валидация нуля — twin_lag_directed + test_null_validity_sensor.
"""
from __future__ import annotations

import os
import pathlib
import sqlite3

import numpy as np
import pandas as pd
import pytest

import correlation_gate as G
import sys as _sys
_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "methodology/validation_gate"))
import longitudinal_analysis as la

_EMPTY_LAB = pd.DataFrame(columns=["lab", "metric", "label", "spearman_r", "p_value", "n", "significant", "strong"])
_EMPTY_LABS = pd.DataFrame(columns=["date", "test_name", "value", "unit"])


def _daily(seed=0):
    rng = np.random.default_rng(seed)
    d = pd.date_range("2019-08-01", "2026-01-31", freq="D")
    out = {"date": d}
    for c in ("hrv", "sleep_total", "sleep_deep", "steps", "sleep_score", "readiness"):
        out[c] = rng.normal(size=len(d))
    return pd.DataFrame(out)


def _corr(pairs):
    return pd.DataFrame([{"metric_a": a, "metric_b": b, "spearman_r": 0.1, "p_value": 0.3,
                          "significant": False, "strong": False} for a, b in pairs])


def test_lag_family_owner_only():
    """enable_stratified=True → meta.stratified.lagged есть (список); False → нет лаг-находок."""
    daily, corr = _daily(), _corr([("hrv", "steps")])
    _, _, meta_on = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB, enable_stratified=True)
    _, _, meta_off = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB, enable_stratified=False)
    assert isinstance(meta_on["stratified"]["lagged"], list)
    assert meta_off["stratified"].get("lagged", []) == []


def test_derived_excluded_from_lag_family():
    """Композиты (sleep_score/readiness) НЕ участвуют в направленной семье (композит×вход, §6/R1)."""
    df = G._gate_lagged(_daily(), seed=0)
    if not df.empty:
        involved = set(df["predictor"]) | set(df["target"])
        assert "sleep_score" not in involved and "readiness" not in involved
    # состав пар: preds/tgts без derived, без self
    preds = [m for m in G.LAGGED["predictors"] if m not in G.KNOWN_DERIVED]
    tgts = [m for m in G.LAGGED["targets"] if m not in G.KNOWN_DERIVED]
    assert "sleep_score" not in preds and "readiness" not in tgts


def test_lag_determinism():
    daily = _daily()
    a = G.gate_correlations(daily, _EMPTY_LABS, _corr([("hrv", "steps")]), _EMPTY_LAB, enable_stratified=True, seed=0)[2]
    b = G.gate_correlations(daily, _EMPTY_LABS, _corr([("hrv", "steps")]), _EMPTY_LAB, enable_stratified=True, seed=0)[2]
    assert a["stratified"]["lagged"] == b["stratified"]["lagged"], "тот же сид → тот же лаг-набор"


def test_naive_lagged_fenced_from_belief():
    """ФЕНС: наивный lagged_correlations (spearman) НЕ в вере. build_ai_summary берёт лаг из gate_meta,
    НЕ из corr_lagged; без gate_meta top_lagged отсутствует."""
    src = pathlib.Path(la.__file__).read_text(encoding="utf-8")
    import re
    fn = re.search(r"\ndef build_ai_summary\(.*?\n    return out", src, re.S).group(0)
    assert "corr_lagged" not in fn, "build_ai_summary не должен читать наивный corr_lagged"
    assert "gate_meta" in fn and "top_lagged" in fn
    # без gate_meta ключа top_lagged нет. Кадр гейтованный: с 2026-07-26 build_ai_summary
    # отказывается строить веру из негейтованного corr_all (fail-closed, P1-01).
    _yr = pd.DataFrame({"year": [2020, 2021]})
    _gated = _corr([("hrv", "steps")]).assign(gate_pass=True, p_perm=0.001, derived=False)
    s = la.build_ai_summary(_yr, pd.DataFrame(columns=["phase", "phase_type", "start", "end", "n_days"]),
                            {}, _gated, _EMPTY_LAB, [])
    assert "top_lagged" not in s


def test_lag_label_single_source():
    assert G.lag_label("lag-lever").startswith("устойчивое предшествование")
    assert G.lag_label("") == "" and G.lag_label(None) == "" and G.lag_label("wat") == ""


def test_both_renderers_use_lag_label():
    root = pathlib.Path(G.__file__).resolve().parent
    for fn in ("generate_constitutions.py", "gp_context.py"):
        assert "lag_label" in (root / fn).read_text(encoding="utf-8"), f"{fn} обязан читать lag_label"


# ── golden на каноне ──────────────────────────────────────────────────────────
_DB = str(__import__("pathlib").Path.home() / "health/data/health.db")
try:
    _OK = os.path.exists(_DB)
    if _OK:
        _con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
        _cdaily = pd.read_sql_query(
            "SELECT date,hrv,sleep_total,sleep_deep,steps,sleep_score,readiness FROM daily_metrics ORDER BY date",
            _con, parse_dates=["date"])
        _con.close()
        _OK = len(_cdaily) > 1000
except Exception:
    _OK = False


@pytest.mark.skipif(not _OK, reason="нужна каноническая БД (Studio)")
def test_canon_lag_family_reachable():
    """На каноне: лаг-семья тестируется (m>0), BY-порог достижим (не мёртвая семья), леджер сходится."""
    df = G._gate_lagged(_cdaily, seed=0)
    assert not df.empty and df["p_lag"].notna().sum() >= 6, "лаг-семья должна тестироваться (m≈18)"
    # никаких derived
    assert not ({"sleep_score", "readiness"} & (set(df["predictor"]) | set(df["target"])))
    _, _, meta = G.gate_correlations(_cdaily, _EMPTY_LABS, _corr([("hrv", "steps")]), _EMPTY_LAB, enable_stratified=True)
    assert meta["stratified"]["lag_levers"] == len(meta["stratified"]["lagged"])


def _fake_lag_pass(daily_train, seed):
    """Лаг-гейт, у которого одна пара ПРОШЛА — чтобы проверять ворота публикации, а не данные."""
    return pd.DataFrame([{"predictor": "steps", "target": "hrv", "lag_days": 1, "p_lag": 1e-5,
                          "gate_pass_lag": True, "verdict_lag": "lag-lever"}])


@pytest.mark.parametrize("published, expect_n", [(False, 0), (True, 1)])
def test_приостановка_q_lag_держит_публикацию(monkeypatch, published, expect_n):
    """BL-TWIN-BAND-1 (22.09): при status≠gated_q_lag прошедшая лаг-пара НЕ едет в веру, а статус
    стоит рядом с числом — «0 лаг-рычагов» при приостановке отличим от «проверили, не нашли».
    Позитивный контроль (published=True) обязателен: иначе тест зеленел бы на гейте, который
    не публикует никогда."""
    monkeypatch.setattr(G, "LAG_PUBLISHED", published)
    monkeypatch.setattr(G, "_gate_lagged", _fake_lag_pass)
    daily, corr = _daily(), _corr([("hrv", "steps")])
    _, _, meta = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB, enable_stratified=True)
    assert len(meta["stratified"]["lagged"]) == expect_n
    assert meta["stratified"]["lag_status"] == G.LAGGED.get("status")


def test_публикация_q_lag_стоит_на_артефакте_перепроверки():
    """Право q_lag на веру привязано к ДАННЫМ, а не к слову: status=gated_q_lag допустим, только если
    артефакт PREREG-перепроверки лежит в репозитории, сделан по правилу из twin'а и допускает ВСЕ
    объявленные лаги. Исключённый лаг в семье или вера без артефакта — красный (BL-TWIN-BAND-1)."""
    import json
    import twin_lag_directed as T
    art = pathlib.Path(__file__).resolve().parents[2] / "methodology/validation_gate/twin_lag_revalidation_2026-09-22.json"
    if G.LAGGED.get("status") != "gated_q_lag":
        assert G.LAG_PUBLISHED is False
        return
    assert art.exists(), "q_lag в вере без артефакта перепроверки"
    d = json.loads(art.read_text(encoding="utf-8"))
    assert d["prereg"]["reps_null"] >= T.REPS_NULL and abs(d["prereg"]["alpha_cell"] - T.ALPHA_CELL) < 1e-12
    assert len(d["cells"]) == len(T.LAGS) * len(T.PHIS) * len(T.ETAS)
    for lag in G.LAGGED["lags_days"]:
        assert d["verdicts"][str(lag)] == "admit", f"лаг {lag} в семье, а перепроверка его не допустила"
    # вердикт пересчитывается из ячеек тем же правилом — артефакт нельзя «подправить» руками
    assert {str(k): v for k, v in T.lag_verdicts(
        [{"lag": c["lag"], "verdict": T.cell_verdict(c["x"], c["n"])} for c in d["cells"]]).items()} == d["verdicts"]
