"""Датчик «модель цепочки с незамеренным думанием» (15.10).

13.10 цепочка роли haiku штатно стала [claude-haiku-5-5], а профиля думания у модели не было:
она думала по умолчанию, и задачи режима «чтение» с малым лимитом остались без ответа.
Слой данных подменён (цепочки ролей, провайдер); профили и замеры — настоящие файлы methodology/.
"""
from __future__ import annotations

import pytest

import integrity_tests as it

pytestmark = pytest.mark.unit


def _run(monkeypatch, chain):
    import hai_core
    import llm_client
    monkeypatch.setattr(llm_client, "provider", lambda: "anthropic")
    monkeypatch.setattr(hai_core, "model_chain", lambda role: chain)
    said = []
    monkeypatch.setattr(it, "warn", lambda label, detail="": said.append((label, detail)))
    r = it.check_chain_models_thinking_known()
    return r, [d for l, d in said if "незамеренным думанием" in l]


def test_unknown_model_in_chain_is_named(monkeypatch):
    r, hits = _run(monkeypatch, ["claude-haiku-9-9"])
    assert r["gaps"] == 1 and hits and "claude-haiku-9-9 (" in hits[0] and "нет профиля думания" in hits[0]


def test_measured_models_are_quiet(monkeypatch):
    """Отрицательный контроль: модели с профилем и замеренным запасом — молчание.
    claude-haiku-5-5 здесь же: после записи 15.10 профиль и запас у неё есть."""
    r, hits = _run(monkeypatch, ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-4-7"])
    assert r["gaps"] == 0 and not hits


def test_thinking_model_without_reserve_is_named(monkeypatch):
    import llm_client
    monkeypatch.setattr(llm_client, "reasoning_reserve", lambda m: 0)
    r, hits = _run(monkeypatch, ["claude-sonnet-5-5"])
    assert r["gaps"] == 1 and "запас не замерен" in hits[0]
