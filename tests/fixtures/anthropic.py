"""
tests/fixtures/anthropic.py — scripted mock для Anthropic SDK.

**Что делает:**
- Подменяет `anthropic.Anthropic` и `anthropic.AsyncAnthropic` на mock-клиенты,
  которые возвращают заранее заскриптованные ответы по prompt-pattern.
- Логирует все вызовы в `mock.calls` (для проверок в тестах).
- НЕ делает реальных HTTP-вызовов — все ответы локальные.

**Не покрывает:**
- streaming (текущие тесты не нужны).
- tool_use (можно добавить когда понадобится).

**Использование:**

    def test_gp_daily_uses_haiku_for_arbiter(anthropic_mock):
        anthropic_mock.script(
            match=lambda prompt: "JSON-экстракция" in prompt,
            response='{"hrv_trend": "stable", "tasks": []}'
        )
        # ... запускаем код, который зовёт anthropic.Anthropic().messages.create(...)
        assert any("haiku" in c["model"] for c in anthropic_mock.calls)

**Default response:**
Если ни один script не подошёл — возвращается `_DEFAULT_RESPONSE_TEXT`
(заглушка). Тест может это явно проверить или поставить strict-режим:
`anthropic_mock.strict = True` → бросать AssertionError при unscripted call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Union

import pytest


_DEFAULT_RESPONSE_TEXT = "[mock] no script matched"


# ── Анатомия Anthropic Response ──────────────────────────────────────────────

@dataclass
class _MockContentBlock:
    type: str = "text"
    text: str = ""


@dataclass
class _MockResponse:
    """Совместимый с anthropic.types.Message минимум."""
    content: List[_MockContentBlock]
    id: str = "msg_mock"
    model: str = "claude-mock"
    role: str = "assistant"
    stop_reason: str = "end_turn"
    stop_sequence: Optional[str] = None
    type: str = "message"
    usage: Any = None


# ── Script entry ─────────────────────────────────────────────────────────────

ScriptMatcher = Union[str, re.Pattern, Callable[[str], bool]]


@dataclass
class _Script:
    matcher: ScriptMatcher
    response_text: str
    times_used: int = 0

    def matches(self, prompt: str) -> bool:
        if isinstance(self.matcher, str):
            return self.matcher in prompt
        if isinstance(self.matcher, re.Pattern):
            return bool(self.matcher.search(prompt))
        if callable(self.matcher):
            return bool(self.matcher(prompt))
        return False


# ── Главный mock ────────────────────────────────────────────────────────────

@dataclass
class AnthropicMock:
    scripts: List[_Script] = field(default_factory=list)
    calls: List[dict] = field(default_factory=list)
    strict: bool = False
    default_text: str = _DEFAULT_RESPONSE_TEXT

    def script(
        self,
        match: ScriptMatcher,
        response: Union[str, dict],
    ) -> None:
        """Зарегистрировать ответ для конкретного prompt-pattern.

        match: substring (str), re.Pattern или callable(prompt) → bool.
        response: текст или dict (будет json.dumps).
        """
        if isinstance(response, dict):
            import json
            response = json.dumps(response, ensure_ascii=False)
        self.scripts.append(_Script(matcher=match, response_text=response))

    def reset(self) -> None:
        self.scripts.clear()
        self.calls.clear()

    def _serialize_messages(self, messages: List[dict]) -> str:
        """Для matching — все user-сообщения склеены в один prompt."""
        parts = []
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
            parts.append(f"[{m.get('role', '?')}] {content}")
        return "\n".join(parts)

    def _resolve_response(self, prompt: str, model: str, **kwargs) -> _MockResponse:
        self.calls.append({
            "model": model,
            "prompt": prompt,
            "kwargs": kwargs,
        })
        for s in self.scripts:
            if s.matches(prompt):
                s.times_used += 1
                return _MockResponse(content=[_MockContentBlock(text=s.response_text)],
                                      model=model)
        if self.strict:
            raise AssertionError(
                f"AnthropicMock.strict: no script matched. Prompt:\n{prompt[:300]}..."
            )
        return _MockResponse(content=[_MockContentBlock(text=self.default_text)],
                              model=model)

    def used_scripts(self) -> List[_Script]:
        """Полезно для проверки: какие script-ы реально стрельнули."""
        return [s for s in self.scripts if s.times_used > 0]


# ── Fake-классы для подмены anthropic.Anthropic / AsyncAnthropic ─────────────

class _FakeMessages:
    def __init__(self, mock: AnthropicMock):
        self._mock = mock

    def create(self, *, messages: list, model: str = "claude-mock",
               system: str = "", **kwargs) -> _MockResponse:
        prompt = (system + "\n" + self._mock._serialize_messages(messages)).strip()
        return self._mock._resolve_response(prompt, model, **kwargs)


class _FakeAsyncMessages:
    def __init__(self, mock: AnthropicMock):
        self._mock = mock

    async def create(self, *, messages: list, model: str = "claude-mock",
                     system: str = "", **kwargs) -> _MockResponse:
        prompt = (system + "\n" + self._mock._serialize_messages(messages)).strip()
        return self._mock._resolve_response(prompt, model, **kwargs)


class _FakeAnthropicClient:
    def __init__(self, mock: AnthropicMock):
        self.messages = _FakeMessages(mock)


class _FakeAsyncAnthropicClient:
    def __init__(self, mock: AnthropicMock):
        self.messages = _FakeAsyncMessages(mock)


# ── Fixture ──────────────────────────────────────────────────────────────────

@pytest.fixture
def anthropic_mock(monkeypatch: pytest.MonkeyPatch) -> AnthropicMock:
    """
    Подменяет `anthropic.Anthropic(...)` и `anthropic.AsyncAnthropic(...)`
    на fake-клиенты, направленные в общий `AnthropicMock`.

    Тест использует `mock.script(...)` для регистрации ответов; реальный код,
    который делает `anthropic.Anthropic(api_key=...).messages.create(...)`,
    получит mock-response.
    """
    mock = AnthropicMock()

    import anthropic

    def _fake_anthropic(*args, **kwargs):
        return _FakeAnthropicClient(mock)

    def _fake_async_anthropic(*args, **kwargs):
        return _FakeAsyncAnthropicClient(mock)

    monkeypatch.setattr(anthropic, "Anthropic", _fake_anthropic)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _fake_async_anthropic)
    # Ключ тоже подменяется: код зовёт клиента как anthropic.Anthropic(api_key=llm_client.api_key()),
    # а api_key() читает файл ~/.health_secrets/anthropic_key. Без этой строки заглушка
    # работала только там, где на диске лежит настоящий ключ владельца — 12 тестов были
    # красными в чистой копии и зелёными на Studio (§20; репетиция экспорта 2026-09-24).
    import llm_client
    monkeypatch.setattr(llm_client, "api_key", lambda: "sk-test-anthropic-mock")

    return mock
