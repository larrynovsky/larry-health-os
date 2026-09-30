"""Триггер BL-MCGAP-FLIP-1 (решение владельца 23.09): семья пропускает пары на шумовом полу.

Предикат чистый — `correlation_gate.mc_resolution_fragile(families)`; его зовут ночной
check_mc_gap и еженедельная задача владельца. Здесь — его поведение на независимо сконструированных артефактах.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import correlation_gate as G  # noqa: E402


def _fam(passed, B, m):
    return {"evaluated": True, "passed": passed, "B": B, "m": m, "k_star": passed, "flagged": []}


def test_synthetic_noise_floor_triggers():
    """Синтетическая семья с прошедшими парами на шумовом полу должна будить."""
    out = G.mc_resolution_fragile({"D": _fam(5, 1000, 52), "A": _fam(9, 100000, 52), "q_lag": None})
    assert len(out) == 1 and out[0].startswith("D:")


def test_synthetic_resolved_families_are_quiet():
    """Нет прошедших пар на шумовом полу: триггер молчит."""
    fams = {"D": _fam(0, 1000, 52), "A": _fam(9, 100000, 52), "q_lag": _fam(0, 100000, 24)}
    assert G.mc_resolution_fragile(fams) == []


def test_граница_шага_by():
    """Пол ниже шага — пара может пройти одна, триггера нет; выше — есть. B=3151 — граница при m=66."""
    assert G.mc_resolution_fragile({"D": _fam(1, 5000, 66)}) == []
    assert G.mc_resolution_fragile({"D": _fam(1, 3000, 66)}) != []


def test_мусор_в_артефакте_не_роняет():
    assert G.mc_resolution_fragile({"D": ["мусор"], "A": None}) == []
    assert G.mc_resolution_fragile({}) == [] and G.mc_resolution_fragile(None) == []


def test_точный_перебор_не_будит_триггер():
    """exact=True выключает Монте-Карло триггер независимо от старого B. Тот же синтетический вход без exact обязан будить."""
    fam = dict(_fam(3, 1000, 156), exact=True)
    assert G.mc_resolution_fragile({"D": fam}) == []
    assert G.mc_resolution_fragile({"D": _fam(3, 1000, 156)}) != []   # позитивный контроль


def test_точный_p_детерминирован_и_без_rng():
    """p при b_perm=None: перебор ВСЕХ годных сдвигов, rng не нужен, ответ один на один вход.
    Пол — 1/(1+годных): при идеальной связи ни один сдвиг не догоняет наблюдённое r."""
    import numpy as np
    n = 400
    t = np.arange(n, dtype=float)
    a = np.sin(t / 7.0) + np.random.default_rng(3).normal(0, 0.1, n)
    ra, rb = a - a.mean(), a - a.mean()
    r = G._masked_corr(ra, rb)
    p1 = G._pair_perm_p(ra, rb, r, None, None)
    p2 = G._pair_perm_p(ra, rb, r, None, None)
    assert p1 == p2
    годных = sum(1 for k in range(G.GUARD, n - G.GUARD)
                 if G._masked_corr(ra, np.roll(rb, k)) is not None)
    assert p1 <= 2.0 / (1 + годных)


def test_структурная_пара_не_попадает_в_mc_зазор_семьи_A():
    """28.09 (решение владельца, вариант А): hrv×recovery_high_min — арифметика Oura. Структурные
    пары в веру не доходят, MC-зазор по ним — ложный вызов. Мутация: вернуть sg целиком → пара
    остаётся в выборке и тест краснеет."""
    import pandas as pd
    df = pd.DataFrame({"metric_a": ["hrv", "sleep_total"], "metric_b": ["recovery_high_min", "hrv"],
                       "a_lever": [True, True], "p_strat": [0.0054, 0.001]})
    kept = G._non_structural(df)
    assert list(zip(kept["metric_a"], kept["metric_b"])) == [("sleep_total", "hrv")]
