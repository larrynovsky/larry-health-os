"""
tests/unit/test_memory_facts_invariants.py — E-датчик (долг из аудита, 5 июля).

Рантайм-инвариант memory_facts: no-double-active-key (битый upsert = две активных
версии одного ключа = порча). RST: тест должен ПАДАТЬ на нарушении, не только
проходить на чистом.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_double_active_key_detected(db):
    """Вставляем в ОБХОД save_fact две активных строки на один ключ → датчик FAIL."""
    import integrity_tests as it
    import health_db
    with health_db.get_conn() as c:
        for _ in range(2):
            c.execute(
                "INSERT INTO memory_facts (mem_class, key, value, subject, active) "
                "VALUES ('fact','dup_key','v','self',1)"
            )
    with pytest.raises(AssertionError, match="double-active-key"):
        it.check_memory_facts_invariants()


def test_clean_state_passes(db):
    import integrity_tests as it
    import memory_facts_db as mf
    mf.save_fact("fact", "нормальный факт", key="k_ok")
    assert it.check_memory_facts_invariants() == {"ok": True}


def test_upsert_does_not_create_double_active(db):
    """save_fact дважды по одному ключу → одна активная (supersede), датчик чист."""
    import integrity_tests as it
    import memory_facts_db as mf
    mf.save_fact("fact", "v1", key="k_up")
    mf.save_fact("fact", "v2", key="k_up")
    assert it.check_memory_facts_invariants() == {"ok": True}
