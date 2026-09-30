"""Twin валидации НАПРАВЛЕННОГО лаг-нуля (Группа 2 ч.2 fdr-online).

Что этот twin доказывает и почему устроен так: docs/explanation/lag_directed_validation.md.

Проверяет ПРОДОВЫЙ correlation_gate._strat_pvalue_lagged (strat_hi на паре pred@T ↔ tgt@T+lag,
лаг ВНУТРИ эпохи) на двух вопросах, РАЗДЕЛЬНО по лагам 1/2/3:
  • type-I@0.05 под H0 (нет направленной связи; x⊥y + общий эпоха-дрейф η) по сетке φ×η;
  • мощность под H1 (y[t] ← β·x[t-lag]) — ловит ли реальный внедрённый лаг.
Гоняет ПРОДОВУЮ функцию (не переписку — ловушка exp_family_a_mechanism). БД не нужна (DGP синтетич.).
Запуск: PYTHONPATH=<repo> python3.11 methodology/validation_gate/twin_lag_directed.py

ПРЕДРЕГИСТРАЦИЯ (2026-09-22, BL-TWIN-BAND-1, решение владельца «вариант А»). Прежний критерий —
«INFLATED, если a > 0.05 + 2·√(a(1−a)/reps)» при reps=150 — строил полосу из НАБЛЮДЁННОЙ доли:
мощность против истинных a=0.08 была 0.22, против 0.10 — 0.54. На нём стоял промоут q_lag 23.07.
Новое правило зафиксировано ДО прогона (коммит с этим текстом предшествует артефакту результата):
  • ячейка = (lag, φ, η), сетка 3×4×2 = 24 ячейки, сиды — прежняя формула (не выбираются);
  • в ячейке REPS_NULL=2000 повторов, B=1000; x = число p<0.05 среди n валидных p;
  • инфляция ячейки ⟺ односторонний точный биномиальный p = P(Bin(n, 0.05) ≥ x) < ALPHA_CELL,
    ALPHA_CELL = 0.05/24 (Бонферрони: вероятность ложно исключить хоть один лаг ≤ 0.05);
  • мощность правила против a=0.08 в ячейке ≈ 0.99, против 0.07 ≈ 0.82 (биномиально, n=2000);
  • лаг k ИСКЛЮЧАЕТСЯ из семьи, если инфлирована ХОТЬ ОДНА его ячейка; остальные — допускаются;
  • консервативность (a заметно ниже 0.05) — не исключение, а запись: тест теряет мощность, не честность;
  • мощность под H1 (β=0.4, φ=0.6, η=0.8, 200 повторов) — описательно, в решение не входит.
Граница правила, названная заранее: проверяется калибровка на уровне 0.05, а решения гейта принимаются
в хвосте (BY-пороги ~1e-3) при B=1e5 в проде; калибровку глубокого хвоста этот twin не доказывает.
Структурное исключение лага, а не «чинить» prewhitening'ом (спека §3.3.2). Наивный lagged_correlations
остаётся вне веры.
"""
import json
import sys

import numpy as np
import pandas as pd
from scipy.stats import binom

import correlation_gate as G

_D = pd.date_range("2019-08-01", "2026-01-31", freq="D")
_GRID = pd.DataFrame({"date": _D}).set_index("date").asfreq("D")
EPI = G._epoch_indices(_GRID.index)
N = len(_D)
LAGS = (1, 2, 3)
CSTAR = 0.15
PHIS = (0.0, 0.3, 0.6, 0.9)
ETAS = (0.0, 0.8)
REPS_NULL = 2000                       # PREREG 2026-09-22
ALPHA_CELL = 0.05 / (len(LAGS) * len(PHIS) * len(ETAS))   # Бонферрони по 24 ячейкам


def inflation_p(x: int, n: int, nominal: float = 0.05) -> float:
    """Односторонний точный биномиальный p: P(Bin(n, nominal) ≥ x). Малое — доля ошибок ВЫШЕ номинала."""
    return float(binom.sf(x - 1, n, nominal)) if n > 0 else float("nan")


def cell_verdict(x: int, n: int) -> str:
    """PREREG-правило ячейки. 'inflated' ⟺ inflation_p < ALPHA_CELL; иначе 'ok'. n=0 → 'no_data'
    (ячейка, где тест не вернул ни одного p, — не «чисто», а «не судимо»)."""
    if n <= 0:
        return "no_data"
    return "inflated" if inflation_p(x, n) < ALPHA_CELL else "ok"


def ar1(L, phi, rng):
    x = np.zeros(L)
    s = np.sqrt(1 - phi * phi) if phi else 1.0
    for t in range(1, L):
        x[t] = phi * x[t - 1] + rng.normal() * s
    return x


def tau_of(phi):
    q = phi * phi
    return 0.0 if q <= 0 else -1.0 / np.log(q)


def gen_h0(phi, eta, rng):
    """H0 направленного теста: x⊥y (нет лаговой связи) + общий эпоха-дрейф η. Стратификация обязана
    снять η → номинал; AR(1)-части независимы → нет направленного сигнала."""
    x = np.full(N, np.nan)
    y = np.full(N, np.nan)
    for idx in EPI:
        L = len(idx)
        if L < 2:
            continue
        s = rng.normal() if eta > 0 else 0.0
        x[idx] = eta * s + ar1(L, phi, rng)
        y[idx] = eta * s + ar1(L, phi, rng)
    return x, y


def gen_h1(phi, eta, beta, lag, rng):
    """H1: y[t] ← β·x[t-lag] ВНУТРИ эпохи (x ведёт y на lag) + независимый AR(1)-остаток + η-дрейф."""
    x = np.full(N, np.nan)
    y = np.full(N, np.nan)
    for idx in EPI:
        L = len(idx)
        if L < 2 + lag:
            continue
        s = rng.normal() if eta > 0 else 0.0
        xe = ar1(L, phi, rng)
        ye = np.sqrt(max(0.0, 1 - beta * beta)) * ar1(L, phi, rng)
        ye[lag:] += beta * xe[:-lag]          # y[t] получает β·x[t-lag]
        x[idx] = eta * s + xe
        y[idx] = eta * s + ye
    return x, y


def _p_lagged(x, y, lag, tau, B, rng):
    return G._strat_pvalue_lagged(G._grank(pd.Series(x)), G._grank(pd.Series(y)),
                                  EPI, tau, CSTAR, B, rng, lag)[0]


def run_null(phi, eta, lag, reps=150, B=1000, seed=0):
    rng = np.random.default_rng(seed)
    tau = tau_of(phi)
    ps = [p for _ in range(reps)
          for p in [_p_lagged(*gen_h0(phi, eta, rng), lag, tau, B, rng)] if p == p]
    x = int((np.array(ps) < 0.05).sum()) if ps else 0
    return len(ps), x


def run_power(phi, eta, beta, lag, reps=100, B=1000, seed=0):
    rng = np.random.default_rng(seed)
    tau = tau_of(phi)
    ps = [p for _ in range(reps)
          for p in [_p_lagged(*gen_h1(phi, eta, beta, lag, rng), lag, tau, B, rng)] if p == p]
    return float((np.array(ps) < 0.05).mean()) if ps else float("nan")


def lag_verdicts(cells):
    """cells: [{lag, verdict, ...}] → {lag: 'exclude'|'admit'|'no_data'}. Лаг исключается, если
    инфлирована хоть одна его ячейка; 'no_data' — если хоть одна ячейка не судима (осторожно: не допуск)."""
    out = {}
    for lag in sorted({c["lag"] for c in cells}):
        vs = [c["verdict"] for c in cells if c["lag"] == lag]
        out[lag] = "exclude" if "inflated" in vs else ("no_data" if "no_data" in vs else "admit")
    return out


if __name__ == "__main__":
    out_path = sys.argv[1] if len(sys.argv) > 1 else None
    reps = int(sys.argv[2]) if len(sys.argv) > 2 else REPS_NULL
    print(f"=== TYPE-I @0.05 под H0, PREREG: reps={reps}, alpha_cell={ALPHA_CELL:.5f} ===", flush=True)
    print("lag  phi  eta     n     x   type-I   p_infl    verdict", flush=True)
    cells = []
    for lag in LAGS:
        for phi in PHIS:
            for eta in ETAS:
                n, x = run_null(phi, eta, lag, reps=reps,
                                seed=lag * 1000 + int(phi * 100) + int(eta * 10) + 1)
                pi = inflation_p(x, n)
                v = cell_verdict(x, n)
                cells.append({"lag": lag, "phi": phi, "eta": eta, "n": n, "x": x,
                              "type_i": (x / n) if n else None, "p_inflation": pi, "verdict": v})
                print("%d    %.1f  %.1f  %5d %5d   %.4f   %.2e   %s" % (lag, phi, eta, n, x,
                      (x / n) if n else float("nan"), pi, v), flush=True)
    power = {}
    print("\n=== МОЩНОСТЬ @0.05 под H1 (β=0.4, φ=0.6, η=0.8), описательно ===", flush=True)
    for lag in LAGS:
        power[lag] = run_power(0.6, 0.8, 0.4, lag, reps=200, seed=lag * 7 + 3)
        print("%d    %.3f" % (lag, power[lag]), flush=True)
    verdicts = lag_verdicts(cells)
    print("\n=== ВЕРДИКТ ПО ЛАГАМ (PREREG) ===", flush=True)
    for lag, v in verdicts.items():
        print(f"  lag {lag}: {v}", flush=True)
    if out_path:
        import datetime as _dt
        import subprocess as _sp
        try:
            sha = _sp.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
        except Exception as e:  # noqa: BLE001 — sha — провенанс, не условие счёта
            print(f"  ⚠️ git sha не получен: {e}", flush=True)
            sha = None
        json.dump({"prereg": {"reps_null": reps, "alpha_cell": ALPHA_CELL, "nominal": 0.05,
                              "rule": "exclude lag if any cell has P(Bin(n,0.05)>=x) < alpha_cell"},
                   "ran_at": _dt.datetime.now().isoformat(timespec="seconds"), "head_sha": sha,
                   "cells": cells, "power_h1": power,
                   "verdicts": {str(k): v for k, v in verdicts.items()}},
                  open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"артефакт: {out_path}", flush=True)
    print("DONE", flush=True)
