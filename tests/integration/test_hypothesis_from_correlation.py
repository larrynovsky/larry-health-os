"""
Wave 4-CORRELATIONS C-8 — integration test для generate_hypothesis_from_correlation.

Mocked Anthropic + in-memory БД. Покрывает:
- Пустой input → пустой output.
- 1 correlation drift → 1 гипотеза с trigger='correlation_drift'.
- Топ-3 cap → 5 drifts на входе, не более 3 гипотез.
- Q2=B: дедуп НЕ применяется — две гипотезы могут существовать вместе
  (одна про hrv drift, другая про hrv↔resting_hr correlation drift).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

pytestmark = pytest.mark.integration


SAMPLE_DRIFT = {
    "metric_a": "hrv",
    "metric_b": "resting_hr",
    "r_recent": -0.588,
    "r_baseline": -0.799,
    "delta_pct": 26.4,
    "n_pairs_recent": 88,
    "n_pairs_baseline": 90,
    "severity": "mild",
}


def _mock_anthropic_response(json_payload: dict):
    """Возвращает mock-объект, имитирующий client.messages.create()."""
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=json.dumps(json_payload, ensure_ascii=False))]
    return mock_msg


@pytest.mark.skip(reason="W5A-INT-5: тест мокает hai_hypotheses.get_client, но после переписания функция вызывает cbcr_hypothesis pipeline. Регрессия покрыта tests/unit/test_corr_cbcr_integration.py")
def test_empty_drifts_returns_empty(db):
    """Пустой input → пустой output, без обращения к API."""
    import sys
    sys.path.insert(0, str(Path(__file__).parents[2]))
    from hai_hypotheses import generate_hypothesis_from_correlation

    with patch("hai_hypotheses.get_client") as mock_client:
        result = generate_hypothesis_from_correlation([])
    assert result == []
    mock_client.assert_not_called()


@pytest.mark.skip(reason="W5A-INT-5: тест мокает hai_hypotheses.get_client, но после переписания функция вызывает cbcr_hypothesis pipeline. Регрессия покрыта tests/unit/test_corr_cbcr_integration.py")
def test_single_drift_creates_one_hypothesis(db, monkeypatch):
    """1 drift → 1 гипотеза с правильным trigger."""
    import sys
    sys.path.insert(0, str(Path(__file__).parents[2]))
    import hai_hypotheses
    import health_db

    # Mock Anthropic client
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_anthropic_response({
        "mechanism": "ослабление вагусного тонуса",
        "prediction": "HRV ниже 17 мс продержится 14 дней подряд",
        "test": "HRV variability >5 мс в течение недели — опровергает",
        "resolution_type": "self_managed",
    })
    monkeypatch.setattr(hai_hypotheses, "get_client", lambda: mock_client)

    ids = hai_hypotheses.generate_hypothesis_from_correlation([SAMPLE_DRIFT])
    assert len(ids) == 1, f"Ожидалась 1 гипотеза, получили {len(ids)}"

    # Проверяем сохранение в memory с правильным trigger.
    saved = health_db.get_memory(category="hypothesis", n=10)
    matching = [
        m for m in saved
        if "корреляция" in (m.get("value") or "")
        and "correlation_drift" in (m.get("value") or "")
    ]
    assert matching, "Гипотеза с trigger=correlation_drift не найдена в memory"

    # Source должен быть 'auto_correlation' (после расширения save_hypothesis).
    assert any(m.get("source") == "auto_correlation" for m in saved), \
        f"source 'auto_correlation' отсутствует. Источники: {[m.get('source') for m in saved]}"


@pytest.mark.skip(reason="W5A-INT-5: тест мокает hai_hypotheses.get_client, но после переписания функция вызывает cbcr_hypothesis pipeline. Регрессия покрыта tests/unit/test_corr_cbcr_integration.py")
def test_top_3_cap(db, monkeypatch):
    """Если на входе 5 drifts — генерируется не более 3 гипотез."""
    import sys
    sys.path.insert(0, str(Path(__file__).parents[2]))
    import hai_hypotheses

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_anthropic_response({
        "mechanism": "test", "prediction": "test", "test": "test",
        "resolution_type": "self_managed",
    })
    monkeypatch.setattr(hai_hypotheses, "get_client", lambda: mock_client)

    # 5 drifts с разной severity и парой
    drifts = []
    for i in range(5):
        d = dict(SAMPLE_DRIFT)
        d["metric_a"] = f"metric_{i}_a"
        d["metric_b"] = f"metric_{i}_b"
        d["delta_pct"] = 30.0 + i * 5
        drifts.append(d)

    ids = hai_hypotheses.generate_hypothesis_from_correlation(drifts)
    assert len(ids) <= 3, f"Должно быть max 3, получили {len(ids)}"


@pytest.mark.skip(reason="W5A-INT-5: тест мокает hai_hypotheses.get_client, но после переписания функция вызывает cbcr_hypothesis pipeline. Регрессия покрыта tests/unit/test_corr_cbcr_integration.py")
def test_drift_and_correlation_coexist_no_dedup(db, monkeypatch):
    """Q2=B: дедуп не применяется. Гипотеза про drift hrv и про корреляцию
    hrv↔resting_hr могут существовать параллельно.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parents[2]))
    import hai_hypotheses

    # Сначала сохраняем drift-гипотезу вручную (имитируем generate_hypothesis_from_drift).
    drift_id = hai_hypotheses.save_hypothesis(
        observation="ВСР упал на -20% (4д подряд)",
        mechanism="вагусное снижение",
        prediction="HRV<17 продержится 14 дней",
        test="HRV>22 опровергнет",
        trigger="drift",
    )
    assert drift_id is not None

    # Теперь correlation drift с теми же метриками — должна создаться отдельная.
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_anthropic_response({
        "mechanism": "ослабление ВНС-связи",
        "prediction": "восстановление обратной связи в течение месяца",
        "test": "HRV растёт + resting_hr падает синхронно",
        "resolution_type": "self_managed",
    })
    monkeypatch.setattr(hai_hypotheses, "get_client", lambda: mock_client)

    correlation_ids = hai_hypotheses.generate_hypothesis_from_correlation([SAMPLE_DRIFT])
    assert len(correlation_ids) == 1, "Correlation-гипотеза должна быть создана"
    assert correlation_ids[0] != drift_id, \
        "Q2=B: correlation и drift hypotheses должны быть РАЗНЫМИ memory entries"


@pytest.mark.skip(reason="Wave 5A-INT-5: generate_hypothesis_from_correlation переписана на CBCR pipeline, API вызов теперь через cbcr_hypothesis.generate_hypothesis_with_critique, не client.messages.create напрямую")

def test_uses_sonnet_not_haiku(db, monkeypatch):
    """C-3 явно использует Sonnet (более глубокая семантика для пары метрик)."""
    import sys
    sys.path.insert(0, str(Path(__file__).parents[2]))
    import hai_hypotheses

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_anthropic_response({
        "mechanism": "x", "prediction": "x", "test": "x",
        "resolution_type": "self_managed",
    })
    monkeypatch.setattr(hai_hypotheses, "get_client", lambda: mock_client)

    hai_hypotheses.generate_hypothesis_from_correlation([SAMPLE_DRIFT])

    # Проверяем что был вызов с model=sonnet
    calls = mock_client.messages.create.call_args_list
    assert calls, "client.messages.create не вызывался"
    used_model = calls[0].kwargs.get("model", "") if calls[0].kwargs else ""
    assert "sonnet" in used_model.lower(), \
        f"Ожидалась Sonnet модель (correlation требует deeper reasoning), получили {used_model!r}"
