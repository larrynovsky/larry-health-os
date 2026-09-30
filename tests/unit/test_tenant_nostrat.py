"""Честная нестратифицированная D-разметка тенанту (Группа 3 nostrat, 2026-07-23).

Тенант без своих эпох НЕ может различить A (рычаг) от D (со-движение) → все его gated-корреляции
честно помечаются «совместное движение, не подтверждён как рычаг» (verdict_family=D-nostrat).
НЕ «видимость»: явный пол «не рычаг». Owner-путь (реальная A/D) не тронут.
При покрытии ниже настроенного порога D-гейта (≥80 дней) разметка остаётся пустой.
"""
from __future__ import annotations

import pandas as pd

import correlation_gate as G
import longitudinal_analysis as la

_YEARLY = pd.DataFrame({"year": [2020, 2021]})
_PHASES = pd.DataFrame(columns=["phase", "phase_type", "start", "end", "n_days"])
_EMPTY_LAB = pd.DataFrame(columns=["lab", "metric", "spearman_r", "p_value", "strong", "significant"])


def _corr_gated(verdict=None):
    row = {"metric_a": "hrv", "metric_b": "steps", "spearman_r": 0.3, "p_value": 0.01,
           "p_perm": 0.002, "gate_pass": True, "strong": True, "significant": True}
    df = pd.DataFrame([row])
    if verdict is not None:
        df["verdict_family"] = [verdict]
    return df


def test_family_label_d_nostrat():
    lbl = G.family_label("D-nostrat")
    assert lbl.startswith("совместное движение")
    assert "не подтверждён как рычаг" in lbl


def test_tenant_gated_marked_nostrat():
    """Тенант (gate applied, нет стратификации, нет колонки verdict_family) → D-nostrat."""
    s = la.build_ai_summary(_YEARLY, _PHASES, {}, _corr_gated(), _EMPTY_LAB, [],
                            gate_meta={"gate_applied": True, "stratified": {"applied": False}})
    assert s["top_correlations"][0].get("verdict_family") == "D-nostrat"


def test_owner_verdict_not_overwritten():
    """Owner (колонка verdict_family есть) — реальная A/D метка НЕ затирается nostrat'ом."""
    s = la.build_ai_summary(_YEARLY, _PHASES, {}, _corr_gated(verdict="A-lever"), _EMPTY_LAB, [],
                            gate_meta={"gate_applied": True, "stratified": {"applied": True}})
    assert s["top_correlations"][0].get("verdict_family") == "A-lever"


def test_no_gatemeta_backward_compat():
    """Нет gate_meta (старый прогон/гейт не применён) → поля нет (backward-compat)."""
    s = la.build_ai_summary(_YEARLY, _PHASES, {}, _corr_gated(), _EMPTY_LAB, [])
    assert "verdict_family" not in s["top_correlations"][0]
