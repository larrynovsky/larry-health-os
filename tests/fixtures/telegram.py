"""
tests/fixtures/telegram.py — minimal mock для python-telegram-bot.

**Что делает:**
1. **Capture mode** — `tg.outgoing` собирает всё, что код пытается отправить:
   `send_message`, `send_photo`, `send_chat_action`, etc. Тест проверяет,
   что было/не было отправлено.
2. **Inject mode** — `tg.make_update(text=..., chat_id=...)` создаёт лёгкий
   `Update`-подобный объект, который можно скармливать handler-функциям
   или filters (`_owner_filter()`).
3. **Filter eval** — `tg.filter_passes(filter_obj, update)` применяет
   `python-telegram-bot` filter к фейковому update и возвращает bool.

**Зачем не полный bot.start_polling():** для unit/integration тестов нам нужны
лёгкие проверки на уровне handler/filter. Полный polling — это уже e2e,
там нужна интеграция с Telegram API mock сервером (не наш случай).

**Использование:**

    def test_unauthorized_chat_blocked(tg):
        from telegram_bot import _owner_filter
        update = tg.make_update(text="/report", chat_id=999)  # чужой chat
        assert not tg.filter_passes(_owner_filter(), update)

    async def test_morning_report_sent(tg):
        # ... handler сделал bot.send_message(...)
        assert any("ВСР" in m["text"] for m in tg.outgoing)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

import pytest


# ── Lightweight Telegram-объекты ────────────────────────────────────────────

@dataclass
class _MockChat:
    id: int
    type: str = "private"


@dataclass
class _MockUser:
    id: int
    is_bot: bool = False
    first_name: str = "Test"
    username: Optional[str] = None


@dataclass
class _MockMessage:
    message_id: int
    chat: _MockChat
    from_user: _MockUser
    text: Optional[str] = None
    photo: Optional[list] = None
    location: Optional[Any] = None

    @property
    def chat_id(self) -> int:
        return self.chat.id


@dataclass
class _MockUpdate:
    update_id: int
    message: Optional[_MockMessage] = None
    callback_query: Optional[Any] = None

    @property
    def effective_chat(self) -> Optional[_MockChat]:
        if self.message:
            return self.message.chat
        return None

    @property
    def effective_user(self) -> Optional[_MockUser]:
        if self.message:
            return self.message.from_user
        return None

    @property
    def effective_message(self) -> Optional[_MockMessage]:
        return self.message


# ── Capture-mock для бота ────────────────────────────────────────────────────

@dataclass
class TelegramMock:
    """Главный объект fixture."""
    outgoing: List[dict] = field(default_factory=list)
    _next_update_id: int = 1
    _next_message_id: int = 1

    # ── Capture API ─────────────────────────────────────────────────────────
    async def send_message(self, chat_id: int, text: str, **kwargs):
        self.outgoing.append({"type": "message", "chat_id": chat_id,
                              "text": text, **kwargs})
        # Возвращаем «отправленное сообщение» — некоторые handlers это используют
        return _MockMessage(
            message_id=self._next_message_id,
            chat=_MockChat(id=chat_id),
            from_user=_MockUser(id=0, is_bot=True),
            text=text,
        )

    async def send_photo(self, chat_id: int, photo: Any, caption: str = None, **kwargs):
        self.outgoing.append({"type": "photo", "chat_id": chat_id,
                              "photo": photo, "caption": caption, **kwargs})

    async def send_document(self, chat_id: int, document: Any, caption: str = None,
                            filename: str = None, **kwargs):
        # `document` кладём КАК ПЕРЕДАЛИ (Path/bytes/file_id) — тест про вложение
        # обязан видеть, ЧТО именно ушло, а не факт вызова.
        self.outgoing.append({"type": "document", "chat_id": chat_id,
                              "document": document, "caption": caption,
                              "filename": filename, **kwargs})

    async def send_chat_action(self, chat_id: int, action: str, **kwargs):
        self.outgoing.append({"type": "chat_action", "chat_id": chat_id,
                              "action": action})

    async def edit_message_text(self, text: str, chat_id: int = None,
                                 message_id: int = None, **kwargs):
        self.outgoing.append({"type": "edit", "chat_id": chat_id,
                              "message_id": message_id, "text": text, **kwargs})

    # Sync-версии (некоторые места в коде вызывают через asyncio.to_thread)
    def send_message_sync(self, chat_id: int, text: str, **kwargs):
        self.outgoing.append({"type": "message_sync", "chat_id": chat_id,
                              "text": text, **kwargs})

    # ── Inject API ──────────────────────────────────────────────────────────
    def make_update(self, text: str = "", chat_id: int = 1, user_id: int = 1,
                    is_bot: bool = False) -> _MockUpdate:
        """Создать минимально валидный Update-подобный объект."""
        chat = _MockChat(id=chat_id)
        user = _MockUser(id=user_id, is_bot=is_bot)
        msg = _MockMessage(
            message_id=self._next_message_id,
            chat=chat,
            from_user=user,
            text=text,
        )
        upd = _MockUpdate(update_id=self._next_update_id, message=msg)
        self._next_update_id += 1
        self._next_message_id += 1
        return upd

    def filter_passes(self, filter_obj, update) -> bool:
        """
        Применяет python-telegram-bot filter к фейковому update.
        Возвращает True если filter принимает.

        В PTB v20+ filter имеет метод `.check_update(update)`. Мы пытаемся
        вызвать его; если не получается — пробуем `.filter(message)`.
        """
        try:
            res = filter_obj.check_update(update)
            return bool(res)
        except Exception:
            try:
                return bool(filter_obj.filter(update.effective_message))
            except Exception:
                return False

    # ── Утилиты для проверок в тестах ───────────────────────────────────────
    def messages_to(self, chat_id: int) -> List[dict]:
        return [m for m in self.outgoing if m.get("chat_id") == chat_id]

    def texts_to(self, chat_id: int) -> List[str]:
        return [m["text"] for m in self.messages_to(chat_id)
                if m.get("type") in ("message", "message_sync") and "text" in m]

    def reset(self) -> None:
        self.outgoing.clear()


# ── Fixture ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tg() -> TelegramMock:
    """Чистый TelegramMock на каждый тест."""
    return TelegramMock()
