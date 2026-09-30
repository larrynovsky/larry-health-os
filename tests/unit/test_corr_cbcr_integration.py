"""W5A-INT-5: tests для нового generate_hypothesis_from_correlation на CBCR."""
from __future__ import annotations

from pathlib import Path
import sys, os, json

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    os.environ["ALLOW_WRITE_NONPRIMARY"] = "1"
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    (tmp_path / "health" / "data").mkdir(parents=True)
    import importlib, health_db
    importlib.reload(health_db)
    health_db.init_db()
    yield health_db
    del os.environ["ALLOW_WRITE_NONPRIMARY"]


@pytest.fixture
def fake_cbcr_dict():
    return {
        "hypothesis_id": "hyp_corr_test",
        "one_line_statement": "ВСР↔ЧСС покоя ОСЛАБЛА в вымышленном ряду",
        "illness_script": {"fault": {"description": "Simulated change in shared background"}},
        "falsification": {
            "etiological_confirmation": "Independent simulated series confirms change",
            "etiological_refutation": "Independent simulated series remains unchanged",
            "therapeutic_response": "No intervention in this fixture",
            "decision_threshold": "Compare with a shuffled control",
        },
        "line_of_reasoning": {"immediate_next_steps": ["Inspect 6 simulated windows"]},
        "structural_confidence": {"score": 5, "confidence": "high"},
        "provenance": {"generator": "cbcr_hypothesis", "model": "claude-sonnet-4-6"},
        "resolution_type": "self_managed",
    }


@pytest.mark.skip(reason="Defunct after Wave 5H-B (2026-05-14): generate_hypothesis_from_drift/_correlation теперь return []. Генерация гипотез перенесена в monthly_consilium. См. #178.")
def test_corr_generator_calls_cbcr_pipeline(tmp_db, fake_cbcr_dict, monkeypatch):
    """Один correlation drift → один сохранённый memory_id + cbcr payload."""
    import importlib, hai_hypotheses
    importlib.reload(hai_hypotheses)
    monkeypatch.setattr("hai_hypotheses.db", tmp_db)
    import cbcr_hypothesis as cbcr
    monkeypatch.setattr(cbcr, "generate_hypothesis_with_critique",
                        lambda obs: dict(fake_cbcr_dict))

    drifts = [{
        "metric_a": "hrv", "metric_b": "resting_hr",
        "r_recent": -0.45, "r_baseline": -0.75,
        "delta_pct": 40.0, "severity": "mild",
        "n_pairs_recent": 90, "n_pairs_baseline": 90,
    }]
    ids = hai_hypotheses.generate_hypothesis_from_correlation(drifts)
    assert len(ids) == 1
    memory_id = ids[0]
    row = tmp_db.get_cbcr_payload(memory_id)
    assert row is not None
    assert row["payload"]["one_line_statement"].startswith("ВСР")
    assert row["structural_score"] == 5
    assert row["confidence_level"] == "high"


@pytest.mark.skip(reason="Defunct after Wave 5H-B (2026-05-14): generate_hypothesis_from_drift/_correlation теперь return []. Генерация гипотез перенесена в monthly_consilium. См. #178.")
def test_corr_generator_top3_only(tmp_db, fake_cbcr_dict, monkeypatch):
    """Q2=B: только top-3 обрабатываются."""
    import importlib, hai_hypotheses
    importlib.reload(hai_hypotheses)
    monkeypatch.setattr("hai_hypotheses.db", tmp_db)
    import cbcr_hypothesis as cbcr

    call_count = {"n": 0}
    def counting_gen(obs):
        call_count["n"] += 1
        d = dict(fake_cbcr_dict)
        d["one_line_statement"] = f"ole {call_count['n']}"
        return d
    monkeypatch.setattr(cbcr, "generate_hypothesis_with_critique", counting_gen)

    drifts = [
        {"metric_a": "a", "metric_b": "b", "r_recent": 0.1, "r_baseline": 0.5,
         "delta_pct": -80, "severity": "mild", "n_pairs_recent": 60, "n_pairs_baseline": 60}
        for _ in range(5)
    ]
    ids = hai_hypotheses.generate_hypothesis_from_correlation(drifts)
    assert call_count["n"] == 3
    assert len(ids) == 3


def test_corr_generator_skips_on_exception(tmp_db, monkeypatch):
    """CBCR exception → skip без crash."""
    import importlib, hai_hypotheses
    importlib.reload(hai_hypotheses)
    monkeypatch.setattr("hai_hypotheses.db", tmp_db)
    import cbcr_hypothesis as cbcr
    monkeypatch.setattr(cbcr, "generate_hypothesis_with_critique",
                        lambda obs: (_ for _ in ()).throw(RuntimeError("api")))

    drifts = [{
        "metric_a": "hrv", "metric_b": "resting_hr",
        "r_recent": -0.4, "r_baseline": -0.8, "delta_pct": 50,
        "severity": "mild", "n_pairs_recent": 90, "n_pairs_baseline": 90,
    }]
    assert hai_hypotheses.generate_hypothesis_from_correlation(drifts) == []


def test_empty_corr_returns_empty():
    import hai_hypotheses
    assert hai_hypotheses.generate_hypothesis_from_correlation([]) == []
