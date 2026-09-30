"""Stage B digital-twin — генератор дневных мульти-метрик + self-check.

Дизайн: methodology/validation_gate/stage_b_design.md (3 решения владельца по развилкам).
Стационарное ядро из manifest v2 `twin_baseline_core` (baseline-ACF + кросс 11×11).

Слои (§1 дизайна): (a) инновации ~ N(0, Σ_innov); (b) пер-метрика AR(3) под baseline acf1-3
(Yule-Walker); (c) обратная калибровка Σ_innov под целевую выходную кросс (AR разный → искажает);
(d) эпохи (сдвиги среднего/дисперсии); (e) missingness (маска по cov_frac).

Self-check = ГЕЙТ: посадил ACF/кросс/changepoint → восстановил в пределах допуска. Иначе Stage B
недостоверен (RST: тестируем против неправильного мира).
"""
from __future__ import annotations
import numpy as np
import yaml


def load_core(manifest_path: str):
    d = yaml.safe_load(open(manifest_path))
    c = d["twin_baseline_core"]
    order = list(c["cross_corr"]["order"])
    C = np.array(c["cross_corr"]["matrix"], dtype=float)
    acf = {m: [float(c["baseline_acf"][m][f"acf{k}"]) for k in (1, 2, 3)] for m in order}
    return order, C, acf


def yule_walker3(acf123):
    """AR(3) коэфф из acf1-3: R φ = r (Toeplitz). Возвращает φ (3,) или None если нестационарен."""
    r1, r2, r3 = acf123
    R = np.array([[1.0, r1, r2], [r1, 1.0, r1], [r2, r1, 1.0]])
    try:
        phi = np.linalg.solve(R, np.array([r1, r2, r3]))
    except np.linalg.LinAlgError:
        return None
    # стационарность: корни 1 - φ1 z - φ2 z² - φ3 z³ вне единичного круга
    roots = np.roots(np.concatenate([[1.0], -phi[::-1]]))  # coeffs высш.степень первой
    if np.any(np.abs(np.roots([-phi[2], -phi[1], -phi[0], 1.0])) <= 1.0000001):
        return None
    return phi


def _ar3(innov, phi):
    """y_t = φ1 y_{t-1}+φ2 y_{t-2}+φ3 y_{t-3} + innov_t (прямая рекурсия, burn-in отбрасывается)."""
    T = len(innov); y = np.zeros(T)
    for t in range(T):
        acc = innov[t]
        if t >= 1: acc += phi[0] * y[t-1]
        if t >= 2: acc += phi[1] * y[t-2]
        if t >= 3: acc += phi[2] * y[t-3]
        y[t] = acc
    return y


def _nearest_psd(M, eps=1e-8):
    M = (M + M.T) / 2
    w, V = np.linalg.eigh(M)
    w = np.clip(w, eps, None)
    out = (V * w) @ V.T
    d = np.sqrt(np.diag(out))
    out = out / np.outer(d, d)   # обратно к корреляции (диаг=1)
    return (out + out.T) / 2


def _est_cross(Y):
    """Выходная кросс-корр матрица (столбцы = метрики)."""
    return np.corrcoef(Y.T)


def _est_acf(y, k):
    y0, y1 = y[:-k], y[k:]
    return float(np.corrcoef(y0, y1)[0, 1])

import pandas as pd


def build_phis(order, acf):
    """AR(3) φ на метрику (Yule-Walker); fallback AR(1) при нестационарности."""
    phis = {}
    for m in order:
        p = yule_walker3(acf[m])
        phis[m] = p if p is not None else np.array([acf[m][0], 0.0, 0.0])
    return phis


def _gen_core(order, phis, L, T, rng, burn=300):
    """Слои (a)+(b): инновации ~N(0, L Lᵀ) → пер-метрика AR(3) → нормировка к ед. дисперсии."""
    V = len(order)
    Z = rng.standard_normal((T + burn, V)) @ L.T
    Y = np.empty((T, V))
    for i, m in enumerate(order):
        y = _ar3(Z[:, i], phis[m])[burn:]
        Y[:, i] = (y - y.mean()) / (y.std() + 1e-12)
    return Y


def calibrate_innov(order, C, phis, T=12000, iters=20, seed=0, lr=0.7):
    """Слой (c) — обратная задача: подобрать Σ_innov так, чтобы ВЫХОДНАЯ кросс ≈ целевой C.
    Фиксированная точка: S ← S + lr·(C − Ĉ). Возвращает (S, финальная max-ошибка)."""
    S = C.copy(); rng = np.random.default_rng(seed); err = None
    for _ in range(iters):
        L = np.linalg.cholesky(_nearest_psd(S))
        Chat = _est_cross(_gen_core(order, phis, L, T, rng))
        E = C - Chat; err = float(np.max(np.abs(E)))
        S = S + lr * E; np.fill_diagonal(S, 1.0); S = _nearest_psd(S)
    return S, err


def generate(order, phis, S_innov, T, seed, epochs=None, cov_frac=None,
             true_pairs=None, rho=0.0, start="2019-08-01"):
    """Полный генератор. true_pairs (для FDR-сценариев): независимый фон + посаженные пары
    (output-корреляция ≠0 только у них → остальные истинно-null). epochs: список
    (metric_idx, s0_frac, s1_frac, mean_shift, var_scale). cov_frac: {metric: доля наблюдений}."""
    rng = np.random.default_rng(seed)
    if true_pairs is not None:
        S = np.eye(len(order))
        for (i, j) in true_pairs:
            S[i, j] = S[j, i] = rho
        L = np.linalg.cholesky(_nearest_psd(S))
    else:
        L = np.linalg.cholesky(_nearest_psd(S_innov))
    Y = _gen_core(order, phis, L, T, rng)
    if epochs:
        for (i, s0, s1, msh, vsc) in epochs:
            a, b = int(s0 * T), int(s1 * T)
            Y[a:b, i] = Y[a:b, i] * vsc + msh
    df = pd.DataFrame(Y, columns=order)
    df.insert(0, "date", pd.date_range(start, periods=T, freq="D"))
    if cov_frac:
        for m in order:
            df.loc[rng.random(T) > cov_frac.get(m, 1.0), m] = np.nan
    return df


def self_check(manifest_path, T=12000, tol_acf=0.05, tol_cross=0.05, seed=7):
    """ГЕЙТ Stage B: генератор восстанавливает посаженное? Возвращает dict с ошибками и pass.
    T большой → MC-шум оценки << допуска, значит допуск меряет ИСТИННОЕ смещение генератора."""
    order, C, acf = load_core(manifest_path)
    phis = build_phis(order, acf)
    S, cal_err = calibrate_innov(order, C, phis, seed=seed)
    rng = np.random.default_rng(seed + 1)
    Y = _gen_core(order, phis, np.linalg.cholesky(_nearest_psd(S)), T, rng)
    acf_err = max(abs(_est_acf(Y[:, i], 1) - acf[m][0]) for i, m in enumerate(order))
    D = np.abs(_est_cross(Y) - C); np.fill_diagonal(D, 0.0)
    cross_err = float(D.max())
    wi, wj = np.unravel_index(np.argmax(D), D.shape)
    worst = f"{order[wi]}×{order[wj]}={D[wi,wj]:.3f}"
    # changepoint: посадить сдвиг среднего +2σ на метрику 0 во второй половине → детект
    df = generate(order, phis, S, T, seed=seed + 2,
                  epochs=[(0, 0.5, 1.0, 2.0, 1.0)])
    x = df[order[0]].to_numpy()
    cp_detected = (np.nanmean(x[T//2:]) - np.nanmean(x[:T//2]))
    stationary = all(yule_walker3(acf[m]) is not None for m in order)
    ok = (acf_err <= tol_acf) and (cross_err <= tol_cross) and (cp_detected > 1.0)
    return {"acf_err": round(acf_err, 4), "cross_err": round(cross_err, 4),
            "worst_cross_pair": worst,
            "calib_err": round(cal_err, 4), "changepoint_recovered": round(float(cp_detected), 3),
            "all_AR3_stationary": stationary, "tol_acf": tol_acf, "tol_cross": tol_cross,
            "PASS": bool(ok)}
