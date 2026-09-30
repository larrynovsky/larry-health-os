"""Stage B — сценарный харнесс: синтетика (stage_b_twin) через РЕАЛЬНЫЙ p-движок
gate_correlations. Меряет FDR/power vs известную правду. Параллельно (multiprocessing).

Сценарии (§3 дизайна):
  global_null       — независимые ряды (S=I) + baseline-ACF, БЕЗ эпох. FDR должен ≤ q.
  changepoint_broken— 2 НЕЗАВИСИМЫХ метрики + общий эпоха-сдвиг среднего → «прыгают» вместе →
                      циркулярный сдвиг может ложно флагнуть. harness ОБЯЗАН показать FDR≫q+δ (сердце).
  partial_power     — независимый фон + посаженные истинные пары (rho). Меряет power и FDR.
"""
from __future__ import annotations
import os, sys, math
import numpy as np, pandas as pd
from multiprocessing import Pool

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "..")))   # repo root
import stage_b_twin as tw
import longitudinal_analysis as la
import correlation_gate as cg

_EMPTY_LABS = pd.DataFrame(columns=["date", "test_name", "value", "unit"])
_EMPTY_LC = pd.DataFrame(columns=["lab","metric","label","spearman_r","p_value","n","significant","strong"])


def _epoch_profile(T, amp):
    """Общая кусочно-линейная нестационарная траектория; amp задаёт масштаб в σ."""
    fr = [0.0, 0.34, 0.37, 0.40, 0.46, 0.47, 0.48, 0.62, 0.70, 0.71, 0.80, 1.0]
    lvl = [0.0, 0.0, -0.3, -1.0, -0.6, -0.7, -0.7, -0.4, -0.2, -0.5, -0.8, -0.8]
    p = np.zeros(T)
    for s in range(11):
        a, b = int(fr[s] * T), int(fr[s + 1] * T)
        if b > a:
            p[a:b] = np.linspace(lvl[s], lvl[s + 1], b - a)
    return amp * p


def _rand_profile(T, amp, rng):
    """Случайный нестационарный профиль с независимыми уровнями для family-wide теста."""
    b = np.linspace(0, T, 12).astype(int); lv = rng.normal(0, 1, 12); p = np.zeros(T)
    for s in range(11):
        if b[s+1] > b[s]:
            p[b[s]:b[s+1]] = np.linspace(lv[s], lv[s+1], b[s+1] - b[s])
    return amp * p


_G = {}   # core, выставляется Pool-initializer'ом ОДИН раз на воркер (калибровка в родителе)


def _init(order, phis, S):
    _G["order"], _G["phis"], _G["S"] = order, phis, S


def _one_trial(args):
    spec, seed = args
    order, phis, S = _G["order"], _G["phis"], _G["S"]
    T = spec["T"]   # длина ряда: из манифеста тенанта (run_scenario), не литерал
    kind = spec["kind"]
    if kind == "global_null":
        df = tw.generate(order, phis, S, T, seed, true_pairs=[], rho=0.0)
        true = set()
    elif kind == "changepoint_broken":
        i, j = spec["pair"]
        ep = [(i, 0.5, 1.0, spec["shift"], 1.0), (j, 0.5, 1.0, spec["shift"], 1.0)]
        df = tw.generate(order, phis, S, T, seed, true_pairs=[], rho=0.0, epochs=ep)
        true = set()   # НЕ истинно коррелированы — только эпоха двигает оба
    elif kind == "trend_broken":
        # 2 НЕЗАВИСИМЫХ метрики + общий ЛИНЕЙНЫЙ тренд (Yuan-Shou: wrap-around ломает circular-shift).
        i, j = spec["pair"]
        df = tw.generate(order, phis, S, T, seed, true_pairs=[], rho=0.0)
        ramp = np.linspace(0.0, spec["slope"], T)
        df[order[i]] = df[order[i]].to_numpy() + ramp
        df[order[j]] = df[order[j]].to_numpy() + ramp
        true = set()
    elif kind == "variance_shifts":
        i, j = spec["pair"]
        ep = [(i, 0.5, 1.0, 0.0, spec["vscale"]), (j, 0.5, 1.0, 0.0, spec["vscale"])]
        df = tw.generate(order, phis, S, T, seed, true_pairs=[], rho=0.0, epochs=ep)
        true = set()
    elif kind == "overlapping":
        i, j = spec["pair"]
        df = tw.generate(order, phis, S, T, seed, true_pairs=[], rho=0.0)
        for idx in (i, j):
            df[order[idx]] = df[order[idx]].rolling(spec.get("win", 7), min_periods=1, center=True).mean()
        true = set()
    elif kind == "combined_nonstat":
        i, j = spec["pair"]
        df = tw.generate(order, phis, S, T, seed, true_pairs=[], rho=0.0)
        prof = _epoch_profile(T, spec.get("amp", 2.5))
        for idx in (i, j):
            df[order[idx]] = df[order[idx]].to_numpy() + prof
        true = set()
    elif kind == "all_nonstat_indep":
        # ВСЕ метрики нестационарны НЕЗАВИСИМО (family-wide null под нестационарностью).
        df = tw.generate(order, phis, S, T, seed, true_pairs=[], rho=0.0)
        rng2 = np.random.default_rng(seed + 555)
        amps = spec.get("amps")   # per-metric (order-aligned); иначе единый amp
        for idx in range(len(order)):
            a = amps[idx] if amps else spec.get("amp", 3.0)
            df[order[idx]] = df[order[idx]].to_numpy() + _rand_profile(T, a, rng2)
        true = set()   # ни одна пара не коррелирована истинно
    elif kind == "partial_power":
        tp = spec["true_pairs"]
        df = tw.generate(order, phis, S, T, seed, true_pairs=tp, rho=spec["rho"])
        true = set(tuple(sorted((order[a], order[b]))) for a, b in tp)
    else:
        raise ValueError(kind)
    corr_all = la.correlation_matrix(df)
    c, _, _ = cg.gate_correlations(df, _EMPTY_LABS, corr_all, _EMPTY_LC, seed=seed)
    passed = set(tuple(sorted((r.metric_a, r.metric_b))) for r in c[c.gate_pass].itertuples())
    R = len(passed); Vf = len(passed - true); TP = len(passed & true)
    fdp = Vf / R if R > 0 else 0.0
    return fdp, R, Vf, TP, len(true)


def _baseline_len(manifest) -> int:
    """Длина первой (baseline) эпохи тенанта в днях, включительно — из его манифеста.
    Раньше стояла литералом (длина эпохи владельца); манифест — приватные данные установки."""
    import datetime as _dt
    import yaml
    a, b = yaml.safe_load(open(manifest))["epochs"][0]["range"]
    return (_dt.date.fromisoformat(str(b)) - _dt.date.fromisoformat(str(a))).days + 1


def run_scenario(manifest, spec, N, ncores=12, base_seed=1000):
    spec = {"T": _baseline_len(manifest), **spec}
    order, C, acf = tw.load_core(manifest)
    phis = tw.build_phis(order, acf)
    S, _ = tw.calibrate_innov(order, C, phis, seed=0)   # калибровка ОДИН раз
    args = [(spec, base_seed + k) for k in range(N)]
    with Pool(ncores, initializer=_init, initargs=(order, phis, S)) as p:
        res = p.map(_one_trial, args)
    fdp = np.array([r[0] for r in res]); R = np.array([r[1] for r in res])
    Vf = np.array([r[2] for r in res]); TP = np.array([r[3] for r in res])
    ntrue = res[0][4]
    fdr = float(fdp.mean())
    se = float(fdp.std(ddof=1) / math.sqrt(N))
    fdr_hi = fdr + 1.645 * se                      # односторон. 95% верх (α_MC=0.05)
    power = float((TP / ntrue).mean()) if ntrue > 0 else None
    return {"scenario": spec["kind"], "N": N, "FDR": round(fdr, 4),
            "FDR_upper95": round(fdr_hi, 4), "any_reject_rate": round(float((R > 0).mean()), 4),
            "mean_R": round(float(R.mean()), 2), "power": round(power, 4) if power is not None else None}
