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
