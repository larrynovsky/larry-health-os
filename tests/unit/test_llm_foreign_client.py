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
    for host in ("api.openai.com", "generativelanguage.googleapis.com"):
        with pytest.raises(httpx.ConnectError, match="no_real_llm_providers"):
            httpx.Client().get(f"https://{host}/v1/models")


test_conftest_blocks_real_openai_and_google_hosts.llm_block_expected = True
