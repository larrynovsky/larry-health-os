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
