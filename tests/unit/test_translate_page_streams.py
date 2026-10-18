"""Перевод страницы идёт ПОТОКОМ (08.10): обычный вызов на 8000 токенов ответа сервер
рвёт без ответа через ~4–5 минут, и большие страницы не переводились никогда."""
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

import doc_agent
import llm_client


class _Messages:
    def __init__(self):
        self.streamed = 0

    def create(self, **kw):
        raise AssertionError("перевод ушёл обычным вызовом, а не потоком")

    def stream(self, **kw):
        outer = self

        class _S:
            def __enter__(self):
                outer.streamed += 1
                return self

            def __exit__(self, *a):
                return False

            def get_final_message(self):
                return SimpleNamespace(content=[SimpleNamespace(type="text", text="# Backup policy\n")])
        return _S()


def test_translate_page_uses_stream(monkeypatch):
    msgs = _Messages()
    monkeypatch.setattr(doc_agent, "_read_secret", lambda name: "x")
    monkeypatch.setattr(llm_client, "guarded_client", lambda *a, **k: SimpleNamespace(messages=msgs))
    monkeypatch.setattr(doc_agent, "_en_problems", lambda *a, **k: [])
    assert doc_agent.translate_page("docs/BACKUP_POLICY.md", dry_run=True) is False  # dry_run: не пишет
    assert msgs.streamed == 1
