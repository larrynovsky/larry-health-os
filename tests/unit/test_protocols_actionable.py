"""Протокол без поведения не рождается и не живёт молча (нить assistant-loop, 2026-11-10).

01.11 два протокола легли с пустым behavior: ответ модели не разобрался как JSON,
запасной разбор нашёл только title. Эти тесты краснеют на коде до правки.
"""
from __future__ import annotations

import pytest

import hai_hypotheses
import health_db
import protocols_db

HYP = {"observation": "obs", "mechanism": "mech", "prediction": "pred", "memory_id": 1}


def _fake_model(monkeypatch, raw: str):
    class _Msgs:
        def create(self, **kw):
            return object()

    class _Client:
        messages = _Msgs()

    monkeypatch.setattr(hai_hypotheses, "get_client", lambda: _Client())
    monkeypatch.setattr(hai_hypotheses.llm_client, "answer_text", lambda resp: raw)
    monkeypatch.setattr(hai_hypotheses, "_judge_protocol", lambda raw: None)
    monkeypatch.setattr(hai_hypotheses.hai_core, "get_model", lambda key: "m")
    monkeypatch.setattr(hai_hypotheses.hai_core, "answer_language", lambda: "")


def test_оборванный_ответ_не_даёт_протокол_без_поведения(monkeypatch):
    _fake_model(monkeypatch, '{"title": "Режим сна", "behavior": "Ложиться в 22:45, вставать')
    with pytest.raises(hai_hypotheses.ProtocolIncomplete):
        hai_hypotheses.generate_protocol_from_hypothesis(HYP)


def test_полный_ответ_несёт_дату_пересмотра(monkeypatch):
    _fake_model(monkeypatch, '{"title": "T", "behavior": "do X daily", "frequency": "daily",'
                             ' "reminder_days": 21}')
    p = hai_hypotheses.generate_protocol_from_hypothesis(HYP)
    assert p["behavior"] == "do X daily"
    assert p["review_in_days"] == 21


def test_дом_не_принимает_протокол_без_поведения(db):
    with pytest.raises(ValueError):
        health_db.save_protocol({"title": "T", "behavior": "  "})


def test_датчик_видит_протокол_без_поведения_или_пересмотра():
    rows = [{"id": 1, "behavior": "x", "review_date": "2026-12-01"},
            {"id": 2, "behavior": "", "review_date": "2026-12-01"},
            {"id": 3, "behavior": "x", "review_date": None}]
    assert protocols_db.unactionable_protocol_ids(rows) == [2, 3]
