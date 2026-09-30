"""Судья переспрашивает ТОЛЬКО потерянные вердикты, один раз (28.09, решение владельца).

Замер 28.09: 6 из 30 ответов судьи ломались в середине, сломанный объект пропадал, и на пути
экстрактора пропавший вердикт необратимо делал вопрос действием. Судим нашу часть: повтор
идёт, идёт только по потерянным, ровно один раз, лишние id модели в результат не попадают,
значение вердикта вне словаря не проходит как есть. Поведение модели — дело проб.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import task_agent as ta  # noqa: E402


def _fake_client(monkeypatch, answers):
    """answers: список ответов модели по очереди (строки). Возвращает журнал запросов."""
    calls = []

    class _Msgs:
        def create(self, **kw):
            calls.append(kw["messages"][0]["content"])
            return SimpleNamespace(content=[SimpleNamespace(text=answers[len(calls) - 1])])

    monkeypatch.setattr(ta, "_get_client", lambda: SimpleNamespace(messages=_Msgs()))
    monkeypatch.setattr(ta, "_measurements_evidence", lambda text: "")
    monkeypatch.setattr(ta, "_note_parse_outcome", lambda ok, failed=False: None)
    return calls


def _v(i, verdict="patient"):
    return {"id": i, "verdict": verdict, "reason": "r", "ru": f"вопрос {i}?"}


ITEMS = [{"id": "a", "text": "Какую дозу принимаете"}, {"id": "b", "text": "Состоялся ли визит"},
         {"id": "c", "text": "Как работает PPARG"}]


def test_lost_verdict_is_asked_again_alone(monkeypatch):
    # первый ответ: объект «b» сломан в середине (salvage его теряет), соседи целы
    broken = ('[' + json.dumps(_v("a")) + ', {"id": "b", "verdict": "patient" "reason": "x"}, '
              + json.dumps(_v("c", "not_patient")) + ']')
    calls = _fake_client(monkeypatch, [broken, json.dumps([_v("b", "unsure")])])
    out = ta.addressed_to_patient(ITEMS)
    assert set(out) == {"a", "b", "c"}, out
    assert out["b"]["verdict"] == "unsure"
    assert len(calls) == 2
    assert calls[1].startswith("b: ") and "a: " not in calls[1] and "c: " not in calls[1], calls[1]


def test_no_retry_when_nothing_lost(monkeypatch):
    calls = _fake_client(monkeypatch, [json.dumps([_v("a"), _v("b"), _v("c")])])
    assert set(ta.addressed_to_patient(ITEMS)) == {"a", "b", "c"}
    assert len(calls) == 1


def test_second_miss_stays_fail_closed(monkeypatch):
    """Повтор один: второй промах — прежний fail-closed, без третьего вызова."""
    calls = _fake_client(monkeypatch, [json.dumps([_v("a"), _v("c")]), "не JSON вовсе"])
    out = ta.addressed_to_patient(ITEMS)
    assert set(out) == {"a", "c"} and len(calls) == 2


def test_extra_ids_from_model_are_dropped(monkeypatch):
    _fake_client(monkeypatch, [json.dumps([_v("a"), _v("b"), _v("c"), _v("zz")])])
    assert set(ta.addressed_to_patient(ITEMS)) == {"a", "b", "c"}


def test_unknown_verdict_is_unparsed():
    out = ta._verdicts_from_rows([{"id": "a", "verdict": "maybe", "reason": "x"}])
    assert out["a"]["verdict"] == "unparsed", out
    assert "unparsed" not in ta.ASKING_VERDICTS
