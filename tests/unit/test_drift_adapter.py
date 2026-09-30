"""W5A-INT-1: tests для _build_cbcr_observation_from_drift adapter."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest
import hai_hypotheses as hh

pytestmark = pytest.mark.unit


def test_drift_adapter_basic_shape():
    """Sub-acute monotonic decline → правильные qualifiers."""
    drift = {
        "metric": "deep_min", "direction": "down", "delta_pct": -18.9,
        "streak_days": 14, "current_7d": 38.2, "baseline_30d": 47.1,
    }
    obs = hh._build_cbcr_observation_from_drift(drift)
    assert obs["trigger"] == "drift"
    assert "Chronic" in obs["summary"] or "Sub-acute" in obs["summary"]  # streak=14 → chronic
    assert "monotonic" in obs["summary"]
    assert "снижение" in obs["summary"]
    assert "глубокий сон" in obs["summary"]
    assert "-18.9%" in obs["summary"]
    assert "14" in obs["summary"]
    # raw для D2 whitelist
    assert obs["details"]["current_7d"] == 38.2
    assert obs["details"]["baseline_30d"] == 47.1


def test_acuteness_thresholds():
    """streak <3 acute, 3-13 sub-acute, ≥14 chronic."""
    assert hh._drift_acuteness(1) == "acute"
    assert hh._drift_acuteness(5) == "sub-acute"
    assert hh._drift_acuteness(13) == "sub-acute"
    assert hh._drift_acuteness(14) == "chronic"
    assert hh._drift_acuteness(90) == "chronic"


def test_course_monotonic_when_streak_long():
    """streak ≥7 → monotonic, иначе progressive."""
    assert hh._drift_course("down", 7) == "monotonic"
    assert hh._drift_course("down", 6) == "progressive"
    assert hh._drift_course("up", 30) == "monotonic"


def test_direction_up_summary():
    """direction=up → 'рост', не 'снижение'."""
    drift = {"metric": "hrv_ms", "direction": "up", "delta_pct": 25,
             "streak_days": 10, "current_7d": 30, "baseline_30d": 24}
    obs = hh._build_cbcr_observation_from_drift(drift)
    assert "рост" in obs["summary"]
    assert "ВСР" in obs["summary"]


def test_unknown_metric_passthrough():
    """Неизвестная метрика — берём raw имя."""
    drift = {"metric": "weird_unknown_metric", "direction": "down",
             "delta_pct": -10, "streak_days": 5, "current_7d": 0, "baseline_30d": 0}
    obs = hh._build_cbcr_observation_from_drift(drift)
    assert "weird_unknown_metric" in obs["summary"]


def test_details_preserves_all_drift_fields():
    """details — копия drift, нужна для D2 whitelist."""
    drift = {"metric": "hrv_ms", "direction": "down", "delta_pct": -15,
             "streak_days": 7, "current_7d": 18.5, "baseline_30d": 22.0,
             "extra_field": "preserved"}
    obs = hh._build_cbcr_observation_from_drift(drift)
    assert obs["details"]["extra_field"] == "preserved"
    assert obs["details"]["current_7d"] == 18.5
