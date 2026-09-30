"""Stage B — сторожа в suite (иначе корректность генератора и валидность manifest v2 никто не увидит).

Детерминированы (фикс. seed) → не флейки. Требуют numpy/scipy/yaml → ночной прогон на Studio.
"""
import sys
import pathlib

import pytest

_VG = pathlib.Path(__file__).resolve().parents[2] / "methodology" / "validation_gate"
sys.path.insert(0, str(_VG))
_MANIFEST = str(_VG / "data_manifest.yaml")


@pytest.mark.owner_data
def test_stage_b_selfcheck_passes():
    """ГЕЙТ Stage B в suite: генератор восстанавливает посаженные ACF/кросс/changepoint.
    Сломают обратную калибровку / Yule-Walker → self_check провалится → тест краснеет."""
    import stage_b_twin as tw
    r = tw.self_check(_MANIFEST)
    assert r["PASS"], f"self_check провалился: {r}"
    assert r["acf_err"] <= r["tol_acf"], f"ACF не восстановлен: {r}"
    assert r["cross_err"] <= r["tol_cross"], f"кросс не восстановлен (обратная калибровка?): {r}"
    assert r["all_AR3_stationary"], "нестационарный AR — Yule-Walker вышел за круг"


@pytest.mark.owner_data
def test_manifest_twin_core_valid():
    """Guard manifest v2 twin_baseline_core: матрица — валидная корреляционная (симметр./diag=1/PSD).
    Порча ядра (из которого сэмплит генератор) → тест краснеет."""
    import yaml
    import numpy as np
    d = yaml.safe_load(open(_MANIFEST))
    assert d["version"] >= 2, "manifest должен быть v2+"
    c = d["twin_baseline_core"]
    order = c["cross_corr"]["order"]
    M = np.array(c["cross_corr"]["matrix"], dtype=float)
    assert M.shape == (11, 11), f"матрица не 11×11: {M.shape}"
    assert np.allclose(M, M.T), "матрица не симметрична"
    assert np.allclose(np.diag(M), 1.0), "диагональ ≠ 1"
    assert float(np.linalg.eigvalsh(M).min()) >= -1e-8, "матрица не PSD"
    assert len(order) == 11 and len(c["baseline_acf"]) == 11, "ядро не 11 метрик"


def test_m1_drift_sensor_positive_control():
    """M1 датчик дрейфа квалификации: низкий дрейф → тихо; сдвиг уровня +5σ между эпохами → алерт.
    Позит.контроль (сломают порог/логику → breach не сработает на 5σ → красный)."""
    import numpy as np
    import pandas as pd
    import integrity_tests as it
    epochs = [{"name": "e1", "range": ["2019-01-01", "2020-12-31"]},
              {"name": "e2", "range": ["2021-01-01", "2022-12-31"]}]
    bacf = {"hrv": {"acf1": 0.3}}
    rng = np.random.default_rng(0)
    days = pd.date_range("2019-06-01", periods=800, freq="D")
    lo = pd.DataFrame({"date": days, "hrv": rng.normal(0, 1, 800)})
    assert it._compute_qual_drift(lo, bacf, epochs)["breach"] is False, "низкий дрейф не должен алертить"
    hi = lo.copy()
    hi.loc[hi.date >= pd.Timestamp("2021-01-01"), "hrv"] = hi.loc[hi.date >= pd.Timestamp("2021-01-01"), "hrv"] + 5.0
    r = it._compute_qual_drift(hi, bacf, epochs)
    assert r["breach"] is True, f"сдвиг +5σ обязан алертить: {r}"


def test_qualification_flip_holds():
    """Сторож флипа qualify() (follow-up the-end): квалификация держится, пока release_result заморожен
    и 8/8 batch-сценариев built. Испортят protocol.release_result/built-флаги → тест краснеет."""
    import sys as _sys
    _root = str(pathlib.Path(__file__).resolve().parents[2])
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    import fdr_qualification as q
    s = q.status()
    assert s["qualified"] is True, f"квалификация должна держаться: {s}"
    assert s["release_ready"] is True, f"release_ready должно быть True: {s}"
    assert s["scenarios_built"] == s["scenarios_total"], f"не все batch-сценарии built: {s}"
    r = q.qualify()   # не должен raise
    assert r["acceptance_all_holm_le_q_delta"] is True, f"stress-критерий не пройден: {r}"
