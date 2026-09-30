"""Фаза C fdr-online: проводка семьи A в конституции/бриф (доразметка verdict_family).

Owner-only (enable_stratified), единый источник формулировки (family_label), backward-compat
(нет поля → рендер как раньше), единый читатель метки (оба рендера зовут family_label).
Все ряды независимо сгенерированы; личная БД не читается.
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


def test_family_label_single_source():
    """family_label — единственный источник слов; пустая строка для отсутствия (backward-compat)."""
    # 2026-07-31: метка описывает ПРОЦЕДУРУ, а не величину. Прежняя формулировка
    # «устойчивая связь (держится внутри стабильных периодов)» обещала силу связи, которую
    # strat_hi не доказывает: при n≈2500 значимый p достигается и при r=0.12.
    assert G.family_label("A-lever") == "не сводится к смене периода (проверено по периодам отдельно)"
    assert "устойчив" not in G.family_label("A-lever"), (
        "метка не имеет права обещать величину — её говорит epoch_label"
    )
    assert G.family_label("D-only") == "совместное движение (может быть следствием смены периода)"
    assert G.family_label("") == "" and G.family_label(None) == "" and G.family_label("wat") == ""


def test_owner_flag_gates_stratified():
    """enable_stratified=True → колонка verdict_family есть; False → её нет (тенант/по умолчанию)."""
    daily, corr = _daily(), _corr([("sleep_deep", "hrv")])
    cg_on, _, meta_on = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB,
                                            enable_stratified=True)
    cg_off, _, meta_off = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB,
                                              enable_stratified=False)
    assert "verdict_family" in cg_on.columns, "owner: разметка A/D обязана присутствовать"
    assert "verdict_family" not in cg_off.columns, "тенант: разметки A/D быть НЕ должно"
    assert meta_on["stratified"]["applied"] is True
    assert meta_off["stratified"]["applied"] is False


def test_stratified_does_not_change_d_gate_pass():
    """Доразметка A НЕ меняет состав/gate_pass семьи D (Q3: тот же набор)."""
    daily, corr = _daily(), _corr([("sleep_deep", "hrv")])
    cg_off, _, _ = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB, enable_stratified=False)
    cg_on, _, _ = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB, enable_stratified=True)
    assert list(cg_on["gate_pass"]) == list(cg_off["gate_pass"]), "gate_pass D не должен зависеть от A"
    assert list(cg_on["p_perm"].fillna(-1)) == list(cg_off["p_perm"].fillna(-1))


def test_both_renderers_use_family_label():
    """Единый читатель метки: и конституции, и gp_context зовут correlation_gate.family_label
    (не пересчитывают A/D сами) — структурный guard против split-brain читателей."""
    root = pathlib.Path(G.__file__).resolve().parent
    for fn in ("generate_constitutions.py", "gp_context.py"):
        src = (root / fn).read_text(encoding="utf-8")
        assert "family_label" in src, f"{fn} обязан читать метку через family_label"


# Сквозная доразметка на независимо сгенерированной связи.
def test_simulated_pair_tagged_a_lever(monkeypatch):
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
    label = G.family_label(row["verdict_family"])
    assert label and label != G.family_label("D-only")
    assert meta["stratified"]["a_levers"] == 1
