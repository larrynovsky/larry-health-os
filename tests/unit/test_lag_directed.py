"""Направленный лаг-класс (Группа 2 ч.2 fdr-online) — DORMANT, валидация нуля.

Guard'ы helper'ов канона (_lag_within_epochs / _strat_pvalue_lagged) + ФЕНС: лаг-класс НЕ подключён
к gate_correlations (валидируем нуль twin'ом twin_lag_directed.py, промоут — по бюджету m владельца).
Полная калибровка type-I/мощности — в twin (methodology/validation_gate/twin_lag_directed.py), не здесь.
Здесь — детерминированные сид-контроли: внедрённый лаг ловится (мощность), H0 не значим, детерминизм.
"""
from __future__ import annotations

import re
import pathlib

import numpy as np

import correlation_gate as G

_L = 400
_EPI = [np.arange(_L)]


def _stat(x, y, lag, seed=7, B=20000):
    return G._strat_pvalue_lagged(G._grank(__import__("pandas").Series(x)),
                                  G._grank(__import__("pandas").Series(y)),
                                  _EPI, 0.0, 1.0, B, np.random.default_rng(seed), lag)[0]


def test_lag_within_epochs():
    """target[t+lag] выравнивается на t ВНУТРИ эпохи; последние lag дней → NaN (без течи через границу)."""
    a = np.arange(10.0)
    epi = [np.arange(5), np.arange(5, 10)]      # две эпохи по 5
    out = G._lag_within_epochs(a, epi, 1)
    assert out[0] == 1.0 and out[3] == 4.0 and np.isnan(out[4]), "внутри 1-й эпохи сдвиг+граница"
    assert out[5] == 6.0 and out[8] == 9.0 and np.isnan(out[9]), "внутри 2-й эпохи сдвиг+граница"
    # значение из следующей эпохи НЕ протекает: out[4] (конец эпохи) = NaN, не a[5]
    assert np.isnan(out[4])


def test_power_injected_lag_detected():
    """Внедрённый направленный лаг y[t]←x[t-1] (сид-детерминир.) ловится: p ≤ 0.05."""
    rng = np.random.default_rng(1)
    x = rng.normal(size=_L)
    y = 0.8 * np.concatenate([[0.0], x[:-1]]) + 0.4 * rng.normal(size=_L)
    assert _stat(x, y, 1) <= 0.05, "направленный лаг обязан ловиться"


def test_null_no_lag_not_significant():
    """H0 (x⊥y, нет лага; сид-детерминир.) → p > 0.05 на этом сиде (одна реализация; калибровка — twin)."""
    rng = np.random.default_rng(3)
    x = rng.normal(size=_L)
    y = rng.normal(size=_L)
    assert _stat(x, y, 1) > 0.05, "независимые ряды не должны давать направленную связь на этом сиде"


def test_determinism():
    rng = np.random.default_rng(5)
    x = rng.normal(size=_L)
    y = 0.7 * np.concatenate([[0.0], x[:-1]]) + 0.5 * rng.normal(size=_L)
    assert _stat(x, y, 1, seed=11) == _stat(x, y, 1, seed=11), "тот же сид → тот же p"


def test_lag_class_dormant_not_wired():
    """ФЕНС: лаг-класс НЕ подключён к gate_correlations (не в вере до промоута/бюджета)."""
    src = pathlib.Path(G.__file__).read_text(encoding="utf-8")
    gc = re.search(r"\ndef gate_correlations\(.*?\n    return cg, lg, meta", src, re.S).group(0)
    assert "_strat_pvalue_lagged" not in gc, "лаг-класс DORMANT: не должен вызываться в gate_correlations"
    assert "_lag_within_epochs" not in gc
