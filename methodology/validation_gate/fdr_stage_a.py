"""Stage A oracle (§10, ступень 1): истинно-oracle p-симулятор для ЧИСТОГО штрафа контроллера BH vs BY.

p-value считаются из ИЗВЕСТНОГО нулевого распределения генератора:
  z ~ MVN(mean, Σ);  под нулём mean=0 → двусторонний p = 2·Φ̄(|z|) ТОЧНО Uniform(0,1) (marginal
  oracle), а зависимость между тестами задаётся Σ (Gaussian copula). Альтернативы — сдвиг среднего.

Это изолирует чистый штраф множественной поправки без ошибки p-engine — в отличие от
fdr_harness.p_oracle, который использует ОЦЕНЁННЫЙ effective-df (§8 вердикта: не oracle).
Позитивные контроли — tests/unit/test_fdr_stage_a.py.
"""
from __future__ import annotations

import numpy as np
from scipy import stats

from fdr_harness import bh_reject, by_reject   # тот же контроллер, что в mechanism-proof harness

DEPENDENCE = ("independent", "prds_positive", "mixed_sign", "common_latent")


def _nearest_pd(S):
    """Ближайшая корреляционная PD-матрица (клип собств. значений) — для mixed-sign Σ."""
    w, V = np.linalg.eigh(S)
    w = np.clip(w, 1e-8, None)
    S2 = (V * w) @ V.T
    d = np.sqrt(np.diag(S2))
    return S2 / np.outer(d, d)


def sigma(m: int, dependence: str, rho: float) -> np.ndarray:
    """Копула-ковариация тестов для сценария зависимости."""
    if dependence == "independent":
        return np.eye(m)
    if dependence == "prds_positive":                     # положительная эквикорреляция → PRDS
        return (1 - rho) * np.eye(m) + rho * np.ones((m, m))
    if dependence == "common_latent":                     # общий скрытый драйвер (факторная)
        lam = np.sqrt(rho) * np.ones((m, 1))
        return lam @ lam.T + (1 - rho) * np.eye(m)
    if dependence == "mixed_sign":                        # смешанные знаки → PRDS не гарантирован
        S = np.eye(m)
        half = m // 2
        for i in range(m):
            for j in range(i + 1, m):
                same = (i < half) == (j < half)
                S[i, j] = S[j, i] = (rho if same else -rho)
        return _nearest_pd(S)
    raise ValueError(f"неизвестная зависимость: {dependence}")


def oracle_pvalues(m, m1, dependence, rho, effect, rng):
    """(p, truth). Под нулём p ТОЧНО Uniform; зависимость = Σ; m1 альтернатив сдвинуты на effect."""
    L = np.linalg.cholesky(sigma(m, dependence, rho))
    z = L @ rng.standard_normal(m)
    truth = np.zeros(m, bool)
    if m1 > 0:
        idx = rng.choice(m, size=m1, replace=False)
        z[idx] += effect
        truth[idx] = True
    p = 2.0 * stats.norm.sf(np.abs(z))
    return p, truth


def _metrics(rej, truth, m1):
    R = int(rej.sum()); V = int((rej & ~truth).sum()); TP = int((rej & truth).sum())
    return (V / R if R else 0.0, (TP / m1 if m1 else np.nan), V >= 1)


def run_oracle_cell(m, m1, dependence, rho, effect, q, trials, seed):
    """Прогон ячейки: FDR/power/p_any_false + односторонняя MC-верхняя граница (1.96·se) для FDR."""
    rng = np.random.default_rng(seed)
    acc = {"BH": [], "BY": []}
    for _ in range(trials):
        p, truth = oracle_pvalues(m, m1, dependence, rho, effect, rng)
        for name, fn in (("BH", bh_reject), ("BY", by_reject)):
            acc[name].append(_metrics(fn(p, q), truth, m1))
    res = {}
    for name in ("BH", "BY"):
        a = np.array(acc[name], float)
        fdr = float(a[:, 0].mean())
        se = float(a[:, 0].std(ddof=1) / np.sqrt(trials))
        power = float(np.nanmean(a[:, 1])) if m1 > 0 else float("nan")
        res[name] = dict(fdr=fdr, fdr_hi=fdr + 1.96 * se, power=power, p_any_false=float(a[:, 2].mean()))
    return res
