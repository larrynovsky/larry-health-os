"""Лаб-путь: scope как РЕЖИМ (не запись) + один канонизатор у producer и у гейта.

Контроли двух механизмов:
  • Неактивная семья не участвует в гейте до явного включения.
  • Producer и гейт используют один канонизатор, чтобы корреляция
    и перестановочный критерий считались на одной выборке.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.unit
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import correlation_gate as G
import longitudinal_analysis as la
import signal_family as sf


def _daily(n=420, seed=3):
    rng = np.random.default_rng(seed)
    d = pd.date_range("2025-01-01", periods=n, freq="D")
    out = {"date": d}
    for c in ("hrv", "sleep_deep", "sleep_total", "steps", "sleep_score", "readiness"):
        out[c] = rng.normal(size=n)
    return pd.DataFrame(out)


def _labs(daily, n_hgb=14, n_alias=6):
    """Синтетический ряд под каноническим именем и его словарным синонимом."""
    days = list(daily["date"])
    rows = []
    for i in range(n_hgb):
        rows.append({"date": days[10 + i * 8], "test_name": "HGB", "value": 120 + i})
    for i in range(n_alias):
        rows.append({"date": days[200 + i * 9], "test_name": "Hemoglobin", "value": 130 + i})
    return pd.DataFrame(rows)


def _lab_corr(daily, labs):
    return la.lab_metric_correlations(daily, labs)


# ── P1-04: scope как режим ───────────────────────────────────────────────────

def test_descoped_lab_cannot_enter_belief_even_with_tiny_p(monkeypatch):
    """Ключевой негативный контроль: даже если бы пара прошла, при descoped её нет в вере.

    Проверяем не «находок не случилось» (это про данные), а что путь НЕ ОЦЕНИВАЕТСЯ:
    p_perm не считается вовсе, gate_pass заведомо False, и статус в meta говорит правду."""
    monkeypatch.setattr(sf, "LABS_ACTIVE", False)
    monkeypatch.setattr(sf, "LABS_SCOPE", "descoped_pending_data")
    daily, labs = _daily(), None
    labs = _labs(daily)
    lc = _lab_corr(daily, labs)
    assert not lc.empty, "фикстура обязана дать хоть одну лаб-пару, иначе тест ничего не проверяет"

    _, lg, meta = G.gate_correlations(daily, labs, pd.DataFrame(columns=[
        "metric_a", "metric_b", "spearman_r", "p_value", "significant", "strong"]), lc)
    assert bool(lg["gate_pass"].any()) is False
    assert lg["p_perm"].isna().all(), "descoped путь не должен ВЫЧИСЛЯТЬ p — он не оценивается"
    assert meta["lab_status"] == "not_evaluated" and meta["lab_pass"] == 0
    assert meta["lab_scope"] == "descoped_pending_data"

    # и подложенный «прошедший» результат физически не попадает в веру
    lg2 = lg.copy()
    lg2.loc[lg2.index[0], "p_perm"] = 1e-9
    s = la.build_ai_summary(pd.DataFrame({"year": [2024, 2025]}), pd.DataFrame(), {},
                            _corr_gated(), lg2, [])
    assert s["lab_metric_correlations"] == [], "gate_pass=False держит пару вне веры"


def test_active_scope_actually_evaluates(monkeypatch):
    """Позитивный контроль: правило не запрещает всё подряд — при active путь считается.
    Без него предыдущий тест зелен и на сломанном лаб-движке."""
    monkeypatch.setattr(sf, "LABS_ACTIVE", True)
    daily = _daily()
    labs = _labs(daily)
    lc = _lab_corr(daily, labs)
    _, lg, meta = G.gate_correlations(daily, labs, pd.DataFrame(columns=[
        "metric_a", "metric_b", "spearman_r", "p_value", "significant", "strong"]), lc)
    assert meta["lab_status"] == "evaluated"
    assert lg["p_perm"].notna().any(), "при active хотя бы одна пара обязана быть посчитана"


def test_unknown_scope_fails_static():
    """Опечатка в манифесте валит импорт, а не открывает путь молча."""
    assert sf._validated_scope("active") == "active"
    assert sf._validated_scope("descoped_pending_data") == "descoped_pending_data"
    with pytest.raises(ValueError, match="scope"):
        sf._validated_scope("enabled")          # правдоподобная опечатка
    with pytest.raises(ValueError, match="scope"):
        sf._validated_scope("")


# ── P2-01: один канонизатор ──────────────────────────────────────────────────

def test_producer_and_gate_see_identical_sample_set(monkeypatch):
    """Паритет выборок по каждому аналиту. Оракул работает и при выключенном пути —
    поэтому чинить канонизацию можно, не открывая scope."""
    monkeypatch.setattr(sf, "LABS_ACTIVE", True)
    daily = _daily()
    labs = _labs(daily, n_hgb=14, n_alias=6)
    lc = _lab_corr(daily, labs)
    prod = lc.attrs.get("producer_obs_n", {})
    assert prod.get("HGB") == 20, "producer обязан сложить оба имени (14 + 6)"

    _, lg, meta = G.gate_correlations(daily, labs, pd.DataFrame(columns=[
        "metric_a", "metric_b", "spearman_r", "p_value", "significant", "strong"]), lc)
    gate = meta["lab_obs_n"]
    for lab in sorted(set(lc["lab"])):
        assert gate.get(lab) == prod.get(lab), (
            f"{lab}: producer видит {prod.get(lab)} наблюдений, гейт {gate.get(lab)} — "
            "сырой эффект и нуль считаются на разных выборках (P2-01)")


def test_alias_only_analyte_is_visible_to_gate(monkeypatch):
    """Кейс, который был полностью невидим гейту: аналит лежит в БД ТОЛЬКО под вариантом.
    Раньше `test_name == 'HGB'` давал 0 строк, и это было неотличимо от «данных нет»."""
    monkeypatch.setattr(sf, "LABS_ACTIVE", True)
    daily = _daily()
    labs = _labs(daily, n_hgb=0, n_alias=18)      # только «Hemoglobin»
    lc = _lab_corr(daily, labs)
    assert not lc.empty, "producer видит аналит по канону"
    _, _, meta = G.gate_correlations(daily, labs, pd.DataFrame(columns=[
        "metric_a", "metric_b", "spearman_r", "p_value", "significant", "strong"]), lc)
    assert meta["lab_obs_n"].get("HGB") == 18, "гейт обязан видеть тот же аналит по канону"


def _corr_gated():
    return pd.DataFrame([{"metric_a": "hrv", "metric_b": "sleep_deep", "spearman_r": 0.6,
                          "p_value": 1e-9, "n": 400, "significant": True, "strong": True,
                          "gate_pass": True, "p_perm": 0.001, "derived": False, "coverage": 0.9}])
