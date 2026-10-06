"""hypothesis_semantic_check Haiku-deduplicator (SX-17d)."""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.unit


def test_check_returns_false_when_no_existing(db, anthropic_mock):
    import hypothesis_semantic_check as hsc
    is_dup, mid, reason = hsc.check("новое уникальное наблюдение про сон")
    assert is_dup is False
    assert mid is None


def test_check_returns_true_when_haiku_says_duplicate(db, anthropic_mock):
    """Имитируем: есть открытая гипотеза + Haiku вернул is_duplicate=true."""
    import health_db
    payload = json.dumps({
        "observation": "Глубокий сон упал до 0.5ч",
        "mechanism": "x",
        "prediction": "y",
        "test": "z",
        "status": "open",
        "trigger": "manual",
    }, ensure_ascii=False)
    existing_id = health_db.save_memory(
        category="hypothesis",
        key="hyp_test",
        value=payload,
        confidence=0.5,
        source="manual",
    )
    anthropic_mock.script(
        match=lambda p: "дедупликатор" in p or "семантически" in p.lower(),
        response=json.dumps({"is_duplicate": True, "existing_id": existing_id,
                             "reason": "match по теме сна"})
    )
    import hypothesis_semantic_check as hsc
    is_dup, mid, reason = hsc.check("Sleep_deep провалился до 0.5 часа")
    assert is_dup is True
    assert mid == existing_id


@pytest.mark.parametrize("haiku, sonnet, want", [
    ((True, 7), (True, 7), (True, 7)),      # оба: дубль одной гипотезы — склейка
    ((True, 7), (False, None), (False, None)),  # разошлись — гипотеза сохраняется
    ((True, 7), (True, 9), (False, None)),  # дубль, но разных гипотез — сохраняется
    ((False, None), None, (False, None)),   # первый судья «не дубль» — второй не нужен
])
def test_duplicate_needs_two_judges_on_the_same_hypothesis(monkeypatch, haiku, sonnet, want):
    """Решение владельца 05.10: склейка — только при согласии двух судей."""
    from types import SimpleNamespace as NS
    import hypothesis_semantic_check as hsc
    answers = {"hypothesis_semantic_check._haiku_compare": haiku,
               "hypothesis_semantic_check._second_opinion": sonnet}
    asked = []

    def create(**kw):
        asked.append(kw["task"])
        dup, mid = answers[kw["task"]]
        return NS(content=[NS(type="text", text=json.dumps({"is_duplicate": dup, "existing_id": mid, "reason": "r"}))])
    monkeypatch.setattr(hsc, "get_client", lambda: NS(messages=NS(create=create)))
    monkeypatch.setattr(hsc.hai_core, "get_model", lambda role: role)
    got = hsc._haiku_compare("кандидат", [{"memory_id": 7, "status": "open", "observation": "x"}])
    assert got[:2] == want
    assert len(asked) == (1 if not haiku[0] else 2)
