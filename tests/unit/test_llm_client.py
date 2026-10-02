"""Характеризация llm_client — гард на исходящем тексте по конструкции."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import llm_client as lc   # noqa: E402


class _Inner:
    """Двойник клиента: запоминает, дошёл ли вызов."""
    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return "sent"


def _client(monkeypatch, hits):
    monkeypatch.setattr(lc.secret_guard, "find_secret_values", lambda *a, **k: hits)
    inner = _Inner()
    return lc._GuardedClient(inner), inner


def test_clean_text_passes(monkeypatch):
    c, inner = _client(monkeypatch, [])
    assert c.messages.create(messages=[{"role": "user", "content": "как спалось"}]) == "sent"
    assert len(inner.calls) == 1


def test_secret_in_text_blocks_and_never_echoes_value(monkeypatch):
    c, inner = _client(monkeypatch, ["health_secrets/anthropic_key"])
    with pytest.raises(lc.SecretLeakBlocked) as e:
        c.messages.create(messages=[{"role": "user", "content": "sk-ant-SECRETVALUE"}])
    assert not inner.calls, "вызов дошёл до API несмотря на находку"
    assert "SECRETVALUE" not in str(e.value), "гард сам стал каналом утечки"
    assert "anthropic_key" in str(e.value)


def test_blind_guard_is_distinct_from_finding(monkeypatch):
    """Слепота ≠ находка. Оба блокируют, но лечатся противоположно (R1)."""
    c, inner = _client(monkeypatch, ["!secret_guard не отработал: RuntimeError"])
    with pytest.raises(lc.GuardUnavailable):
        c.messages.create(messages=[{"role": "user", "content": "текст"}])
    assert not inner.calls


def test_image_bytes_not_scanned(monkeypatch):
    """Картинки в скан не идут: секрет в пикселях текстом не ищется."""
    seen = {}
    monkeypatch.setattr(lc.secret_guard, "find_secret_values",
                        lambda t, *a, **k: seen.setdefault("t", t) and [] or [])
    c = lc._GuardedClient(_Inner())
    c.messages.create(messages=[{"role": "user", "content": [
        {"type": "text", "text": "разбери бланк"},
        {"type": "image", "source": {"type": "base64", "data": "AAAABBBBCCCC" * 50}},
    ]}])
    assert "разбери бланк" in seen["t"]
    assert "AAAABBBBCCCC" not in seen["t"]


def test_system_prompt_is_scanned(monkeypatch):
    """System — тоже исходящий текст; забыть его значит оставить дыру."""
    seen = {}
    monkeypatch.setattr(lc.secret_guard, "find_secret_values",
                        lambda t, *a, **k: seen.setdefault("t", t) and [] or [])
    lc._GuardedClient(_Inner()).messages.create(
        messages=[{"role": "user", "content": "q"}], system="ты врач")
    assert "ты врач" in seen["t"]


def test_negative_control_guard_is_load_bearing(monkeypatch):
    """НЕГАТИВНЫЙ КОНТРОЛЬ: обезврежен гард — блокировка исчезает.

    Значит именно он держит защиту, а не структура обёртки."""
    c, inner = _client(monkeypatch, [])          # гард всегда «чисто» = обезврежен
    c.messages.create(messages=[{"role": "user", "content": "sk-ant-SECRETVALUE"}])
    assert inner.calls, "без гарда вызов обязан проходить — иначе тест проверяет не гард"


def test_guard_outgoing_is_callable_directly(monkeypatch):
    """Публичный вход без клиента — им пользуется doc_agent до конструирования."""
    monkeypatch.setattr(lc.secret_guard, "find_secret_values", lambda *a, **k: [])
    assert lc.guard_outgoing([{"role": "user", "content": "чисто"}]) is None
    monkeypatch.setattr(lc.secret_guard, "find_secret_values",
                        lambda *a, **k: ["health_secrets/oura_token"])
    with pytest.raises(lc.SecretLeakBlocked):
        lc.guard_outgoing([{"role": "user", "content": "тут секрет"}])


def test_guarded_client_builds_via_public_entry(monkeypatch):
    """guarded_client() — публичный вход; клиент не конструируется по-настоящему."""
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _Inner(), raising=False)
    monkeypatch.setattr(lc, "api_key", lambda: "fake")
    monkeypatch.setattr(lc.secret_guard, "find_secret_values", lambda *a, **k: [])
    c = lc.guarded_client()
    assert c.messages.create(messages=[{"role": "user", "content": "q"}]) == "sent"


def test_unknown_attrs_pass_through(monkeypatch):
    c, inner = _client(monkeypatch, [])
    assert c.messages._inner is inner


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_journal_writes_to_redirected_path_not_prod(monkeypatch, tmp_path):
    """Журнал блокировок пишется — но по пути из env, не в боевой logs/ (§20).
    Негативный контроль отравления: боевой файл после блока не растёт."""
    import os
    from pathlib import Path
    redirected = Path(os.environ["HEALTH_LLM_GUARD_LOG"])   # выставлен autouse-фикстурой
    prod = Path(lc.__file__).parent / "logs" / "llm_guard_blocks.log"
    before = prod.stat().st_size if prod.exists() else 0
    monkeypatch.setattr(lc.secret_guard, "find_secret_values",
                        lambda *a, **k: ["health_secrets/oura_token"])
    with pytest.raises(lc.SecretLeakBlocked):
        lc.guard_outgoing([{"role": "user", "content": "тут секрет"}])
    assert redirected.exists() and "SECRET_FOUND" in redirected.read_text(encoding="utf-8")
    after = prod.stat().st_size if prod.exists() else 0
    assert after == before, "блок из теста дописал БОЕВОЙ журнал — отравление вернулось"


# ── 2026-10-01, BL-SECRETS-GUARD-GAPS-1: исходящий текст вне блоков type=text ──
# Проба Кодекса 01.10 (по зеркалу 76717c0): секрет в tool_result → SENT, 1 вызов
# транспорта. Тесты ниже идут через НАСТОЯЩИЙ secret_guard с подложенным каталогом
# секретов — мок find_secret_values скрыл бы ровно то, что извлекается из запроса.
_PLANTED = "sk-fake-0a1b2c3d4e5f6a7b8c9d"


def _real_guard_client(monkeypatch, tmp_path):
    d = tmp_path / ".health_secrets"
    d.mkdir()
    (d / "anthropic_key").write_text(_PLANTED + "\n")
    monkeypatch.setattr(lc.secret_guard, "secrets_dir", lambda: d)
    monkeypatch.setenv("HEALTH_LLM_GUARD_LOG", str(tmp_path / "guard.log"))
    inner = _Inner()
    return lc._GuardedClient(inner), inner


class _SdkBlock:
    """Двойник pydantic-блока SDK: история с ним уходит обратно в API (hai_chat)."""
    def __init__(self, **kw):
        self._kw = kw

    def model_dump(self):
        return dict(self._kw)


@pytest.mark.parametrize("where, kw", [
    ("tool_result", dict(messages=[{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": f"файл: {_PLANTED}"}]}])),
    ("tool_result_blocks", dict(messages=[{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1",
         "content": [{"type": "text", "text": _PLANTED}]}]}])),
    ("tool_use_input", dict(messages=[{"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "read", "input": {"q": _PLANTED}}]}])),
    ("tools_description", dict(messages=[{"role": "user", "content": "q"}],
                               tools=[{"name": "x", "description": f"ключ {_PLANTED}",
                                       "input_schema": {"type": "object"}}])),
    ("sdk_object_block", dict(messages=[{"role": "assistant", "content": [
        _SdkBlock(type="tool_use", id="t1", name="read", input={"q": _PLANTED})]}])),
])
def test_secret_outside_text_blocks_is_blocked(monkeypatch, tmp_path, where, kw):
    c, inner = _real_guard_client(monkeypatch, tmp_path)
    with pytest.raises(lc.SecretLeakBlocked):
        c.messages.create(model="m", max_tokens=1, **kw)
    assert not inner.calls, f"{where}: секрет ушёл в транспорт"


def test_stream_is_guarded_too(monkeypatch, tmp_path):
    c, inner = _real_guard_client(monkeypatch, tmp_path)
    inner.stream = lambda **kw: inner.calls.append(kw)
    with pytest.raises(lc.SecretLeakBlocked):
        c.messages.stream(messages=[{"role": "user", "content": _PLANTED}])
    assert not inner.calls


def test_base64_media_still_not_scanned_real_guard(monkeypatch, tmp_path):
    """Граница названа: base64-данные медиа не сканируются — и это единственное исключение."""
    c, inner = _real_guard_client(monkeypatch, tmp_path)
    c.messages.create(messages=[{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _PLANTED}},
        {"type": "text", "text": "разбери"}]}])
    assert len(inner.calls) == 1


@pytest.fixture(params=[("anthropic", False), ("anthropic", True),
                        ("deepseek", False), ("deepseek", True)])
def http_guard_client(request, monkeypatch, tmp_path):
    """Настоящий SDK и secret_guard; единственная подмена — HTTP-транспорт."""
    import anthropic
    import httpx
    from anthropic._client import Anthropic, AsyncAnthropic
    prov, async_ = request.param
    d = tmp_path / "secrets"
    d.mkdir()
    (d / "planted_key").write_text(_PLANTED)
    monkeypatch.setattr(lc.secret_guard, "secrets_dir", lambda: d)
    monkeypatch.setattr(lc, "api_key", lambda *a: "fake-api-key")
    requests = []

    def respond(req):
        requests.append(req)
        if req.url.path.endswith("count_tokens"):
            return httpx.Response(200, json={"input_tokens": 7})
        if req.url.path.endswith("models"):
            return httpx.Response(200, json={"data": [{"id": "m", "type": "model",
                "display_name": "M", "created_at": "2026-10-02T00:00:00Z"}], "has_more": False})
        return httpx.Response(200, json={"id": "msg_test", "type": "message", "role": "assistant",
            "model": "m", "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1}})

    transport = httpx.MockTransport(respond)
    http = (httpx.AsyncClient if async_ else httpx.Client)(transport=transport)
    ctor = AsyncAnthropic if async_ else Anthropic
    sdk = ctor(api_key="fake-api-key", base_url="https://llm.test", http_client=http, max_retries=0)
    monkeypatch.setattr(anthropic, "AsyncAnthropic" if async_ else "Anthropic", lambda **kw: sdk)
    c = lc.guarded_client(async_=async_, prov=prov)
    yield c, requests, async_
    if async_:
        import asyncio
        asyncio.run(sdk.close())
    else:
        sdk.close()


def _http_message_call(c, method, kw, async_):
    import asyncio
    import inspect
    if method == "create_stream":
        method, kw = "create", {**kw, "stream": True}
    if method == "count_tokens":
        kw = {k: v for k, v in kw.items() if k != "max_tokens"}
    if async_:
        async def call():
            if method == "stream":
                async with c.messages.stream(**kw):
                    pass
            else:
                r = getattr(c.messages, method)(**kw)
                r = await r if inspect.isawaitable(r) else r
                if kw.get("stream"):
                    await r.close()
                return r
        return asyncio.run(call())
    if method == "stream":
        with c.messages.stream(**kw):
            pass
    else:
        r = getattr(c.messages, method)(**kw)
        if kw.get("stream"):
            r.close()
        return r


@pytest.mark.parametrize("method", ["create", "create_stream", "stream", "count_tokens"])
@pytest.mark.parametrize("where", ["messages", "system", "tools", "extra_body", "extra_headers",
                                   "extra_query", "metadata", "stop_sequences"])
def test_http_all_message_arguments_guarded(http_guard_client, method, where):
    c, requests, async_ = http_guard_client
    kw = dict(model="m", max_tokens=1, messages=[{"role": "user", "content": "q"}])
    if where == "messages":
        kw[where][0]["content"] = _PLANTED
    elif where == "tools":
        kw[where] = [{"name": "x", "description": _PLANTED, "input_schema": {"type": "object"}}]
    elif where in ("extra_body", "extra_headers", "extra_query"):
        kw[where] = {"note": _PLANTED}
    elif where == "metadata":
        kw[where] = {"user_id": _PLANTED}
    elif where == "stop_sequences":
        kw[where] = [_PLANTED]
    else:
        kw[where] = _PLANTED
    error = None
    try:
        _http_message_call(c, method, kw, async_)
    except Exception as e:
        error = e
    leaked = sum(_PLANTED.encode() in r.content for r in requests)
    assert requests == [], f"{method}/{where}: HTTP requests={len(requests)}, bodies with planted secret={leaked}"
    assert isinstance(error, lc.SecretLeakBlocked)


@pytest.mark.parametrize("path", ["messages.batches", "messages.with_raw_response",
    "messages.with_streaming_response", "messages.unknown", "beta", "completions",
    "post", "get", "copy", "unknown"])
def test_http_unlisted_sdk_attributes_rejected(http_guard_client, path):
    c, requests, _ = http_guard_client
    obj = c
    with pytest.raises(AttributeError, match="guard|гард"):
        for name in path.split("."):
            obj = getattr(obj, name)
    assert requests == []


def test_http_with_options_preserves_guard(http_guard_client):
    c, requests, async_ = http_guard_client
    clone = c.with_options(timeout=5)
    error = None
    try:
        _http_message_call(clone, "create", dict(model="m", max_tokens=1,
            messages=[{"role": "user", "content": _PLANTED}]), async_)
    except Exception as e:
        error = e
    leaked = sum(_PLANTED.encode() in r.content for r in requests)
    assert requests == [], f"with_options: HTTP requests={len(requests)}, bodies with planted secret={leaked}"
    assert isinstance(error, lc.SecretLeakBlocked)
    assert clone.messages.create != c.messages.create
    _http_message_call(clone, "create", dict(model="m", max_tokens=1,
        messages=[{"role": "user", "content": "q"}]), async_)
    assert len(requests) == 1


@pytest.mark.parametrize("method", ["create", "create_stream", "stream", "count_tokens"])
def test_http_clean_messages_and_base64_reach_transport(http_guard_client, method):
    c, requests, async_ = http_guard_client
    kw = dict(model="m", max_tokens=1, messages=[{"role": "user", "content": "q"}],
        extra_body={"content": [{"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": _PLANTED}}]})
    result = _http_message_call(c, method, kw, async_)
    if method == "count_tokens":
        assert result.input_tokens == 7
    assert len(requests) == 1
    assert _PLANTED.encode() in requests[0].content


@pytest.mark.parametrize("http_guard_client", [("anthropic", False), ("anthropic", True)], indirect=True)
def test_anthropic_async_models_page_preserved(http_guard_client):
    import asyncio
    c, requests, async_ = http_guard_client
    if async_:
        async def listing():
            page = await c.models.list(limit=100)
            assert page.data[0].id == "m"
            return [m.id async for m in page]
        assert asyncio.run(listing()) == ["m"]
    else:
        assert [m.id for m in c.models.list(limit=100)] == ["m"]
    assert len(requests) == 1


@pytest.mark.parametrize("http_guard_client", [("anthropic", False), ("anthropic", True)], indirect=True)
@pytest.mark.parametrize("route", ["batches", "raw", "streaming_raw", "beta", "completions", "copy"])
def test_http_sdk_escape_attempt_never_sends_secret(http_guard_client, route):
    """При откате исключение SDK после отправки НЕ делает тест зелёным."""
    import asyncio
    import inspect
    c, requests, async_ = http_guard_client
    kw = dict(model="m", max_tokens=1, messages=[{"role": "user", "content": _PLANTED}])
    error = None

    def attempt():
        if route == "batches":
            return c.messages.batches.create(requests=[{"custom_id": "x", "params": kw}])
        if route == "raw":
            return c.messages.with_raw_response.create(**kw)
        if route == "streaming_raw":
            return c.messages.with_streaming_response.create(**kw)
        if route == "beta":
            return c.beta.messages.create(**kw)
        if route == "completions":
            return c.completions.create(model="m", max_tokens_to_sample=1, prompt=_PLANTED)
        return c.copy().messages.create(**kw)

    try:
        if async_:
            async def call():
                r = attempt()
                if hasattr(r, "__aenter__"):
                    async with r:
                        pass
                elif inspect.isawaitable(r):
                    await r
            asyncio.run(call())
        else:
            r = attempt()
            if hasattr(r, "__enter__"):
                with r:
                    pass
    except Exception as e:
        error = e
    leaked = sum(_PLANTED.encode() in r.content for r in requests)
    assert requests == [], f"{route}: HTTP requests={len(requests)}, bodies with planted secret={leaked}"
    assert isinstance(error, AttributeError) and "гард" in str(error)


@pytest.mark.parametrize("http_guard_client", [("anthropic", False), ("anthropic", True)], indirect=True)
@pytest.mark.parametrize("method", ["list", "retrieve"])
@pytest.mark.parametrize("field", ["extra_body", "extra_headers"])
def test_http_models_extensions_guarded(http_guard_client, method, field):
    c, requests, _ = http_guard_client
    with pytest.raises(lc.SecretLeakBlocked):
        args = ("m",) if method == "retrieve" else ()
        getattr(c.models, method)(*args, **{field: {"note": _PLANTED}})
    assert requests == []
    with pytest.raises(AttributeError, match="гард"):
        c.models.with_raw_response


def test_http_with_options_headers_guarded(http_guard_client):
    c, requests, _ = http_guard_client
    with pytest.raises(lc.SecretLeakBlocked):
        c.with_options(default_headers={"x-note": _PLANTED})
    assert requests == []


def test_http_client_close_preserved(http_guard_client):
    import asyncio
    c, _, async_ = http_guard_client
    if async_:
        asyncio.run(c.close())
    else:
        c.close()
    sdk = c._inner._sdk if isinstance(c._inner, lc._CompatClient) else c._inner
    assert sdk.is_closed()


@pytest.mark.parametrize("where", ["extra_body", "extra_headers", "extra_query"])
def test_http_secret_in_field_name_is_guarded(http_guard_client, where):
    c, requests, async_ = http_guard_client
    kw = dict(model="m", max_tokens=1, messages=[{"role": "user", "content": "q"}],
              **{where: {_PLANTED: "q"}})
    with pytest.raises(lc.SecretLeakBlocked):
        _http_message_call(c, "create", kw, async_)
    assert requests == []


@pytest.mark.parametrize("where", ["extra_headers", "extra_query"])
def test_http_sdk_mapping_and_duplicate_values_guarded(http_guard_client, where):
    import httpx
    c, requests, async_ = http_guard_client
    ctor = httpx.Headers if where == "extra_headers" else httpx.QueryParams
    # QueryParams.items() видит только первое значение: игла — во втором.
    kw = dict(model="m", max_tokens=1, messages=[{"role": "user", "content": "q"}],
              **{where: ctor([("note", "q"), ("note", _PLANTED)])})
    error = None
    try:
        _http_message_call(c, "create", kw, async_)
    except Exception as e:
        error = e
    assert requests == [], f"{where}: SDK Mapping пропущен гардом"
    assert isinstance(error, lc.SecretLeakBlocked)


@pytest.mark.parametrize("where", ["extra_body", "extra_headers"])
def test_http_base64_marker_outside_media_does_not_hide_secret(http_guard_client, where):
    c, requests, async_ = http_guard_client
    kw = dict(model="m", max_tokens=1, messages=[{"role": "user", "content": "q"}],
              **{where: {"type": "base64", "data": _PLANTED}})
    with pytest.raises(lc.SecretLeakBlocked):
        _http_message_call(c, "create", kw, async_)
    assert requests == []
