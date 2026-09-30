"""Датчик симметрии дубля значения (BL-DOCAGENT-DEAD-1, 2026-07-06).

doc_agent._MODEL_FALLBACK осознанно дублирует hai_core.MODEL_DEFAULTS["sonnet"]
(hai_core на MacBook не импортируется по R1/R2). Дубль без датчика = split-brain
(feedback_duplicate_value_splitbrain) — этот тест и есть датчик; гоняется там,
где hai_core доступен (Studio nightly / staging), иначе skip.
"""

import pytest

try:
    import hai_core
except Exception:  # RuntimeError R1/R2 вне Studio — не ImportError
    pytest.skip("hai_core недоступен вне Studio (R1/R2)", allow_module_level=True)


def test_model_fallback_matches_hai_core_code_default(monkeypatch, tmp_path):
    sd = tmp_path / "s"
    sd.mkdir()
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(sd))  # тенант-гард secrets_paths
    import doc_agent
    assert doc_agent._MODEL_FALLBACK == hai_core.MODEL_DEFAULTS["sonnet"], (
        "фолбэк doc_agent разъехался с hai_core.MODEL_DEFAULTS — обнови _MODEL_FALLBACK"
    )
