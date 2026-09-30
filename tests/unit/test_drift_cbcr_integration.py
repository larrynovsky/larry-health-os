"""W5A-INT-4: tests для нового generate_hypothesis_from_drift на CBCR pipeline.

Без реальных API-вызовов — мокаем cbcr_hypothesis.generate_hypothesis_with_critique
и db.save_*.
"""
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
def mock_cbcr(monkeypatch):
    """Мок cbcr_hypothesis.generate_hypothesis_with_critique → детерминированный CBCR-dict."""
    import cbcr_hypothesis as cbcr

    fake_cbcr_dict = {
        "hypothesis_id": "hyp_test",
        "one_line_statement": "Sub-acute monotonic decline test signal",
        "illness_script": {"fault": {"description": "Test mechanism: vagal dysregulation"}},
        "falsification": {
            "etiological_confirmation": "predicted recovery 6w",
            "etiological_refutation": "x",
            "therapeutic_response": "y",
            "decision_threshold": "z",
        },
        "line_of_reasoning": {"immediate_next_steps": ["TSH+fT4 первый шаг"]},
        "structural_confidence": {"score": 4, "confidence": "medium"},
        "provenance": {"generator": "cbcr_hypothesis", "model": "claude-sonnet-4-6"},
        "resolution_type": "self_managed",
    }

    def fake_generate(observation):
        return dict(fake_cbcr_dict)

    monkeypatch.setattr(cbcr, "generate_hypothesis_with_critique", fake_generate)
    return fake_cbcr_dict


@pytest.mark.skip(reason="Defunct after Wave 5H-B (2026-05-14): generate_hypothesis_from_drift/_correlation теперь return []. Генерация гипотез перенесена в monthly_consilium. См. #178.")
def test_drift_generator_calls_cbcr_pipeline(tmp_db, mock_cbcr, monkeypatch):
    """Один drift → один сохранённый memory_id + один hypotheses_cbcr row."""
    monkeypatch.setattr("hai_hypotheses.db", tmp_db)
    import importlib, hai_hypotheses
    importlib.reload(hai_hypotheses)
    monkeypatch.setattr("hai_hypotheses.db", tmp_db)
    # Перезагрузка убивает наш monkeypatch на cbcr — снова мокаем
    import cbcr_hypothesis as cbcr
    monkeypatch.setattr(cbcr, "generate_hypothesis_with_critique",
                        lambda obs: dict(mock_cbcr))

    drifts = [{
        "metric": "deep_min", "direction": "down", "delta_pct": -18.9,
        "streak_days": 14, "current_7d": 38.2, "baseline_30d": 47.1,
    }]
    ids = hai_hypotheses.generate_hypothesis_from_drift(drifts)
    assert len(ids) == 1
    memory_id = ids[0]

    # CBCR-payload сохранён
    row = tmp_db.get_cbcr_payload(memory_id)
    assert row is not None
    assert row["payload"]["one_line_statement"].startswith("Sub-acute")
    assert row["structural_score"] == 4
    assert row["confidence_level"] == "medium"


@pytest.mark.skip(reason="Defunct after Wave 5H-B (2026-05-14): generate_hypothesis_from_drift/_correlation теперь return []. Генерация гипотез перенесена в monthly_consilium. См. #178.")
def test_drift_generator_skips_dups(tmp_db, mock_cbcr, monkeypatch):
    """Если open hypothesis с тем же observation существует — пропуск."""
    import importlib, hai_hypotheses
    importlib.reload(hai_hypotheses)
    monkeypatch.setattr("hai_hypotheses.db", tmp_db)
    import cbcr_hypothesis as cbcr
    monkeypatch.setattr(cbcr, "generate_hypothesis_with_critique",
                        lambda obs: dict(mock_cbcr))

    drifts = [{
        "metric": "deep_min", "direction": "down", "delta_pct": -18.9,
        "streak_days": 14, "current_7d": 38.2, "baseline_30d": 47.1,
    }]
    ids1 = hai_hypotheses.generate_hypothesis_from_drift(drifts)
    assert len(ids1) == 1
    # Второй раз — должно скипнуться (дедуп)
    ids2 = hai_hypotheses.generate_hypothesis_from_drift(drifts)
    assert len(ids2) == 0


def test_drift_generator_skips_on_cbcr_exception(tmp_db, monkeypatch):
    """Если CBCR падает — drift пропускается без crash."""
    import importlib, hai_hypotheses
    importlib.reload(hai_hypotheses)
    monkeypatch.setattr("hai_hypotheses.db", tmp_db)

    import cbcr_hypothesis as cbcr
    def boom(observation):
        raise RuntimeError("API timeout")
    monkeypatch.setattr(cbcr, "generate_hypothesis_with_critique", boom)

    drifts = [{
        "metric": "deep_min", "direction": "down", "delta_pct": -10,
        "streak_days": 5, "current_7d": 40, "baseline_30d": 45,
    }]
    ids = hai_hypotheses.generate_hypothesis_from_drift(drifts)
    assert ids == []


def test_drift_generator_skips_empty_mechanism(tmp_db, monkeypatch):
    """Если flatten возвращает пустой mechanism — пропуск."""
    import importlib, hai_hypotheses
    importlib.reload(hai_hypotheses)
    monkeypatch.setattr("hai_hypotheses.db", tmp_db)

    import cbcr_hypothesis as cbcr
    monkeypatch.setattr(cbcr, "generate_hypothesis_with_critique",
                        lambda obs: {"one_line_statement": "ok statement",
                                     "illness_script": {"fault": {}}})

    drifts = [{
        "metric": "deep_min", "direction": "down", "delta_pct": -10,
        "streak_days": 5, "current_7d": 40, "baseline_30d": 45,
    }]
    ids = hai_hypotheses.generate_hypothesis_from_drift(drifts)
    assert ids == []


def test_empty_drifts_returns_empty():
    """Без дрейфов — пустой список без вызовов."""
    import hai_hypotheses
    assert hai_hypotheses.generate_hypothesis_from_drift([]) == []
