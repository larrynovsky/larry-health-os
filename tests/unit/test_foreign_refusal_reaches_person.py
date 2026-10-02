"""Отказ допуска через настоящие обработчики текста/фото, без сети и секретов."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("lang,expected", [
    ("ru", "Эта функция пока недоступна: она не прошла проверку с твоим поставщиком моделей. Повтор запроса не поможет."),
    ("en", "This feature is currently unavailable: it hasn't passed the checks with your model provider. Retrying the request won't help."),
], ids=["ru", "en"])
@pytest.mark.parametrize("route", ["text", "photo"])
def test_foreign_refusal_reaches_person(monkeypatch, tmp_path, lang, expected, route):
    import hai_chat
    import hai_core
    import profile_db
    from bot import errors, filters, helpers
    from handlers import messages

    refusal = hai_core.ModelNotAdmitted(
        "openai: модель 'gpt-x' роли 'sonnet' не прошла допуск")
    get_model = Mock(side_effect=refusal)
    create = Mock(side_effect=AssertionError("До поставщика моделей доходить нельзя"))
    monkeypatch.setattr(hai_core, "get_model", get_model)
    monkeypatch.setattr(hai_chat, "get_client", lambda: SimpleNamespace(
        messages=SimpleNamespace(create=create)))
    monkeypatch.setattr(hai_chat, "build_chat_payload", lambda *a: ("test", []))
    monkeypatch.setattr(hai_chat, "get_system_prompt", lambda: "test")
    monkeypatch.setattr(hai_chat.db, "init_db", lambda: None)
    monkeypatch.setattr(hai_chat, "build_smart_context", lambda *a, **kw: "test")
    monkeypatch.setattr(profile_db, "get_patient_profile", lambda: {"identity.language": lang})
    monkeypatch.setattr(filters, "owner_chat_id", lambda: 7)
    monkeypatch.setattr(messages.db, "get_open_tasks", lambda *a: [])
    monkeypatch.setattr(messages.ck, "checkin_state", SimpleNamespace(active=False))
    monkeypatch.setattr(messages.abh, "get_active_assessment", lambda *a: None)
    monkeypatch.setattr(helpers, "_run_arbiter_background", AsyncMock())
    monkeypatch.setattr(helpers, "_service_trouble_background", AsyncMock())
    journal = tmp_path / "faults.jsonl"
    monkeypatch.setenv("HEALTH_FAULTS_JOURNAL", str(journal))

    sent = []

    async def send_message(*, chat_id, text, **kw):
        assert chat_id == 7
        sent.append(text)
        return SimpleNamespace(message_id=100)

    async def reply_text(text, **kw):
        return await send_message(chat_id=7, text=text, **kw)

    file = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=b"test"))
    bot = SimpleNamespace(send_message=send_message, get_file=AsyncMock(return_value=file))
    chat = SimpleNamespace(id=7, send_action=AsyncMock())
    message = SimpleNamespace(text="Как дела?", caption="Что на фото?",
                              photo=[SimpleNamespace(file_id="test")], chat=chat, reply_to_message=None,
                              get_bot=lambda: bot, reply_text=reply_text)
    update = SimpleNamespace(message=message, effective_chat=chat, get_bot=lambda: bot)
    context = SimpleNamespace(bot=bot, user_data={})

    async def dispatch():
        try:
            handler = messages.handle_text if route == "text" else messages.handle_photo
            await handler(update, context)
        except hai_core.ModelNotAdmitted as err:
            # PTB передаёт необработанное исключение зарегистрированному error_handler.
            context.error = err
            await errors._error_handler(update, context)

    asyncio.run(dispatch())
    get_model.assert_called_once_with("sonnet")
    create.assert_not_called()
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    print(f"person[{lang}]={sent!r}; faults={records!r}")
    assert len(records) == 1
    assert "ModelNotAdmitted" in records[0]["text"]
    assert str(refusal) in records[0]["text"]
    assert sent == [expected]
