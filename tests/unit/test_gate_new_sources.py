"""v10 семьи (нить new-sources, решение владельца 25.09 «дать новым источникам шанс»).

Три оракула, каждый краснеет на откате своей правки:
  1. p пары, покрывающей часть сетки, не занижен (старый расчёт — занижен: зубы проверены тут же);
  2. меньше года общих дней — «не тестировалась» (вне m); год с сильной связью — проходит,
     хотя доля сетки 0.2 (старый coverage_min 0.40 её бы срезал);
  3. frozen_at сдвигается по кварталам и никогда не назад.
"""
from __future__ import annotations

from datetime import date

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

import correlation_gate as G   # noqa: E402
import signal_family as SF     # noqa: E402

_EMPTY_LAB = pd.DataFrame(columns=["lab", "metric", "label", "spearman_r",
                                   "p_value", "n", "significant", "strong"])
_EMPTY_LABS = pd.DataFrame(columns=["date", "test_name", "value", "unit"])


def _ar1(phi, n, rng):
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = phi * x[t - 1] + rng.normal()
    return x


def _pair(rng, n_grid, n_cov, phi):
    """Две НЕЗАВИСИМЫЕ метрики, обе видны только в последние n_cov дней сетки n_grid."""
    a = np.full(n_grid, np.nan)
    b = np.full(n_grid, np.nan)
    a[-n_cov:] = _ar1(phi, n_cov, rng)
    b[-n_cov:] = _ar1(phi, n_cov, rng)
    return G._grank(pd.Series(a)), G._grank(pd.Series(b))


def test_pair_p_does_not_depend_on_empty_grid():
    """p пары — свойство её общих дней, а не длины сетки. Та же пара, приклеенная к 5000 пустым
    дням, обязана дать ТОТ ЖЕ p (тот же сид). До v10 сдвиг шёл по всей сетке и негодный сдвиг
    считался «не превысил»: пустые дни сами тянули p к 1/(B+1) — откат краснеет здесь."""
    rng = np.random.default_rng(3)
    ra, rb = _pair(rng, 700, 700, 0.5)
    rb = 0.05 * ra + rb                                  # слабая связь: p далеко от пола
    r = G._masked_corr(ra, rb)
    pad = np.full(5000, np.nan)
    p_short = G._pair_perm_p(ra, rb, r, np.random.default_rng(1), 300)
    p_long = G._pair_perm_p(np.r_[pad, ra], np.r_[pad, rb], r, np.random.default_rng(1), 300)
    assert p_short > 0.02, f"sanity: p на полу ({p_short}) — сравнение ничего не докажет"
    assert p_short == p_long, f"p зависит от пустых дней сетки: {p_short} vs {p_long}"


def test_partial_coverage_null_nominal():
    """Под H0 на паре, покрывающей 700 из 3000 дней, type-I нового p номинален (замер 25.09:
    0.025–0.055 на 400 повторах по четырём раскладкам покрытия)."""
    rng = np.random.default_rng(20260925)
    ps = []
    for _ in range(150):
        ra, rb = _pair(rng, 3000, 700, 0.3)
        ps.append(G._pair_perm_p(ra, rb, G._masked_corr(ra, rb), rng, 200))
    t = float((np.array(ps) < 0.05).mean())
    assert t <= 0.10, f"type-I {t:.3f} > 0.10 — нуль пары на частичном покрытии занижен"


def _mkcorr(daily, pairs):
    from scipy.stats import spearmanr
    rows = []
    for a, b in pairs:
        m = daily[[a, b]].dropna()
        r, p = spearmanr(m[a], m[b])
        rows.append({"metric_a": a, "metric_b": b, "spearman_r": float(r), "p_value": float(p),
                     "n": len(m), "significant": p < 0.05, "strong": abs(r) >= 0.3})
    return pd.DataFrame(rows)


def test_min_overlap_days_untested_below_year_and_new_source_passes(monkeypatch):
    monkeypatch.setattr(G._sf, "FROZEN_AT", None)
    rng = np.random.default_rng(5)
    n = 2000
    days = pd.date_range("2020-01-01", periods=n, freq="D")
    hrv = rng.normal(size=n)
    young = np.full(n, np.nan)                       # новый источник: 200 дней
    young[-200:] = hrv[-200:] * 0.8 + rng.normal(scale=0.6, size=200)
    newer = np.full(n, np.nan)                       # новый источник: 400 дней, доля сетки 0.2
    newer[-400:] = hrv[-400:] * 0.8 + rng.normal(scale=0.6, size=400)
    daily = pd.DataFrame({"date": days, "hrv": hrv, "spo2_avg": young, "weight": newer})
    corr = _mkcorr(daily, [("hrv", "spo2_avg"), ("hrv", "weight")])
    cg, _, _ = G.gate_correlations(daily, _EMPTY_LABS, corr, _EMPTY_LAB)
    y = cg[cg.metric_b == "spo2_avg"].iloc[0]
    w = cg[cg.metric_b == "weight"].iloc[0]
    assert int(y.overlap_days) == 200 and pd.isna(y.p_perm), "меньше года — не тестировалась"
    assert cg.attrs["m_family"] == 1, "непроверенная пара не входит в m"
    assert float(w.coverage) < 0.40, "sanity: пара, которую срезал бы старый coverage_min"
    assert bool(w.gate_pass) is True, "год общих дней и сильная связь — новый источник проходит"


@pytest.mark.parametrize("today, expected", [
    (date(2026, 9, 25), "2026-07-12"),    # правило даёт 30.06 < объявленного → объявленный
    (date(2026, 12, 22), "2026-07-12"),   # 30.09 + 84 = 23.12 — ещё рано
    (date(2026, 12, 23), "2026-09-30"),   # первый сдвиг
    (date(2027, 3, 25), "2026-12-31"),
    (date(2027, 6, 23), "2027-03-31"),
])
def test_effective_frozen_at_quarterly_never_back(today, expected):
    assert SF.effective_frozen_at("2026-07-12", 84, today) == expected
    assert SF.effective_frozen_at("2026-07-12", 0, today) == "2026-07-12", "правило выключено"
    assert SF.effective_frozen_at(None, 84, today) is None
