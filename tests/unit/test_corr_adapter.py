"""W5A-INT-2: tests для _build_cbcr_observation_from_correlation."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest
import hai_hypotheses as hh

pytestmark = pytest.mark.unit


def test_corr_adapter_weakening():
    """Независимый пример ослабления связи двух метрик."""
    drift = {
        "metric_a": "hrv", "metric_b": "resting_hr",
        "r_recent": -0.45, "r_baseline": -0.75,
        "delta_pct": 40.0, "severity": "mild",
        "n_pairs_recent": 90, "n_pairs_baseline": 90,
    }
    obs = hh._build_cbcr_observation_from_correlation(drift)
    assert obs["trigger"] == "correlation_drift"
    assert "ВСР" in obs["summary"]
    assert "ЧСС покоя" in obs["summary"]
    assert "ОСЛАБЛА" in obs["summary"]
    assert "mild" in obs["summary"]
    # raw для D2 whitelist
    assert obs["details"]["n_pairs_recent"] == 90


def test_corr_adapter_strengthening():
    """abs(recent) > abs(baseline) → УСИЛИЛАСЬ."""
    drift = {
        "metric_a": "hrv", "metric_b": "sleep_deep",
        "r_recent": 0.85, "r_baseline": 0.5,
        "delta_pct": 70, "severity": "moderate",
        "n_pairs_recent": 90, "n_pairs_baseline": 90,
    }
    obs = hh._build_cbcr_observation_from_correlation(drift)
    assert "УСИЛИЛАСЬ" in obs["summary"]


def test_corr_adapter_sign_flip():
    """recent и baseline разных знаков → СМЕНИЛА ЗНАК."""
    drift = {
        "metric_a": "weight", "metric_b": "sleep_score",
        "r_recent": -0.4, "r_baseline": 0.3,
        "delta_pct": -233, "severity": "strong",
        "n_pairs_recent": 90, "n_pairs_baseline": 90,
    }
    obs = hh._build_cbcr_observation_from_correlation(drift)
    assert "СМЕНИЛА ЗНАК" in obs["summary"]
    assert "strong" in obs["summary"]


def test_corr_unknown_metric_passthrough():
    drift = {
        "metric_a": "foo", "metric_b": "bar",
        "r_recent": 0.1, "r_baseline": 0.5,
        "delta_pct": -80, "severity": "mild",
        "n_pairs_recent": 60, "n_pairs_baseline": 60,
    }
    obs = hh._build_cbcr_observation_from_correlation(drift)
    assert "foo" in obs["summary"]
    assert "bar" in obs["summary"]


def test_corr_details_preserves_all_fields():
    drift = {
        "metric_a": "hrv", "metric_b": "resting_hr",
        "r_recent": -0.5, "r_baseline": -0.8,
        "delta_pct": 37, "severity": "mild",
        "n_pairs_recent": 90, "n_pairs_baseline": 90,
        "extra": "preserved",
    }
    obs = hh._build_cbcr_observation_from_correlation(drift)
    assert obs["details"]["extra"] == "preserved"
