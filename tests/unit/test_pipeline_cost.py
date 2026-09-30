"""#139: _attach_pipeline_cost работает на success и forced путях."""
from __future__ import annotations
from pathlib import Path
import sys

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest
import cbcr_hypothesis as ch

pytestmark = pytest.mark.unit


def test_attach_aggregates_gen_plus_critique():
    """gen cost + critique cost = pipeline cost."""
    hyp = {"provenance": {"cost_estimate_usd": 0.10}}
    history = [
        {"critique": {"_cost_estimate_usd": 0.02}},
        {"critique": {"_cost_estimate_usd": 0.03}},
    ]
    ch._attach_pipeline_cost(hyp, history)
    assert hyp["provenance"]["pipeline_cost_estimate_usd"] == 0.15


def test_attach_works_with_empty_history():
    """Без regen iterations — только gen cost."""
    hyp = {"provenance": {"cost_estimate_usd": 0.17}}
    ch._attach_pipeline_cost(hyp, [])
    assert hyp["provenance"]["pipeline_cost_estimate_usd"] == 0.17


def test_attach_handles_none_costs():
    """None в полях — трактуется как 0.0."""
    hyp = {"provenance": {"cost_estimate_usd": None}}
    history = [{"critique": {"_cost_estimate_usd": None}}]
    ch._attach_pipeline_cost(hyp, history)
    assert hyp["provenance"]["pipeline_cost_estimate_usd"] == 0.0


def test_attach_creates_provenance_if_missing():
    """Если provenance нет — создаётся."""
    hyp = {}
    ch._attach_pipeline_cost(hyp, [])
    assert "provenance" in hyp
    assert hyp["provenance"]["pipeline_cost_estimate_usd"] == 0.0
