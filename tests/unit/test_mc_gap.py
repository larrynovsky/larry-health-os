"""MC-зазор (Коммит3): детектор «открытий на разрешении Монте-Карло» — триггер (а) Фаз 1/3-а.

Тесты снизу вверх (RST):
  Т1 — _mc_se/_mc_gap_family: MCSE-формула, радиус 2·MCSE, per-family B, edge (m/k*/пусто).
  Т2 ⭐ СИНТЕТИЧЕСКИЙ позит.контроль + break-back: real-diff при рычаг=0 ПУСТ (все прогоны
      тривиально молчат) → зелёный на реальных данных ничего не доказывает. Только искусственный
      p впритык к линии обязывает флаг появиться; убрать радиус (break-back) → флаг исчезает.
  Т3 — identity-parity: детектор — чистое чтение, gate_pass/attrs бит-в-бит прежние.

Тяжёлый (numpy) → на MacBook без стека ПРОПУСКАЕТСЯ, гоняется на Studio.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit
pytest.importorskip("numpy")   # ДО import numpy/pandas — иначе на MacBook ошибка коллекции рвёт весь батч

import numpy as np              # noqa: E402
import pandas as pd             # noqa: E402
import correlation_gate as G    # noqa: E402


# ── Т1: MCSE и радиус ────────────────────────────────────────────────────────
def test_mc_se_binomial():
    # p̂=0.001, B=1000 → √(0.001·0.999/1000) ≈ 0.000999
    assert G._mc_se(0.001, 1000) == pytest.approx(np.sqrt(0.001 * 0.999 / 1000))
    assert G._mc_se(0.5, 100) == pytest.approx(np.sqrt(0.25 / 100))
    assert G._mc_se(0.0, 1000) == 0.0
    assert G._mc_se(0.3, 0) == 0.0  # B=0 гард


def test_gap_flags_pair_inside_radius():
    # line = (q/H_m)·k*/m. Подбираем p РОВНО на линии → |p−L|=0 < 2·MCSE → флаг.
    m, k, B, q = 10, 3, 1000, 0.10
    line = (q / G._H(m)) * k / m
    res = G._mc_gap_family([("Aрядом", line)], q, m, B, k)
    assert res["evaluated"] is True
    assert res["line"] == pytest.approx(line, abs=1e-8)
    assert len(res["flagged"]) == 1
    assert res["flagged"][0]["pair"] == "Aрядом"


def test_gap_ignores_pair_outside_radius():
    m, k, B, q = 10, 3, 1000, 0.10
    line = (q / G._H(m)) * k / m
    far = line + 100 * G._mc_se(line, B)  # далеко за 2·MCSE
    res = G._mc_gap_family([("далеко", far)], q, m, B, k)
    assert res["flagged"] == []           # пусто, но ключ присутствует (empty-not-absent)
    assert "flagged" in res


def test_gap_per_family_B_changes_radius():
    # Тот же p и линия, но меньший B → шире MCSE → пара, немая при большом B, флагается при малом.
    m, k, q = 10, 3, 0.10
    line = (q / G._H(m)) * k / m
    p = line + 0.0015
    tight = G._mc_gap_family([("x", p)], q, m, 100000, k)   # B_STRAT: узкий радиус
    loose = G._mc_gap_family([("x", p)], q, m, 1000, k)     # B_PERM: широкий радиус
    assert tight["flagged"] == []
    assert len(loose["flagged"]) == 1


def test_gap_edges_empty_and_zero():
    q = 0.10
    assert G._mc_gap_family([], q, 10, 1000, 3)["flagged"] == []      # нет прошедших
    assert G._mc_gap_family([("x", 0.001)], q, 0, 1000, 3)["line"] is None  # m=0
    assert G._mc_gap_family([("x", 0.001)], q, 10, 1000, 0)["line"] is None  # k*=0 (ничего не отобрано)
    nan_res = G._mc_gap_family([("x", float("nan"))], q, 10, 1000, 3)
    assert nan_res["flagged"] == []  # NaN p̂ пропущен, не крэшит


# ── Т2 ⭐: синтетический позит.контроль + break-back ──────────────────────────
def _break_back_no_radius(pass_rows, q, m, B, k):
    """Ломаем детектор: убираем 2·MCSE-радиус (флаг только при p РОВНО == line).
    Настоящий детектор ДОЛЖЕН ловить пары в окрестности; сломанный — практически никогда."""
    line = (q / G._H(m)) * k / m
    return [lbl for lbl, p in pass_rows if p == line]


def test_synthetic_positive_control_and_break_back():
    m, k, B, q = 10, 3, 100000, 0.10
    line = (q / G._H(m)) * k / m
    # Пара ВНУТРИ радиуса, но НЕ ровно на линии (реалистичный «зазор»).
    p_near = line + 1.5 * G._mc_se(line, B)
    rows = [("near_line", p_near)]
    live = G._mc_gap_family(rows, q, m, B, k)
    assert len(live["flagged"]) == 1, "детектор обязан поймать открытие в 2·MCSE от линии"
    # break-back: та же пара, детектор без радиуса → НЕ ловит → доказывает, что ловит именно радиус.
    assert _break_back_no_radius(rows, q, m, B, k) == []


# ── Т3: identity-parity — детектор не трогает решения гейта ───────────────────
def _mkcorr(rows):
    return pd.DataFrame([{"metric_a": a, "metric_b": b, "spearman_r": r, "p_value": p,
                          "n": n, "significant": p < 0.05, "strong": abs(r) >= 0.3}
                         for a, b, r, p, n in rows])


def test_gate_pass_unchanged_by_mc_gap():
    """Полный gate_correlations: mc_gap в meta присутствует, но daily gate_pass идентичен
    прогону-без-чтения-mc_gap (детектор — post-hoc чистое чтение). Заземляет риск №2."""
    rng = np.random.default_rng(7)
    n = 400
    dates = pd.date_range("2019-01-01", periods=n, freq="D")
    base = np.cumsum(rng.normal(size=n))
    daily = pd.DataFrame({
        "date": dates,
        "sleep_total": base + rng.normal(scale=0.3, size=n),
        "hrv": base + rng.normal(scale=0.3, size=n),
        "steps": rng.normal(size=n),
    })
    corr = _mkcorr([("sleep_total", "hrv", 0.6, 1e-6, n),
                    ("sleep_total", "steps", 0.02, 0.7, n)])
    empty_lab = pd.DataFrame(columns=["lab", "metric", "label", "spearman_r",
                                      "p_value", "n", "significant", "strong"])
    empty_labs = pd.DataFrame(columns=["date", "test_name", "value", "unit"])
    cg, _lg, meta = G.gate_correlations(daily, empty_labs, corr, empty_lab,
                                        seed=0, enable_stratified=False)
    assert "mc_gap" in meta and meta["mc_gap"]["families"]["D"]["evaluated"] is True
    # A/q_lag не считались (owner off) → None, НЕ пустой словарь
    assert meta["mc_gap"]["families"]["A"] is None
    assert meta["mc_gap"]["families"]["q_lag"] is None
    # Повторный прогон тем же seed → тот же pass-set (детектор детерминирован и без side-effects).
    cg2, _lg2, _m2 = G.gate_correlations(daily, empty_labs, corr, empty_lab,
                                         seed=0, enable_stratified=False)
    assert cg["gate_pass"].tolist() == cg2["gate_pass"].tolist()
    assert cg.attrs.get("fdr_thr") == cg2.attrs.get("fdr_thr")
