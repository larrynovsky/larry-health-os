"""Причинное уточнение A-рычагов (Группа 2 fdr-online) — ОПИСАТЕЛЬНОЕ, вне бюджета q_A/q_D.

Проверяем МЕХАНИЗМ (быстрый остаток убивает медленную общую причину, держит суточную связь),
а не «истину рычага» — оракула истины на N=1 нет (Oracle-Problem). Контроли детерминированы по
сиду (golden-стиль): позитивный → «рычаг», со-причина медленного фона → «совпадение». Плюс guard'ы:
owner-only, пусто для не-A-рычага, D-гейт не тронут, детерминизм, единый читатель слов. Сквозной контроль — на независимой синтетике.
"""
from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd
import pytest

import correlation_gate as G

_EMPTY_LAB = pd.DataFrame(columns=["lab", "metric", "label", "spearman_r",
                                   "p_value", "n", "significant", "strong"])
_EMPTY_LABS = pd.DataFrame(columns=["date", "test_name", "value", "unit"])


def _daily(cols=("sleep_deep", "hrv"), seed=0):
    rng = np.random.default_rng(seed)
    d = pd.date_range("2019-08-01", "2026-01-31", freq="D")
    out = {"date": d}
    for c in cols:
        out[c] = rng.normal(size=len(d))
    return pd.DataFrame(out)


def _corr(pairs):
    return pd.DataFrame([{"metric_a": a, "metric_b": b, "spearman_r": 0.1, "p_value": 0.3,
                          "significant": False, "strong": False} for a, b in pairs])


# ── строительный набор синтетики для суб-теста (одна длинная эпоха) ──
_L = 400
_EPI = [np.arange(_L)]
_DOW = np.arange(_L) % 7


def _same_night_pair(seed=1):
    """Одновременная связь той же ночи (без предшествования) → рычаг НЕ отделить → не_знаю."""
    rng = np.random.default_rng(seed)
    x = rng.normal(size=_L)
    y = x + 0.4 * rng.normal(size=_L)
    return x, y


def _lagged_pair(seed=1):
    """Направленная связь с предшествованием: y[t] ← x[t-1] (a ведёт b на день) → рычаг."""
    rng = np.random.default_rng(seed)
    x = rng.normal(size=_L)
    y = 0.8 * np.concatenate([[0.0], x[:-1]]) + 0.4 * rng.normal(size=_L)
    return x, y


def _slow_shared_pair(seed=2):
    """Связь ТОЛЬКО через ГЛАДКИЙ медленный общий сигнал; быстрые части независимы → совпадение."""
    rng = np.random.default_rng(seed)
    t = np.arange(_L)
    slow = np.sin(2 * np.pi * t / 90.0) + t / 300.0
    x = 3.0 * slow + rng.normal(size=_L)
    y = 3.0 * slow + rng.normal(size=_L)
    return x, y


def test_causal_label_single_source():
    """causal_label — единственный источник слов; пусто для отсутствия/неизвестного (backward-compat)."""
    assert G.causal_label("рычаг").startswith("похоже на управляемую")
    assert G.causal_label("совпадение").startswith("похоже на совпадение")
    assert G.causal_label("не_знаю").startswith("неясно")
    assert G.causal_label("") == "" and G.causal_label(None) == "" and G.causal_label("wat") == ""


def test_shift_nan():
    a = np.arange(5.0)
    assert np.array_equal(G._shift_nan(a, 0), a)
    r1 = G._shift_nan(a, 1)
    assert np.isnan(r1[0]) and r1[1] == 0.0 and r1[4] == 3.0
    rm = G._shift_nan(a, -1)
    assert np.isnan(rm[4]) and rm[0] == 1.0 and rm[3] == 4.0


def test_residual_mechanism_deterministic():
    """Ядро метода (детерминировано, без permutation): быстрый остаток ДЕРЖИТ одновременную суточную
    связь и УБИВАЕТ связь, жившую в гладком медленном общем фоне. На этом стоит рычаг vs совпадение."""
    nx, ny = _same_night_pair()
    r_raw = abs(G._masked_corr(nx - np.nanmean(nx), ny - np.nanmean(ny)))
    urx = G._fast_residual(nx, _EPI, _DOW, G.CAUSAL_RESID_WINDOW)
    ury = G._fast_residual(ny, _EPI, _DOW, G.CAUSAL_RESID_WINDOW)
    r_res = abs(G._masked_corr(urx, ury))
    assert r_raw > 0.6 and r_res > 0.5, "суточная связь обязана пережить быстрый остаток"

    cx, cy = _slow_shared_pair()
    r_raw_co = abs(G._masked_corr(cx - np.nanmean(cx), cy - np.nanmean(cy)))
    urcx = G._fast_residual(cx, _EPI, _DOW, G.CAUSAL_RESID_WINDOW)
    urcy = G._fast_residual(cy, _EPI, _DOW, G.CAUSAL_RESID_WINDOW)
    r_res_co = abs(G._masked_corr(urcx, urcy))
    assert r_raw_co > 0.5, "со-причина: в сыром виде связь есть"
    assert r_res_co < 0.25, "со-причина: после снятия гладкого медленного фона связь обязана исчезнуть"
    assert r_res_co < 0.5 * r_raw_co, "остаток должен резко ослабить медленную со-причину"


def _verdict(pair, seed=7, B=None):
    return G._causal_verdict(*pair, _EPI, _DOW, 0.0, G.CAUSAL_ALPHA, 1.0,
                             B or G.CAUSAL_B_PERM, np.random.default_rng(seed), G.CAUSAL_LAG_DAYS)


def test_lever_control_directed():
    """Позитив (сид-детерминирован): направленное предшествование y←x[t-1] → 'рычаг'."""
    v, prov = _verdict(_lagged_pair())
    assert v == "рычаг", f"ждём рычаг, получили {v} ({prov})"
    assert prov["p_fwd"] is not None and prov["p_fwd"] <= G.CAUSAL_ALPHA


def test_coincidence_control():
    """Со-причина ГЛАДКОГО медленного фона (сид-детерминирован): быстрый остаток пуст → 'совпадение'."""
    v, prov = _verdict(_slow_shared_pair())
    assert v == "совпадение", f"ждём совпадение, получили {v} ({prov})"


def test_same_night_unknown():
    """Только одновременная связь (без предшествования) → честно 'не_знаю' (N=1 не отделяет)."""
    v, prov = _verdict(_same_night_pair())
    assert v == "не_знаю", f"ждём не_знаю, получили {v} ({prov})"


def test_verdict_is_one_of_three():
    """Вердикт всегда из трёх допустимых (никаких неожиданных строк)."""
    for pair in (_lagged_pair(), _slow_shared_pair(), _same_night_pair()):
        v, _ = _verdict(pair, seed=0, B=2000)
        assert v in ("рычаг", "совпадение", "не_знаю")


def test_columns_owner_only():
    """enable_stratified=True → колонка verdict_causal есть; False → её нет (тенант)."""
    daily, corr = _daily(), _corr([("sleep_deep", "hrv")])
    cg_on, _, meta_on = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB, enable_stratified=True)
    cg_off, _, _ = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB, enable_stratified=False)
    assert "verdict_causal" in cg_on.columns and "causal_p0" in cg_on.columns
    assert "verdict_causal" not in cg_off.columns
    assert isinstance(meta_on["stratified"]["causal"], dict)


def test_causal_empty_for_non_a_lever():
    """Случайная пара (не A-рычаг) → verdict_causal == '' (уточняем ТОЛЬКО рычаги)."""
    daily, corr = _daily(seed=3), _corr([("sleep_deep", "hrv")])
    cg, _, _ = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB, enable_stratified=True)
    row = cg.iloc[0]
    if not bool(row.get("a_lever")):
        assert row["verdict_causal"] == "", "не-A-рычаг не должен получать причинный вердикт"


def test_d_gate_pass_unchanged():
    """Причинный слой НЕ трогает состав/gate_pass семьи D (сидит только внутри stratified)."""
    daily, corr = _daily(), _corr([("sleep_deep", "hrv")])
    cg_off, _, _ = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB, enable_stratified=False)
    cg_on, _, _ = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB, enable_stratified=True)
    assert list(cg_on["gate_pass"]) == list(cg_off["gate_pass"])
    assert list(cg_on["p_perm"].fillna(-1)) == list(cg_off["p_perm"].fillna(-1))


def test_both_renderers_use_causal_label():
    """Единый читатель слов: конституции и gp_context зовут causal_label (не пересчитывают)."""
    root = pathlib.Path(G.__file__).resolve().parent
    for fn in ("generate_constitutions.py", "gp_context.py"):
        src = (root / fn).read_text(encoding="utf-8")
        assert "causal_label" in src, f"{fn} обязан читать причинную метку через causal_label"


# Сквозная доразметка на независимо сгенерированной связи.
def test_simulated_pair_has_causal_verdict(monkeypatch):
    # Независимый ряд и эпоха; ни личной БД, ни fitted-параметров.
    daily = _daily(cols=("fixture_q", "fixture_r"), seed=47).iloc[:420].copy()
    daily["date"] = pd.date_range("2001-02-01", periods=len(daily), freq="D")
    daily["fixture_r"] = 0.7 * daily["fixture_q"] + 0.5 * daily["fixture_r"]
    monkeypatch.setattr(G, "EPOCHS", [("2001-02-01", "2002-03-27")])
    monkeypatch.setattr(G, "BASELINE_ACF", {})
    corr = _corr([("fixture_q", "fixture_r")])
    cg, _, meta = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB, enable_stratified=True)
    row = cg.iloc[0]
    assert row["verdict_family"] == "A-lever"
    assert row["verdict_causal"] in ("рычаг", "совпадение", "не_знаю")
    assert G.causal_label(row["verdict_causal"]) != ""
    assert sum(meta["stratified"]["causal"].values()) == meta["stratified"]["a_levers"]


def test_simulated_pair_determinism(monkeypatch):
    # Независимый ряд и эпоха; ни личной БД, ни fitted-параметров.
    daily = _daily(cols=("fixture_q", "fixture_r"), seed=47).iloc[:420].copy()
    daily["date"] = pd.date_range("2001-02-01", periods=len(daily), freq="D")
    daily["fixture_r"] = 0.7 * daily["fixture_q"] + 0.5 * daily["fixture_r"]
    monkeypatch.setattr(G, "EPOCHS", [("2001-02-01", "2002-03-27")])
    monkeypatch.setattr(G, "BASELINE_ACF", {})
    corr = _corr([("fixture_q", "fixture_r")])
    cg1, _, _ = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB, enable_stratified=True, seed=0)
    cg2, _, _ = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB, enable_stratified=True, seed=0)
    assert list(cg1["verdict_causal"]) == list(cg2["verdict_causal"]), "тот же сид → тот же вердикт"
