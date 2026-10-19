"""«След экрана»: в отчёт о сбое попадают имена шаблонов, которые реально ушли, и ничего больше.

Значения в тестах выдуманы.
"""
import asyncio
import json
from types import SimpleNamespace

import i18n
import screen_trace
from bot.main import _TracedRequest


class _Inner:
    read_timeout = 5

    def __init__(self, code=200):
        self.code = code

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kw):
        return self.code, b'{"ok": true}'


def _send(text, method="sendMessage", code=200):
    req = _TracedRequest(_Inner(code))
    data = SimpleNamespace(parameters={"chat_id": 1, "text": text})
    return asyncio.run(req.do_request(f"https://x/bot1/{method}", "POST", request_data=data))


def test_sent_template_is_traced_by_key_not_text(screen_trace_file):
    text = i18n.t("onboarding.reply.stopped")
    i18n.t("common.error.button_outdated")            # подготовлено, но не отправлено
    _send(text + "\n\nМария Выдуманная, глюкоза 5.1")   # шаблон + данные человека
    rec = json.loads(screen_trace_file.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["keys"] == ["onboarding.reply.stopped"]
    raw = screen_trace_file.read_text(encoding="utf-8")
    assert "Мария" not in raw and "5.1" not in raw and text not in raw
    assert screen_trace.recent()[-1].endswith(" onboarding.reply.stopped")


def test_message_without_template_is_free_text(screen_trace_file):
    _send("ответ модели про сон, выдуманный")
    assert screen_trace.recent()[-1].endswith(" free_text")


def test_failed_send_and_other_methods_are_not_traced(screen_trace_file):
    _send(i18n.t("onboarding.reply.stopped"), code=400)
    _send(i18n.t("onboarding.reply.stopped"), method="getMe")
    assert not screen_trace_file.exists() or screen_trace_file.read_text() == ""


def test_other_tenant_lines_are_not_returned(screen_trace_file):
    screen_trace_file.write_text(json.dumps({"ts": "2099-01-01T00:00:00+00:00", "tenant": "чужой",
                                             "keys": ["jobs.brief.header"]}) + "\n")
    assert screen_trace.recent() == []


def test_trace_never_raises(monkeypatch, tmp_path):
    (tmp_path / "файл").write_text("не каталог")
    monkeypatch.setenv("HEALTH_SCREEN_TRACE", str(tmp_path / "файл" / "trace.jsonl"))
    screen_trace.note("любой текст")                   # не бросает
    assert screen_trace.recent() == []
