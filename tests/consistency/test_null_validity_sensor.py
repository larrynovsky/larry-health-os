"""Фаза 3-б fdr-online: ДАТЧИК ВАЛИДНОСТИ НУЛЯ (nightly).

Ловит ТИХИЙ откат самого статистического нуля (baseline-ACF/эпохи/strat-код) — сторож BH/BY этого НЕ
видит (он про отбор, не про нуль). Синтетический H0 (data-independent): если type-I прод-статистика
`_strat_pvalue` уплыл из номинальной полосы — нуль протух, тест падает → в run_checks + самодатируемый
регресс-алерт (живой канал, получатель владелец); liveness = сам ночной прогон. Позит-контроль внизу
доказывает, что полоса РАЗЛИЧАЕТ (глобальный нестратиф. нуль под эпоха-дрейфом обязан её пробить).
Гоняет ПРОДОВЫЙ статистик (не суррогат). БД не нужна.
"""
from __future__ import annotations

import pytest

# numpy/pandas — под importorskip: на MacBook их нет, а голый import на уровне модуля рвал
# КОЛЛЕКЦИЮ всего батча (не скип, а error). Тот же дефект чинили в test_mc_gap.py @417feac —
# фикс был точечным, класс остался. Найдено прогоном 2026-07-25.
np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

import correlation_gate as G   # noqa: E402

_D = pd.date_range("2019-08-01", "2026-01-31", freq="D")
_GRID = pd.DataFrame({"date": _D}).set_index("date").asfreq("D")
EPI = G._epoch_indices(_GRID.index)
N = len(_D)
BAND = 0.12   # номинал 0.05 + ~3σ (reps=100) — падает при реальной инфляции, устойчив к MC-шуму


def _ar1(L, phi, rng):
    x = np.zeros(L)
    s = np.sqrt(1 - phi * phi) if phi else 1.0
    for t in range(1, L):
        x[t] = phi * x[t - 1] + rng.normal() * s
    return x


def _h0(phi, eta, rng):
    """H0: x⊥y (нет связи) + общий эпоха-дрейф η. Стратификация обязана снять η → номинал."""
    x = np.full(N, np.nan); y = np.full(N, np.nan)
    for idx in EPI:
        L = len(idx)
        if L < 2:
            continue
        s = rng.normal() if eta > 0 else 0.0
        x[idx] = eta * s + _ar1(L, phi, rng)
        y[idx] = eta * s + _ar1(L, phi, rng)
    return x, y


def _tau(phi):
    q = phi * phi
    return 0.0 if q <= 0 else -1.0 / np.log(q)


def _typeI_strat(phi, eta, reps, B, seed):
    rng = np.random.default_rng(seed)
    tau = _tau(phi)
    ps = []
    for _ in range(reps):
        x, y = _h0(phi, eta, rng)
        p, _, _ = G._strat_pvalue(G._grank(pd.Series(x)), G._grank(pd.Series(y)), EPI, tau, 0.15, B, rng)
        if p == p:
            ps.append(p)
    return float((np.array(ps) < 0.05).mean()) if ps else float("nan")


def _typeI_global(phi, eta, reps, B, seed):
    """Глобальный нестратиф. нуль (circular shift всего ряда) — под эпоха-дрейфом обязан инфлировать."""
    rng = np.random.default_rng(seed)
    ps = []
    for _ in range(reps):
        x, y = _h0(phi, eta, rng)
        rx, ry = G._grank(pd.Series(x)), G._grank(pd.Series(y))
        obs = G._masked_corr(rx, ry)
        if obs is None:
            continue
        cnt = tot = 0
        for _b in range(B):
            k = int(rng.integers(G.GUARD, N - G.GUARD))
            rc = G._masked_corr(rx, np.roll(ry, k))
            if rc is not None:
                tot += 1
                if abs(rc) >= abs(obs):
                    cnt += 1
        if tot:
            ps.append((1 + cnt) / (1 + tot))
    return float((np.array(ps) < 0.05).mean()) if ps else float("nan")


@pytest.mark.owner_data
def test_null_validity_nominal():
    """Реалистичный режим (φ=0.6, η=0.8): type-I прод-нуля в номинальной полосе. Падение = нуль протух."""
    a = _typeI_strat(0.6, 0.8, reps=100, B=1000, seed=101)
    assert a <= BAND, f"ДАТЧИК НУЛЯ: type-I={a:.3f} > {BAND} — статистический нуль протух (baseline-ACF/эпохи/strat)"


@pytest.mark.owner_data
def test_null_sensor_has_teeth():
    """Позит-контроль: глобальный нестратиф. нуль под эпоха-дрейфом (φ=0.9,η=0.8) ПРОБИВАЕТ полосу —
    значит сенсор различает валидный нуль от невалидного (не вакуумный)."""
    a_global = _typeI_global(0.9, 0.8, reps=40, B=800, seed=202)
    assert a_global > BAND, f"глобальный нуль под дрейфом обязан инфлировать (>{BAND}), иначе сенсор слеп: {a_global:.3f}"
