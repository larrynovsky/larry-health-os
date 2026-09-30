"""Контроли стратифицированного A-гейта (_gate_daily_stratified), Фаза 2 fdr-online.

Канон = strat_hi (сырые per-эпоха кросс-произведения, равный вес эпох, baseline-тяжёлый).
Независимая синтетика проверяет формулу и статистические свойства без личной БД.

Позит.контроли:
  - scheme_raw: сигнал в baseline детектится, сигнал в короткой эпохе — НЕТ (отличает raw от equal);
  - discriminator: эпоха-дрейф (η≈1, без внутриэпоховой связи) НЕ значим (семья A снимает режим);
  - config_guard: пол strat-p достижим для BY (B≠1000);
  - source_guard: _gate_daily (D) не стал стратифицированным + стратиф. НЕ подключён (проводка=фаза C).
"""
from __future__ import annotations

import pathlib

import pytest
import re

import numpy as np
import pandas as pd
import yaml

import correlation_gate as G


def _dates():
    return pd.date_range("2019-08-01", "2026-01-31", freq="D")


def _gr(v):
    return G._grank(pd.Series(v))


def test_tau_pair_bartlett(monkeypatch):
    """Независимые коэффициенты проверяют формулу, симметрию и неположительное произведение."""
    monkeypatch.setattr(G, "BASELINE_ACF", {"fixture_q": 0.4, "fixture_r": 0.5,
                                         "fixture_s": -0.3})
    assert G._tau_pair("fixture_q", "fixture_r") == pytest.approx(-1.0 / np.log(0.2))
    assert G._tau_pair("fixture_s", "fixture_r") == 0.0
    assert G._tau_pair("fixture_r", "fixture_q") == G._tau_pair("fixture_q", "fixture_r")
    assert G._tau_pair("nonexistent", "fixture_r") == 0.0


def test_epochs_sourced_from_manifest():
    """Эпохи читаются из data_manifest (frozen), НЕ хардкодятся в correlation_gate."""
    man = yaml.safe_load((pathlib.Path(G.__file__).resolve().parent / "methodology"
                          / "validation_gate" / "data_manifest.yaml").read_text(encoding="utf-8"))
    expect = [tuple(e["range"]) for e in man["epochs"]]
    assert list(G.EPOCHS) == expect, "эпохи должны идти из data_manifest, не из литерала"


def test_config_guard_by_reachable_and_not_1000():
    """Пол strat-p = 1/(B+1) должен быть строго < BY-rank1 порога для реалистичного m;
    стратиф. B НЕ наследует b_perm=1000 (иначе BY недостижим — data_manifest.discreteness)."""
    B = G.B_STRAT
    assert B != G.B_PERM and B >= 3200, f"стратиф. B={B} не должен быть 1000 (BY недостижим)"
    floor = 1.0 / (B + 1)
    for m in (10, 45, 65):
        by_rank1 = G.FDR_Q / (G._H(m) * m)          # порог BY на ранге 1 = q/(H_m·m)
        assert floor < by_rank1, f"пол {floor:.1e} ≥ BY-rank1 {by_rank1:.1e} при m={m}"


@pytest.mark.owner_data
def test_scheme_raw_baseline_heavy():
    """Позит.контроль СЫРОЙ (baseline-тяжёлой) схемы: сигнал в baseline → детект + высокая
    baseline-доля вклада. Дискриминатор канона: equal-схема (row3 спеки, норм. w_e=1) дала бы
    baseline ~10%, inv-var (row2, спека-«первичка») ~55%; сырой канон концентрирует. NB: сырая
    схема НЕ слепа к коротким эпохам — сильный локальный сигнал тоже детектится (это by design)."""
    rng = np.random.default_rng(0)
    d = _dates()
    n = len(d)
    grid = pd.DataFrame({"date": d}).set_index("date").asfreq("D")
    epi = G._epoch_indices(grid.index)
    base_idx = epi[0]
    x = rng.normal(size=n)
    y = rng.normal(size=n)
    y[base_idx] = x[base_idx] * 0.6 + rng.normal(scale=0.6, size=len(base_idx))
    p, b, _ = G._strat_pvalue(_gr(x), _gr(y), epi, 0.0, 0.15, 20000, np.random.default_rng(1))
    assert p < 0.01, f"baseline-сигнал обязан детектиться сырой схемой: p={p}"
    assert b > 60.0, f"baseline-доля вклада высокая при raw (equal дал бы ~10%): {b:.1f}%"


@pytest.mark.owner_data
def test_discriminator_epoch_drift_not_a_lever():
    """Позит.контроль двух семей: эпоха-средние co-двигаются (η≈1), внутри эпох независимо →
    strat НЕ значим (семья A снимает режимный отклик, в отличие от глобального D)."""
    rng = np.random.default_rng(5)
    d = _dates()
    n = len(d)
    grid = pd.DataFrame({"date": d}).set_index("date").asfreq("D")
    epi = G._epoch_indices(grid.index)
    x = np.full(n, np.nan)
    y = np.full(n, np.nan)
    for idx in epi:
        if len(idx) < 16:
            continue
        s = rng.normal()
        x[idx] = s + rng.normal(size=len(idx))
        y[idx] = s + rng.normal(size=len(idx))
    p, _, _ = G._strat_pvalue(_gr(x), _gr(y), epi, 0.0, 0.15, 20000, np.random.default_rng(6))
    assert p > 0.05, f"эпоха-дрейф (η≈1, без внутриэпоховой связи) НЕ должен быть A-рычагом: p={p}"


def test_gate_stratified_derived_excluded_and_columns():
    """_gate_daily_stratified добавляет колонки; derived-пара (sleep_score) исключена (NaN)."""
    rng = np.random.default_rng(3)
    d = _dates()
    n = len(d)
    daily = pd.DataFrame({"date": d, "hrv": rng.normal(size=n),
                          "sleep_deep": rng.normal(size=n), "sleep_score": rng.normal(size=n)})
    corr = pd.DataFrame([{"metric_a": a, "metric_b": b}
                         for a, b in [("hrv", "sleep_deep"), ("hrv", "sleep_score")]])
    out = G._gate_daily_stratified(daily, corr, seed=0)
    for c in ("p_strat", "baseline_share", "strat_epochs", "derived", "a_lever", "verdict_family"):
        assert c in out.columns, f"нет колонки {c}"
    ss = out[(out.metric_a == "hrv") & (out.metric_b == "sleep_score")].iloc[0]
    assert bool(ss.derived) is True, "sleep_score обязан быть derived"
    assert ss.p_strat != ss.p_strat, "derived-пара исключена (p_strat=NaN)"
    assert bool(ss.a_lever) is False


def test_source_guard_d_unchanged_and_a_owner_gated():
    """_gate_daily (D) остаётся ГЛОБАЛЬНЫМ (не стратифицированным). Фаза C (2026-07-22): стратиф. A
    ПОДКЛЮЧЕНА к gate_correlations, но строго под owner-флагом enable_stratified (эпохи владельца)."""
    src = pathlib.Path(G.__file__).read_text(encoding="utf-8")

    def _slice(fn):
        m = re.search(r"\ndef " + fn + r"\(.*?(?=\ndef )", src, re.S)
        return m.group(0) if m else ""

    d = _slice("_gate_daily")
    assert d, "не найден _gate_daily"
    assert "EPOCHS" not in d and "strat" not in d, "_gate_daily (D) не должен стать стратифицированным"
    gc = re.search(r"\ndef gate_correlations\(.*", src, re.S).group(0)
    assert "_gate_daily_stratified" in gc, "Фаза C: стратиф. A должна быть подключена в gate_correlations"
    assert "if enable_stratified" in gc, "вызов стратиф. A обязан быть под owner-флагом enable_stratified"
