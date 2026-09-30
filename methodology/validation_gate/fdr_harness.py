#!/usr/bin/env python3
"""
Two-stage FDR null-harness for the N=1 validation gate.

Purpose
-------
Resolve, by simulation rather than decree, three disputed questions about the
multiplicity correction in `correlation_gate.py`:

  Q1  BH vs BY under sign-indefinite cross-dependence.
  Q2  per-run vs registry FDR (addressed in the doc; harness covers within-run).
  Q3  detrend / permutation validity + the *resolution* of permutation p-values.

Design (mirrors the two-stage split argued in the verdict):

  Stage A  ORACLE engine: continuous, effective-df-corrected analytic p-values.
           Isolates the PURE BH-vs-BY multiplicity penalty, free of discreteness.

  Stage B  SHIFT engine: exact circular-shift permutation p-values from full
           enumeration of all T cyclic alignments -> exact floor 1/T. Exposes the
           DISCRETENESS collapse and the wall  T >= m * H_m / q  (BY, rank-1).

Generator: V variables, all C(V,2) pairs tested (a shared variable sits in V-1
pairs -> exactly the dependence the spec worries about). Each series is AR(1)
with parameter phi. Alternatives inject genuine contemporaneous correlation rho
into n_true disjoint pairs.

The AR(1) parameter phi controls serial dependence; series length controls resolution.
Public demonstrations require an independently chosen parameter grid and duration.

Not a release-grade run (trials are modest for wall-clock). The doc specifies
the 50k all-null protocol for release qualification; this is the mechanism proof.
"""

import numpy as np
from numpy.fft import fft, ifft
from scipy import stats

# -----------------------------------------------------------------------------
# core helpers
# -----------------------------------------------------------------------------

def H(m):
    return float(np.sum(1.0 / np.arange(1, m + 1)))

from scipy.signal import lfilter

def ar1(T, phi, rng, innov=None):
    """One AR(1) path, unit stationary variance (vectorized, short burn-in)."""
    B = 200
    if innov is None:
        innov = rng.standard_normal(T + B)
    else:
        # caller supplied length-T innovations; prepend burn-in
        innov = np.concatenate([rng.standard_normal(B), innov])
    s = np.sqrt(1 - phi**2)
    x = lfilter([s], [1.0, -phi], innov)
    return x[B:]

def gen_variables(V, T, phi, rng, true_pairs=None, rho=0.0):
    """
    Return array (V, T). true_pairs: list of (i,j) disjoint pairs made correlated
    at contemporaneous rho (same-phi AR(1) with correlated innovations -> corr=rho).
    """
    X = np.empty((V, T))
    done = set()
    if true_pairs:
        for (i, j) in true_pairs:
            zi = rng.standard_normal(T)
            zj = rho * zi + np.sqrt(1 - rho**2) * rng.standard_normal(T)
            X[i] = ar1(T, phi, rng, innov=zi)
            X[j] = ar1(T, phi, rng, innov=zj)
            done.add(i); done.add(j)
    for k in range(V):
        if k not in done:
            X[k] = ar1(T, phi, rng)
    return X

def pairs_of(V):
    return [(i, j) for i in range(V) for j in range(i+1, V)]

# -----------------------------------------------------------------------------
# Stage A: continuous oracle p-value (effective-df corrected)
# -----------------------------------------------------------------------------

def p_oracle(x, y, phi):
    """Pearson r with autocorrelation-corrected effective df (two AR(1), same phi)."""
    r = np.corrcoef(x, y)[0, 1]
    T = len(x)
    vif = (1 + phi**2) / (1 - phi**2)          # correlation-df inflation
    n_eff = max(4.0, T / vif)
    if abs(r) >= 1: r = np.sign(r) * (1 - 1e-9)
    t = r * np.sqrt((n_eff - 2) / (1 - r**2))
    p = 2 * stats.t.sf(abs(t), df=n_eff - 2)
    return min(1.0, p)

# -----------------------------------------------------------------------------
# Stage B: exact circular-shift permutation p-value via FFT (all T shifts)
# -----------------------------------------------------------------------------

def p_shift(x, y):
    """
    EXACT circular-shift permutation p for |corr|: full enumeration of all T
    unique cyclic alignments. p = #{tau: |r_tau| >= |r_obs|} / T  (observed
    always counts), so the floor is exactly 1/T.

    NB: the (b+1)/(B+1) Phipson-Smyth estimator is the MONTE-CARLO case, used
    only when B < T random shifts are sampled; drawing more B does NOT create
    new unique cyclic alignments (there are only T), so it cannot lower 1/T.
    """
    T = len(x)
    xz = (x - x.mean()); yz = (y - y.mean())
    sx = np.sqrt((xz**2).sum()); sy = np.sqrt((yz**2).sum())
    if sx == 0 or sy == 0:
        return 1.0
    # EXACT circular cross-correlation at all T lags: length-T FFT, NO padding
    # (zero-padding would give linear/aperiodic correlation, which is wrong).
    cc = ifft(fft(xz) * np.conj(fft(yz))).real   # sum_t x_t y_{(t+tau) mod T}
    r_all = cc / (sx * sy)
    r_obs = r_all[0]
    b = int(np.sum(np.abs(r_all) >= abs(r_obs) - 1e-12))   # includes observed
    return (b) / (T)          # = (#>=obs)/T ; observed always counts -> >=1/T

# -----------------------------------------------------------------------------
# multiplicity procedures
# -----------------------------------------------------------------------------

def bh_reject(pvals, q):
    m = len(pvals); order = np.argsort(pvals); ps = np.array(pvals)[order]
    thresh = q * np.arange(1, m+1) / m
    passed = np.where(ps <= thresh)[0]
    if len(passed) == 0: return np.zeros(m, bool)
    kmax = passed.max(); rej = np.zeros(m, bool); rej[order[:kmax+1]] = True
    return rej

def by_reject(pvals, q):
    m = len(pvals)
    return bh_reject(pvals, q / H(m))

# -----------------------------------------------------------------------------
# one trial
# -----------------------------------------------------------------------------

def one_trial(V, T, phi, rng, n_true, rho, q, engine):
    prs = pairs_of(V)
    # disjoint true pairs
    true_pairs = []
    used = set()
    if n_true > 0:
        cand = prs[:]
        rng.shuffle(cand)
        for (i, j) in cand:
            if i not in used and j not in used:
                true_pairs.append((i, j)); used.add(i); used.add(j)
                if len(true_pairs) == n_true: break
    true_set = set(true_pairs)
    X = gen_variables(V, T, phi, rng, true_pairs, rho)
    pv = np.empty(len(prs))
    for idx, (i, j) in enumerate(prs):
        if engine == 'oracle':
            pv[idx] = p_oracle(X[i], X[j], phi)
        else:
            pv[idx] = p_shift(X[i], X[j])
    out = {}
    for name, rej_fn in (('BH', bh_reject), ('BY', by_reject)):
        rej = rej_fn(pv, q)
        R = int(rej.sum())
        V_false = sum(1 for k,(i,j) in enumerate(prs) if rej[k] and (i,j) not in true_set)
        TP = sum(1 for k,(i,j) in enumerate(prs) if rej[k] and (i,j) in true_set)
        fdp = V_false / max(R, 1)
        power = TP / max(len(true_set), 1) if true_set else np.nan
        out[name] = (fdp, power, V_false >= 1, R)
    return out

def run_cell(V, T, phi, n_true, rho, q, engine, trials, seed):
    rng = np.random.default_rng(seed)
    acc = {'BH': [], 'BY': []}
    for _ in range(trials):
        o = one_trial(V, T, phi, rng, n_true, rho, q, engine)
        for k in ('BH','BY'):
            acc[k].append(o[k])
    res = {}
    for k in ('BH','BY'):
        a = np.array(acc[k], float)
        res[k] = dict(fdr=a[:,0].mean(), power=np.nanmean(a[:,1]),
                      p_any_false=a[:,2].mean(), R=a[:,3].mean())
    return res

def wall(m, q, proc):
    Hm = H(m)
    return int(np.ceil(m/q)) if proc=='BH' else int(np.ceil(m*Hm/q))
