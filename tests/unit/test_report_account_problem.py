"""
tests/unit/test_report_account_problem.py — карточка report:90fb8f (24.10, v0.2.4).

Утренний отчёт чужой установки падал на 400 от Anthropic в думающем вызове
(BadRequestError в llm_client._s ← gp_agent.generate_daily_report). Отказ по счёту
(«usage limits», «credit balance») — проблема, которую чинит только человек, и
llm_client.is_account_problem её распознаёт с 23.10. Но утренний отчёт и /report
его не спрашивали: человеку уходило «сбой записал, починка не работает»
(jobs.morning.failed.unrepaired), с чем ему делать нечего. Теперь — строка про ключ
и баланс. В обе стороны (§17): обычный сбой по-прежнему даёт свою строку.
"""
from __future__ import annotations

import asyncio
import types

import httpx
import anthropic
import pytest

import i18n
import notify
import jobs.scheduled as sched
import triage_agent as _tri

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolated_key_directory(monkeypatch, tmp_path):
    import llm_client
    monkeypatch.setattr(llm_client, "_KEY_FILE", tmp_path / "anthropic_key")
    return tmp_path


def _bad_request(text: str):
    req = httpx.Request("POST", "https://api.example/v1/messages")
    return anthropic.BadRequestError(text, response=httpx.Response(400, request=req), body=None)


def _morning(monkeypatch, tg, exc) -> list:
    monkeypatch.setattr(_tri, "latest_verdict", lambda require_today=False: ({}, ""))
    monkeypatch.setattr(notify, "notify_operator", lambda msg: "telegram")
    monkeypatch.setattr("llm_client.provider", lambda: "anthropic")

    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(sched.gp, "generate_daily_report", boom)
    monkeypatch.setattr(sched, "refresh_data", lambda *a, **k: None)
    monkeypatch.setattr(sched, "get_chat_id", lambda: 111222333)
    sent = []

    async def fake_send_long(bot, chat_id, text, **kw):
        sent.append(text)

    monkeypatch.setattr(sched, "send_long", fake_send_long)
    asyncio.run(sched.send_morning_report(types.SimpleNamespace(bot=tg)))
    return sent


@pytest.mark.parametrize("text", [
    "You have reached your specified API usage limits. You will regain access on 2026-11-01",
    "Your credit balance is too low to access the Anthropic API.",
])
def test_morning_report_account_refusal_tells_key_problem(monkeypatch, tg, fault_journal, text):
    sent = _morning(monkeypatch, tg, _bad_request(text))
    assert sent == [i18n.t("person.llm.key_problem", provider="Anthropic")]
    assert fault_journal.read_text().strip(), "сбой обязан остаться в журнале"


def test_morning_report_other_bad_request_keeps_failure_line(monkeypatch, tg, fault_journal):
    sent = _morning(monkeypatch, tg, _bad_request("thinking: type 'enabled' is not supported"))
    assert len(sent) == 1
    assert sent[0] != i18n.t("person.llm.key_problem", provider="Anthropic")
    assert sent[0] in (i18n.t("jobs.morning.failed"), i18n.t("jobs.morning.failed.unrepaired"))


@pytest.mark.parametrize("cmd, gen", [("cmd_report", "generate_daily_report"),
                                       ("cmd_weekly", "generate_weekly_report"),
                                       ("cmd_monthly", "generate_monthly_report")])
def test_report_commands_account_refusal_tell_key_problem(monkeypatch, fault_journal, cmd, gen):
    """Все три команды — каждая своим вызовом: откат любой одной краснит свой параметр."""
    import handlers.reports as rep
    import bot.helpers
    monkeypatch.setattr("llm_client.provider", lambda: "anthropic")
    monkeypatch.setattr(bot.helpers, "refresh_data", lambda *a, **k: None)

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(bot.helpers, "_send_problem_proposals", _noop)
    monkeypatch.setattr(bot.helpers, "_send_tasks_from_report", _noop)

    def boom(*a, **k):
        raise _bad_request("You have reached your specified API usage limits")

    monkeypatch.setattr(rep.gp, gen, boom)
    sent = []

    async def fake_send_long(bot, chat_id, text, **kw):
        sent.append(text)

    monkeypatch.setattr(rep, "send_long", fake_send_long)
    msg = types.SimpleNamespace(chat=types.SimpleNamespace(send_action=_noop), get_bot=lambda: None)
    update = types.SimpleNamespace(message=msg, effective_chat=types.SimpleNamespace(id=1))
    asyncio.run(getattr(rep, cmd)(update, None))
    assert sent == [i18n.t("person.llm.key_problem", provider="Anthropic")]
    assert "Anthropic" in sent[0] and "{" not in sent[0]


def test_rate_limit_is_not_called_a_balance_problem(monkeypatch):
    """Ревью 27.10: «RESOURCE_EXHAUSTED … per minute» ловит общий маркер счёта, но это частота —
    совет «пополните баланс» был бы ложным. Отказ по счёту при этом остаётся счётом."""
    import llm_client
    monkeypatch.setattr(llm_client, "provider", lambda: "gemini")
    rate = RuntimeError("429 RESOURCE_EXHAUSTED: per-minute request quota exceeded; retry in 30s")
    quota = RuntimeError("429 RESOURCE_EXHAUSTED: You exceeded your current quota, check billing")
    assert llm_client.fault_person_key(rate, "jobs.morning.failed") == ("jobs.morning.failed", {})
    assert llm_client.fault_person_key(quota, "jobs.morning.failed") == (
        "person.llm.key_problem", {"provider": "Gemini"})


@pytest.mark.parametrize("lang", ["ru", "en"])
@pytest.mark.parametrize("current,others", [
    ("anthropic", ["openai"]), ("anthropic", ["openai", "gemini", "deepseek"]),
    ("openai", ["anthropic"])])
def test_account_refusal_names_other_keys_without_reading_them(
        monkeypatch, isolated_key_directory, lang, current, others):
    import llm_client as lc
    from pathlib import Path
    monkeypatch.setenv("HEALTH_LLM_PROVIDER", current)
    monkeypatch.setattr(i18n, "lang_of", lambda: lang)
    profiles = lc.profiles()
    for name in [current, *others]:
        (isolated_key_directory / profiles[name]["key_file"]).write_text("synthetic-secret")
    # Guard the filesystem boundary: detecting a key must never read its value.
    read_text = Path.read_text

    def guarded_read(path, *args, **kw):
        assert path.parent != isolated_key_directory, "key contents were read"
        return read_text(path, *args, **kw)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    exc = anthropic.PermissionDeniedError(
        "refused", response=httpx.Response(403, request=httpx.Request("POST", "https://api.example")), body=None)
    key, kw = lc.fault_person_key(exc, "jobs.morning.failed")
    assert key == "person.llm.key_problem_other_keys"
    message = i18n.t(key, **kw)
    assert message.startswith(i18n.t("person.llm.key_problem", provider=lc.provider_title(current)))
    for name in others:
        assert lc.provider_title(name) in message
        assert f"HEALTH_LLM_PROVIDER={name}" in message
    assert f"HEALTH_LLM_PROVIDER={current}" not in message
    assert ".env" in message and "docker compose up -d" in message
    assert "synthetic-secret" not in message
    rate = RuntimeError("429 RESOURCE_EXHAUSTED per minute; retry in 30s")
    assert lc.fault_person_key(rate, "jobs.morning.failed") == ("jobs.morning.failed", {})


def test_current_key_alone_keeps_old_message(monkeypatch, isolated_key_directory):
    import llm_client as lc
    monkeypatch.setenv("HEALTH_LLM_PROVIDER", "anthropic")
    (isolated_key_directory / "anthropic_key").touch()
    # A directory bearing a key filename is not a key file.
    (isolated_key_directory / "openai_key").mkdir()
    assert lc.fault_person_key(_bad_request("credit balance"), "jobs.morning.failed") == (
        "person.llm.key_problem", {"provider": "Anthropic"})


def test_morning_report_delivers_other_key_hint(monkeypatch, tg, fault_journal, isolated_key_directory):
    (isolated_key_directory / "openai_key").touch()
    sent = _morning(monkeypatch, tg, _bad_request("credit balance"))
    assert len(sent) == 1
    assert "Anthropic" in sent[0] and "OpenAI" in sent[0]
    assert "HEALTH_LLM_PROVIDER=openai" in sent[0]
