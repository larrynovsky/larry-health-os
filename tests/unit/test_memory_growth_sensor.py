"""
tests/unit/test_memory_growth_sensor.py — Ф4.3 tripwire (5 июля).

RST: тестируем РИСК, не happy-path. Риск датчика роста — в WHERE-клаузе:
считать не тот класс (включить fact/preference → ложная тревога на здоровом росте)
или не сработать вовсе. Проверяем ровно это.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _seed(mem_class: str, n: int) -> None:
    import memory_facts_db as mf
    for i in range(n):
        mf.save_fact(mem_class, f"значение {i}", key=f"{mem_class}_k{i}", subject="self")


def test_warns_when_episodic_over_threshold(db, monkeypatch):
    import integrity_tests as it
    monkeypatch.setattr(it, "MEMORY_EPISODIC_GROWTH_WARN", 2)
    _seed("state", 2)
    _seed("question", 2)          # 4 эпизодических > порог 2
    before = len(it._warnings)
    it.check_memory_facts_growth()
    assert any("растёт" in w[0] for w in it._warnings[before:]), "должен сработать на эпизодических"


def test_ignores_facts_and_preferences(db, monkeypatch):
    """fact/preference растут легитимно и не истекают по времени — датчик их НЕ считает."""
    import integrity_tests as it
    monkeypatch.setattr(it, "MEMORY_EPISODIC_GROWTH_WARN", 2)
    _seed("fact", 10)
    _seed("preference", 10)       # 20 неэпизодических, но порог по эпизодическим = 0
    before = len(it._warnings)
    it.check_memory_facts_growth()
    assert not any("растёт" in w[0] for w in it._warnings[before:]), \
        "рост fact/preference не должен тревожить (не эпизодические)"


def test_quiet_below_threshold(db, monkeypatch):
    import integrity_tests as it
    monkeypatch.setattr(it, "MEMORY_EPISODIC_GROWTH_WARN", 100)
    _seed("state", 3)
    before = len(it._warnings)
    it.check_memory_facts_growth()
    assert not any("растёт" in w[0] for w in it._warnings[before:])
