"""Unit-тест на fixture `anthropic_mock`."""
from __future__ import annotations

import asyncio
import json
import re

import pytest

pytestmark = pytest.mark.unit


def test_default_response(anthropic_mock):
    import anthropic
    client = anthropic.Anthropic(api_key="fake")
    resp = client.messages.create(
        model="claude-haiku-4-5",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert resp.content[0].text == anthropic_mock.default_text
    assert anthropic_mock.calls
    assert anthropic_mock.calls[0]["model"] == "claude-haiku-4-5"


def test_script_substring_match(anthropic_mock):
    anthropic_mock.script(match="JSON-экстракция", response='{"x": 1}')

    import anthropic
    client = anthropic.Anthropic(api_key="fake")
    resp = client.messages.create(
        model="claude-haiku-4-5",
        messages=[{"role": "user", "content": "Сделай JSON-экстракция этих данных"}],
    )
    assert resp.content[0].text == '{"x": 1}'
    parsed = json.loads(resp.content[0].text)
    assert parsed["x"] == 1


def test_script_regex_match(anthropic_mock):
    pattern = re.compile(r"триаж\s+метрик", re.IGNORECASE)
    anthropic_mock.script(match=pattern, response="OK")

    import anthropic
    client = anthropic.Anthropic(api_key="fake")
    resp = client.messages.create(
        model="claude-haiku-4-5",
        messages=[{"role": "user", "content": "Триаж Метрик за 7 дней"}],
    )
    assert resp.content[0].text == "OK"


def test_script_callable_match(anthropic_mock):
    anthropic_mock.script(
        match=lambda p: "Council" in p and "Round B" in p,
        response="round-b-synthesized",
    )

    import anthropic
    client = anthropic.Anthropic(api_key="fake")
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": "Council coordinator: synthesize Round B"}],
    )
    assert resp.content[0].text == "round-b-synthesized"


def test_script_dict_serialized(anthropic_mock):
    anthropic_mock.script(match="extract", response={"hrv": 25, "sleep": 7.5})

    import anthropic
    client = anthropic.Anthropic(api_key="fake")
    resp = client.messages.create(
        model="claude-haiku-4-5",
        messages=[{"role": "user", "content": "extract metrics"}],
    )
    parsed = json.loads(resp.content[0].text)
    assert parsed["hrv"] == 25


def test_async_anthropic(anthropic_mock):
    """AsyncAnthropic тоже подменяется (Council использует именно его)."""
    anthropic_mock.script(match="async", response="async-ok")

    async def call():
        import anthropic
        client = anthropic.AsyncAnthropic(api_key="fake")
        return await client.messages.create(
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "async test"}],
        )

    resp = asyncio.run(call())
    assert resp.content[0].text == "async-ok"


def test_strict_mode_raises_on_unscripted(anthropic_mock):
    anthropic_mock.strict = True

    import anthropic
    client = anthropic.Anthropic(api_key="fake")
    with pytest.raises(AssertionError, match="no script matched"):
        client.messages.create(
            model="claude-haiku-4-5",
            messages=[{"role": "user", "content": "unscripted prompt"}],
        )


def test_calls_log(anthropic_mock):
    anthropic_mock.script(match="A", response="a")
    anthropic_mock.script(match="B", response="b")

    import anthropic
    client = anthropic.Anthropic(api_key="fake")
    client.messages.create(model="m1", messages=[{"role": "user", "content": "A"}])
    client.messages.create(model="m2", messages=[{"role": "user", "content": "B"}])
    client.messages.create(model="m3", messages=[{"role": "user", "content": "C"}])  # default

    assert len(anthropic_mock.calls) == 3
    assert [c["model"] for c in anthropic_mock.calls] == ["m1", "m2", "m3"]
    assert len(anthropic_mock.used_scripts()) == 2


def test_system_prompt_included_in_match(anthropic_mock):
    """system prompt тоже идёт в matching."""
    anthropic_mock.script(match="ФОРМАТ ОТВЕТА", response="formatted")

    import anthropic
    client = anthropic.Anthropic(api_key="fake")
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        system="ФОРМАТ ОТВЕТА — обязательно русский",
        messages=[{"role": "user", "content": "что у меня с ВСР"}],
    )
    assert resp.content[0].text == "formatted"
