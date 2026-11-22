"""G2: semantic dedup на пути monthly_consilium._save_consilium_hypothesis.

Раньше консилиум был единственным источником гипотез без semantic-dedup
(literature_curator/survivorship_curator его уже имели, а замысел в
hypothesis_engine.md утверждал, что имеет и консилиум). Фиксируем контракт:
дубль → пропуск (save_hypothesis НЕ зовётся, None); не-дубль → сохранение.

Позитивный контроль: убери semcheck.check в _save_consilium_hypothesis —
save_hypothesis начнёт зваться на дубле, и assert_not_called ниже покраснеет.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

_HYP = {
    "theme": "HRV и шаги",
    "patient_view": {
        "noticed": "HRV снижается в дни высокой активности",
        "might_mean": "перетренированность",
        "possible_causes": [{"how_to_check": "сравнить HRV и шаги за месяц"}],
        "do_now": "снизить нагрузку на неделю и пересмотреть",
    },
    "consensus": {"count": 7},
}


def test_save_consilium_hypothesis_skips_semantic_dup():
    """Дубль по смыслу → save_hypothesis не вызывается, функция вернёт None."""
    import monthly_consilium as mc
    with patch("hypothesis_semantic_check.check",
               return_value=(True, 42, "та же тема HRV/активность")), \
         patch("hai_hypotheses.save_hypothesis") as mock_save:
        result = mc._save_consilium_hypothesis(_HYP)

    mock_save.assert_not_called()      # ключевой assert: дубль не сохраняется
    assert result is None


def test_save_consilium_hypothesis_saves_when_not_dup():
    """Не дубль → save_hypothesis вызывается, возвращается memory_id."""
    import monthly_consilium as mc
    with patch("hypothesis_semantic_check.check",
               return_value=(False, None, "")), \
         patch("hai_hypotheses.save_hypothesis", return_value=99) as mock_save, \
         patch.object(mc.db, "save_cbcr_payload", return_value=None):
        result = mc._save_consilium_hypothesis(_HYP)

    mock_save.assert_called_once()
    assert result == 99


def test_save_consilium_hypothesis_proceeds_when_semcheck_errors():
    """semcheck упал → не роняем сохранение (fail-open), гипотеза сохраняется."""
    import monthly_consilium as mc
    with patch("hypothesis_semantic_check.check", side_effect=RuntimeError("haiku down")), \
         patch("hai_hypotheses.save_hypothesis", return_value=7) as mock_save, \
         patch.object(mc.db, "save_cbcr_payload", return_value=None):
        result = mc._save_consilium_hypothesis(_HYP)

    mock_save.assert_called_once()
    assert result == 7


def test_консилиум_передаёт_прогноз_в_числах_а_старое_поле_не_меняется():
    """Этап В (11.11): forecast едет рядом; prediction по-прежнему how_to_check — его читают люди."""
    import monthly_consilium as mc
    fc = [{"metric": "hrv", "horizon_days": 30, "threshold": 22, "bound": "at_least"}]
    for given, expected in ((fc, fc), ("не список", None), (None, None)):
        with patch("hypothesis_semantic_check.check", return_value=(False, None, "")), \
             patch("hai_hypotheses.save_hypothesis", return_value=5) as mock_save, \
             patch.object(mc.db, "save_cbcr_payload", return_value=None):
            assert mc._save_consilium_hypothesis({**_HYP, "forecast": given, "no_forecast_reason": "нет"}) == 5
        kw = mock_save.call_args.kwargs
        assert kw["forecast"] == expected and kw["prediction"] == "сравнить HRV и шаги за месяц"


def test_инструкция_координатора_несёт_раздел_прогноза(monkeypatch):
    import monthly_consilium as mc
    monkeypatch.setattr("hypothesis_verdict.forecast_forms_now", lambda: "ФОРМЫ-И-ПОКАЗАТЕЛИ")
    seen = {}

    class Client:
        class messages:
            @staticmethod
            def create(**kw):
                seen.update(kw)
                raise RuntimeError("стоп")
    monkeypatch.setattr(mc.llm_client, "guarded_client", lambda: Client)
    assert mc._run_coordinator("пакет", [])["hypotheses"] == []
    assert "ФОРМЫ-И-ПОКАЗАТЕЛИ" in seen["system"] and "no_forecast_reason" in seen["system"]
    assert "Не подбирай тему под измеримое" in seen["system"]
