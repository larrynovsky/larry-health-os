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
