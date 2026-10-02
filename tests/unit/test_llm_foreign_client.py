"""Чужой провайдер установки за тем же гардом (нить llm-provider, 2026-10-02): выбор
провайдера, ключ из его файла, гард ДО перевода и транспорта, ответ в форме Anthropic,
допуск роли как условие работы, страж conftest на хостах провайдеров."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import hai_core  # noqa: E402
import llm_client as lc  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "llm_translate"
_PLANTED = "sk-fake-0a1b2c3d4e5f6a7b8c9d"


def test_provider_default_and_unknown(monkeypatch):
    monkeypatch.delenv("HEALTH_LLM_PROVIDER", raising=False)
    assert lc.provider() == "anthropic"
    monkeypatch.setenv("HEALTH_LLM_PROVIDER", "nosuch")
    with pytest.raises(ValueError):
        lc.provider()


def test_api_key_reads_the_provider_file(monkeypatch, tmp_path):
    (tmp_path / "openai_key").write_text("ok-openai\n")
    monkeypatch.setattr(lc, "_KEY_FILE", tmp_path / "anthropic_key")
    assert lc.api_key("openai") == "ok-openai"


class _FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **req):
        self.calls.append(req)
        return json.loads((FIX / "openai_turn2.json").read_text(encoding="utf-8"))


class _FakeOpenAI:
    last = None

    def __init__(self, api_key):
        self.responses = _FakeResponses()
        self.models = self
        _FakeOpenAI.last = self


def _openai_install(monkeypatch, tmp_path):
    import openai
    d = tmp_path / ".health_secrets"
    d.mkdir()
    (d / "anthropic_key").write_text(_PLANTED + "\n")
    (d / "openai_key").write_text("sk-openai-test\n")
    monkeypatch.setattr(lc, "_KEY_FILE", d / "anthropic_key")
    monkeypatch.setattr(lc.secret_guard, "secrets_dir", lambda: d)
    monkeypatch.setenv("HEALTH_LLM_GUARD_LOG", str(tmp_path / "guard.log"))
    monkeypatch.setenv("HEALTH_LLM_PROVIDER", "openai")
    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)


def test_foreign_call_is_translated_and_answer_has_anthropic_shape(monkeypatch, tmp_path):
    _openai_install(monkeypatch, tmp_path)
    r = lc.guarded_client().messages.create(model="gpt-5.6-terra", max_tokens=100, system="s",
                                            messages=[{"role": "user", "content": "q"}])
    req = _FakeOpenAI.last.responses.calls[0]
    assert req["instructions"] == "s" and req["input"][0]["content"][0]["type"] == "input_text"
    assert r.stop_reason == "end_turn" and r.content[0].text and r.usage.output_tokens > 0


def test_guard_stands_before_translation_and_transport(monkeypatch, tmp_path):
    _openai_install(monkeypatch, tmp_path)
    c = lc.guarded_client()
    with pytest.raises(lc.SecretLeakBlocked):
        c.messages.create(model="m", max_tokens=1, messages=[{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t", "content": _PLANTED}]}])
    assert _FakeOpenAI.last.responses.calls == [], "секрет ушёл к чужому провайдеру"


def test_foreign_role_works_only_when_admitted(monkeypatch, tmp_path):
    monkeypatch.setenv("HEALTH_LLM_PROVIDER", "openai")
    monkeypatch.setattr(hai_core.db, "get_config", lambda k, d=None, **kw: d)
    table = tmp_path / "t.json"
    monkeypatch.setattr(hai_core, "_ADMISSION_TABLE", table)
    default = lc.profiles()["openai"]["role_defaults"]["opus"]
    with pytest.raises(hai_core.ModelNotAdmitted):
        hai_core.get_model("opus")
    table.write_text(json.dumps({"openai": {"opus": {default: {"passed": True}}}}))
    assert hai_core.get_model("opus") == default
    table.write_text(json.dumps({"openai": {"opus": {default: {"passed": False}}}}))
    with pytest.raises(hai_core.ModelNotAdmitted):
        hai_core.get_model("opus")


def test_anthropic_path_unchanged(monkeypatch):
    monkeypatch.delenv("HEALTH_LLM_PROVIDER", raising=False)
    monkeypatch.setattr(hai_core.db, "get_config", lambda k, d=None, **kw: d)
    assert hai_core.get_model("opus") == hai_core.MODEL_DEFAULTS["opus"]


def test_conftest_blocks_real_openai_and_google_hosts():
    """Позитивный контроль стража: настоящий клиент httpx к хосту провайдера отбит."""
    import httpx
    for host in ("api.openai.com", "generativelanguage.googleapis.com", "api.deepseek.com"):
        with pytest.raises(httpx.ConnectError, match="no_real_llm_providers"):
            httpx.Client().get(f"https://{host}/v1/models")


test_conftest_blocks_real_openai_and_google_hosts.llm_block_expected = True


# ── DeepSeek: точка, совместимая с Anthropic (02.10) ─────────────────────────────────────────
class _FakeAnthropicMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return "ok"


class _FakeAnthropic:
    last = None

    def __init__(self, api_key, base_url=None):
        self.api_key, self.base_url = api_key, base_url
        self.messages = _FakeAnthropicMessages()
        _FakeAnthropic.last = self


def _deepseek_install(monkeypatch, tmp_path):
    import anthropic
    d = tmp_path / ".health_secrets"
    d.mkdir()
    (d / "anthropic_key").write_text(_PLANTED + "\n")
    (d / "deepseek_key").write_text("sk-deepseek-test\n")
    monkeypatch.setattr(lc, "_KEY_FILE", d / "anthropic_key")
    monkeypatch.setattr(lc.secret_guard, "secrets_dir", lambda: d)
    monkeypatch.setenv("HEALTH_LLM_GUARD_LOG", str(tmp_path / "guard.log"))
    monkeypatch.setenv("HEALTH_LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(anthropic, "Anthropic", _FakeAnthropic)


def test_deepseek_goes_to_its_endpoint_with_thinking_off(monkeypatch, tmp_path):
    _deepseek_install(monkeypatch, tmp_path)
    c = lc.guarded_client()
    c.messages.create(model="deepseek-flash", max_tokens=5, messages=[{"role": "user", "content": "q"}])
    c.messages.create(model="deepseek-flash", max_tokens=5, thinking={"type": "enabled", "budget_tokens": 9},
                      messages=[{"role": "user", "content": "q"}])
    f = _FakeAnthropic.last
    assert f.api_key == "sk-deepseek-test" and f.base_url == lc.profiles()["deepseek"]["base_url"]
    assert f.messages.calls[0]["thinking"] == {"type": "disabled"}, "без выключенного thinking блок рассуждения съедает max_tokens"
    assert f.messages.calls[1]["thinking"]["type"] == "enabled", "явный thinking вызывающего не перезаписан"


def test_deepseek_guard_stands_before_transport(monkeypatch, tmp_path):
    _deepseek_install(monkeypatch, tmp_path)
    with pytest.raises(lc.SecretLeakBlocked):
        lc.guarded_client().messages.create(model="m", max_tokens=1, messages=[
            {"role": "user", "content": f"вот {_PLANTED}"}])
    assert _FakeAnthropic.last.messages.calls == [], "секрет ушёл к DeepSeek"


def test_deepseek_models_listed_from_models_url(monkeypatch, tmp_path):
    import httpx
    _deepseek_install(monkeypatch, tmp_path)
    seen = {}

    def _get(url, timeout, headers):
        seen.update(url=url, auth=headers["Authorization"])
        return httpx.Response(200, json={"data": [{"id": "deepseek-flash"}, {"id": "deepseek-v4-pro", "created": 0}]},
                              request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", _get)
    ids = [m.id for m in lc.guarded_client().models.list()]
    assert ids == ["deepseek-flash", "deepseek-v4-pro"]
    assert seen == {"url": "https://api.deepseek.com/models", "auth": "Bearer sk-deepseek-test"}


def test_deepseek_substituted_or_rejected_model_is_not_found(monkeypatch, tmp_path):
    """DeepSeek молча отвечает другой моделью на чужое имя (замер 02.10: claude-opus-5 →
    deepseek-v4-pro) и отвергает незнакомое кодом 400. Оба — «модели нет», не успех и не сбой."""
    from types import SimpleNamespace as NS
    _deepseek_install(monkeypatch, tmp_path)
    c = lc.guarded_client()
    inner = _FakeAnthropic.last.messages
    q = dict(max_tokens=1, messages=[{"role": "user", "content": "q"}])
    inner.create = lambda **kw: NS(model="deepseek-v4-pro")
    assert c.messages.create(model="deepseek-v4-pro", **q).model == "deepseek-v4-pro"
    with pytest.raises(lc.ModelNotServed) as ei:
        c.messages.create(model="claude-opus-5", **q)
    assert lc.is_model_not_found(ei.value)

    class _E400(Exception):
        status_code = 400

    def _reject(**kw):
        raise _E400("The supported API model names are deepseek-flash, deepseek-v4-pro")
    inner.create = _reject
    with pytest.raises(lc.ModelNotServed):
        c.messages.create(model="deepseek-v3", **q)


def test_deepseek_async_models_list_uses_async_http(monkeypatch, tmp_path):
    import asyncio
    import anthropic
    import httpx
    _deepseek_install(monkeypatch, tmp_path)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeAnthropic)
    real_async_client = httpx.AsyncClient
    requests = []
    clients = []

    def respond(req):
        requests.append(req)
        return httpx.Response(200, json={"data": [{"id": "deepseek-flash", "created": 0},
                                                 {"id": "deepseek-v4-pro", "created": 1}]})
    def async_client(**kw):
        c = real_async_client(transport=httpx.MockTransport(respond), **kw)
        clients.append(c)
        return c
    monkeypatch.setattr(httpx, "AsyncClient", async_client)
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: pytest.fail("async models.list сделал sync HTTP"))
    # Хост синтетический: conftest запрещает настоящий API даже с MockTransport.
    original_profiles = lc.profiles
    def profiles():
        prof = original_profiles()
        prof["deepseek"]["models_url"] = "https://llm.test/models"
        return prof
    monkeypatch.setattr(lc, "profiles", profiles)
    c = lc.guarded_client(async_=True, prov="deepseek")

    async def listing():
        page = await c.models.list(limit=1)
        assert [(m.id, m.created_at) for m in page] == [("deepseek-flash", "1970-01-01T00:00:00+00:00")]
        assert len(await c.models.list(limit=0)) == 2
    asyncio.run(listing())
    assert len(requests) == 2
    assert all(r.headers["Authorization"] == "Bearer sk-deepseek-test" for r in requests)
    assert all(c.is_closed for c in clients)


@pytest.mark.parametrize("prov", ["openai", "gemini"])
@pytest.mark.parametrize("async_", [False, True])
@pytest.mark.parametrize("where", ["extra_body", "extra_headers", "system", "messages", "tools"])
def test_foreign_http_guard_before_translation(monkeypatch, tmp_path, prov, async_, where):
    import asyncio
    import httpx
    d = tmp_path / "secrets"
    d.mkdir()
    (d / "planted_key").write_text(_PLANTED)
    monkeypatch.setattr(lc.secret_guard, "secrets_dir", lambda: d)
    monkeypatch.setattr(lc, "api_key", lambda *a: "fake-api-key")
    requests = []
    def respond(req):
        requests.append(req)
        if prov == "openai":
            return httpx.Response(200, json=json.loads((FIX / "openai_turn2.json").read_text()))
        return httpx.Response(200, json={"candidates": [{"content": {"role": "model",
            "parts": [{"text": "ok"}]}, "finishReason": "STOP"}], "modelVersion": "m",
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1}})
    transport = httpx.MockTransport(respond)
    sync_http, async_http = httpx.Client(transport=transport), httpx.AsyncClient(transport=transport)
    if prov == "openai":
        import openai
        from openai import OpenAI, AsyncOpenAI
        def sync_ctor(**kw):
            return OpenAI(**kw, base_url="https://llm.test/v1", http_client=sync_http, max_retries=0)
        def async_ctor(**kw):
            return AsyncOpenAI(**kw, base_url="https://llm.test/v1", http_client=async_http, max_retries=0)
        monkeypatch.setattr(openai, "OpenAI", sync_ctor)
        monkeypatch.setattr(openai, "AsyncOpenAI", async_ctor)
    else:
        from google import genai
        ctor = genai.Client
        monkeypatch.setattr(genai, "Client", lambda **kw: ctor(**kw, http_options={
            "base_url": "https://llm.test", "httpx_client": sync_http, "httpx_async_client": async_http}))
    c = lc.guarded_client(prov=prov, async_=async_)
    kw = dict(model="m", max_tokens=1, messages=[{"role": "user", "content": "q"}])
    if where == "messages":
        kw[where][0]["content"] = _PLANTED
    elif where == "tools":
        kw[where] = [{"name": "x", "description": _PLANTED, "input_schema": {"type": "object"}}]
    else:
        kw[where] = _PLANTED if where == "system" else {"note": _PLANTED}
    try:
        with pytest.raises(lc.SecretLeakBlocked):
            if async_:
                async def call():
                    return await c.messages.create(**kw)
                asyncio.run(call())
            else:
                c.messages.create(**kw)
        assert requests == []
        # Негативный контроль: чистый запрос обязан достичь настоящего SDK/MockTransport.
        clean = dict(model="m", max_tokens=1, messages=[{"role": "user", "content": "q"}])
        if async_:
            async def clean_call():
                return await c.messages.create(**clean)
            asyncio.run(clean_call())
        else:
            c.messages.create(**clean)
        assert len(requests) == 1
        for path in ("beta", "responses", "chat", "aio", "unknown"):
            with pytest.raises(AttributeError, match="guard|гард"):
                getattr(c, path)
        for path in ("batches", "with_raw_response", "with_streaming_response", "unknown"):
            with pytest.raises(AttributeError, match="guard|гард"):
                getattr(c.messages, path)
        # Непереведённые методы всё равно проверяют аргументы до отказа SDK.
        for method in ("count_tokens", "stream"):
            with pytest.raises(lc.SecretLeakBlocked):
                getattr(c.messages, method)(**kw)
        closed = []
        if prov == "gemini":
            sdk = c._inner._sdk
            real_close, real_aclose = sdk.close, sdk.aio.aclose
            def close():
                closed.append("sync")
                real_close()
            async def aclose():
                closed.append("async")
                await real_aclose()
            monkeypatch.setattr(sdk, "close", close)
            monkeypatch.setattr(sdk.aio, "aclose", aclose)
        if async_:
            asyncio.run(c.close())
        else:
            c.close()
        if prov == "openai":
            assert sync_http.is_closed
            if async_:
                assert async_http.is_closed
        else:
            assert closed == (["async", "sync"] if async_ else ["sync"])
        # Gemini оставляет переданные пользователем httpx-клиенты их владельцу.
        # Проверяем вызов SDK.close отдельно; HTTP-клиенты закроет finally.
    finally:
        sync_http.close()
        asyncio.run(async_http.aclose())
