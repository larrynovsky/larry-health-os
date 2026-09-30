"""Twin-П7: калибровка ПРОДОВОЙ correlation_gate._strat_pvalue под H0 (нет внутриэпоховой связи).

Проверяет type-I@0.05 + равномерность p по сетке φ (→ спектр τ/L) × эпоха-дрейф η.
Гоняет продовую функцию (не переписку). Запуск: PYTHONPATH=<repo> python3.11 twin_strat_a_p7.py
(нужен только импорт correlation_gate; БД не требуется — DGP синтетический).

РЕЗУЛЬТАТ 2026-07-22 (принят владельцем, Фаза 2 fdr-online; см. null_gate_spec §3.5.1):
  type-I держит номинал 0.04–0.08 по реалистичной сетке (φ≥0.3, любой η; φ=0,η=0=0.053).
  ЕДИНСТВЕННОЕ отклонение — нефизичный угол φ=0 (нулевая суточная автокорр.) + η=0.8:
  type-I≈0.10 (2×, 150 реплик). Гипотеза: утечка через глобальный ранг при эпоха-дрейфе,
  τ/L-гейт её не ловит (φ=0 → τ=0 → s_e=0). Реальные метрики φ>0 → флагманы вне угла.
  Принято с фиксацией ограничения; митигация — отложенный follow-up.
"""
import numpy as np
import pandas as pd
from scipy.stats import kstest

import correlation_gate as G

_D = pd.date_range("2019-08-01", "2026-01-31", freq="D")
_GRID = pd.DataFrame({"date": _D}).set_index("date").asfreq("D")
EPI = G._epoch_indices(_GRID.index)
N = len(_D)


def ar1(L, phi, rng):
    x = np.zeros(L)
    s = np.sqrt(1 - phi * phi) if phi else 1.0
    for t in range(1, L):
        x[t] = phi * x[t - 1] + rng.normal() * s
    return x


def gen_h0(phi, eta, rng):
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


def tau_of(phi):
    q = phi * phi
    return 0.0 if q <= 0 else -1.0 / np.log(q)


def run(phi, eta, reps=150, B=1000, seed=0):
    rng = np.random.default_rng(seed)
    tau = tau_of(phi)
    ps = []
    for _ in range(reps):
        x, y = gen_h0(phi, eta, rng)
        p, _, _ = G._strat_pvalue(G._grank(pd.Series(x)), G._grank(pd.Series(y)),
                                  EPI, tau, 0.15, B, rng)
        if p == p:
            ps.append(p)
    ps = np.array(ps)
    return len(ps), float((ps < 0.05).mean()), float(kstest(ps, "uniform").pvalue), tau


if __name__ == "__main__":
    print("phi  eta   tau     n    type-I@.05  KS-unif-p")
    for phi in (0.0, 0.3, 0.6, 0.9):
        for eta in (0.0, 0.8):
            nn, a, ks, tau = run(phi, eta, seed=int(phi * 100) + int(eta * 10) + 1)
            flag = "" if a <= 0.085 else "   <-- INFLATED (accepted: unphysical corner)"
            print("%.1f  %.1f  %5.2f  %4d   %.3f       %.3f%s" % (phi, eta, tau, nn, a, ks, flag))
    print("DONE")
