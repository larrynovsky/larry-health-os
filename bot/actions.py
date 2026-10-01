"""
bot/actions.py — действия человека кнопками, а не командами.

Решение владельца 28.09: всё, что бот просил «напечатать команду» (/approve 57,
/done <id> [текст], /retire <id>), — кнопка под сообщением. Холодное чтение
показало цену команды: читатель не знает, печатать ли угловые скобки, и
сообщение с командой без кнопки для него — тупик.

Один домен: «кнопка под сообщением → действие над предметом» и «кнопка просит
текст → реплай доставляется тому же действию». Сами действия живут рядом со своей
командой (handlers/*): там же, где их логика, регистрируются декоратором.

Две формы:
- `button(label, verb, target)` — callback_data "act:<verb>:<target>" (≤64 байт);
  нажатие зовёт функцию, зарегистрированную `@action(verb)`.
- `ask(bot, chat_id, prompt, verb, target)` — сообщение с ForceReply; message_id
  записан в bot_prompts, и реплай на него зовёт функцию `@on_reply(verb)`.
  Квитанция в базе, а не в памяти процесса: вопрос может ждать ответа днями,
  а бот перезапускается (тот же образец, что tasks.tg_message_id).
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

PREFIX = "act"
_MAX_CALLBACK_BYTES = 64          # предел Telegram на callback_data
_ACTIONS: dict[str, Callable[..., Awaitable[int | None]]] = {}
_REPLIES: dict[str, Callable[..., Awaitable[int | None]]] = {}

_DDL = """
CREATE TABLE IF NOT EXISTS bot_prompts (
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    verb       TEXT NOT NULL,
    target     TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (chat_id, message_id)
)"""


def action(verb: str):
    """Регистрирует обработчик нажатия: async fn(query, context, target)."""
    def deco(fn):
        _ACTIONS[verb] = fn
        return fn
    return deco


def on_reply(verb: str):
    """Регистрирует обработчик ответа на ask(): async fn(message, context, target, text)."""
    def deco(fn):
        _REPLIES[verb] = fn
        return fn
    return deco


def callback_data(verb: str, target) -> str:
    data = f"{PREFIX}:{verb}:{target}"
    if ":" in verb or len(data.encode()) > _MAX_CALLBACK_BYTES:
        raise ValueError(f"callback_data недопустим: {data!r}")
    return data


def button(label: str, verb: str, target):
    from telegram import InlineKeyboardButton
    return InlineKeyboardButton(label, callback_data=callback_data(verb, target))


def keyboard(*rows):
    """keyboard([btn, btn], [btn]) → InlineKeyboardMarkup; пустые ряды выбрасываются."""
    from telegram import InlineKeyboardMarkup
    return InlineKeyboardMarkup([list(r) for r in rows if r])


def _parse(data: str):
    parts = (data or "").split(":", 2)
    if len(parts) != 3 or parts[0] != PREFIX:
        return None
    return parts[1], parts[2]


import contextvars as _cv
_removed = _cv.ContextVar("actions_removed", default=None)


async def remove_target(query, target):
    """Снять кнопки выбранного предмета, сохранив остальные строки списка.

    Меняем только markup: одинаково для текста и caption документа/фото.
    """
    if _removed.get() == (id(query), str(target)):
        return                                   # уже сняты диспетчером в этом же нажатии
    markup = getattr(query.message, "reply_markup", None)
    rows = [[b for b in row if not ((parsed := _parse(b.callback_data))
                                   and parsed[1] == str(target))]
            for row in markup.inline_keyboard] if markup else []
    remaining = keyboard(*rows)
    await clear_markup(query, remaining if remaining.inline_keyboard else None)


async def clear_markup(query, markup=None):
    """Заменить клавиатуру; «message is not modified» — не ошибка: кнопки уже сняты
    диспетчером (handle_callback), и обработчик, снимающий их по старинке в конце, не должен
    падать после сделанной работы."""
    from telegram.error import BadRequest
    try:
        await query.edit_message_reply_markup(reply_markup=markup)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


async def handle_callback(update, context) -> int | None:
    from bot.filters import owner_chat_id
    import i18n
    if update.effective_chat is None or update.effective_chat.id != owner_chat_id():
        return                                   # UC-I-02 fail-closed, как у соседей
    query = update.callback_query
    await query.answer()
    parsed = _parse(query.data)
    fn = _ACTIONS.get(parsed[0]) if parsed else None
    if fn is None:
        log.warning(f"actions: неизвестное действие {query.data!r}")
        await query.message.reply_text(i18n.t("common.error.button_outdated"))
        return
    # Кнопки предмета снимаются ДО работы обработчика (01.10, слово владельца «кнопка должна
    # исчезать после нажатия»). До этого 15 из 20 обработчиков снимали их в конце, после
    # генерации моделью; кнопка жила ~7 с — повторное «Подтвердить» завело второй протокол (#1118).
    try:
        await remove_target(query, parsed[1])
        _removed.set((id(query), str(parsed[1])))
    except Exception as e:  # noqa: BLE001 — не снялись кнопки: работа всё равно идёт, но громко
        log.warning(f"actions: кнопки {query.data!r} не сняты до обработчика: {e}")
    return await fn(query, context, parsed[1])



def _conn():
    import health_db as db
    conn = db.get_conn()
    conn.execute(_DDL)
    return conn


async def ask(bot, chat_id: int, prompt: str, verb: str, target) -> int:
    """Шлёт prompt с ForceReply и запоминает, куда придёт ответ. → message_id."""
    from telegram import ForceReply
    if verb not in _REPLIES:
        raise ValueError(f"нет обработчика ответа для {verb!r}")
    msg = await bot.send_message(chat_id=chat_id, text=prompt,
                                 reply_markup=ForceReply(selective=True))
    with _conn() as conn:
        conn.execute("INSERT OR REPLACE INTO bot_prompts(chat_id, message_id, verb, target)"
                     " VALUES (?,?,?,?)", (chat_id, msg.message_id, verb, str(target)))
    return msg.message_id


def _reply_receipt(message):
    reply_to = getattr(message, "reply_to_message", None)
    if reply_to is None:
        return None
    with _conn() as conn:
        return conn.execute("SELECT verb, target FROM bot_prompts WHERE chat_id=? AND message_id=?",
                            (message.chat_id, reply_to.message_id)).fetchone()


def reply_verb(message):
    """Маршрут durable-реплая для ConversationHandler, включая рестарт бота."""
    receipt = _reply_receipt(message)
    return receipt[0] if receipt else None


async def handle_reply(message, context) -> bool:
    """Реплай на сообщение ask() → обработчик. True, если реплай был наш."""
    row = _reply_receipt(message)
    if row is None:
        return False
    verb, target = row[0], row[1]
    fn = _REPLIES.get(verb)
    if fn is None:
        log.warning(f"actions: ответ на {verb!r} без обработчика")
        return False
    await fn(message, context, target, (message.text or "").strip())
    with _conn() as conn:
        conn.execute("DELETE FROM bot_prompts WHERE chat_id=? AND message_id=?",
                     (message.chat_id, message.reply_to_message.message_id))
    return True


def register(app) -> None:
    from telegram.ext import CallbackQueryHandler
    app.add_handler(CallbackQueryHandler(handle_callback, pattern=rf"^{PREFIX}:"))


if __name__ == "__main__":
    assert callback_data("pa", 57) == "act:pa:57"
    assert _parse("act:td:412") == ("td", "412")
    assert _parse("cb_as:n:1") is None
    try:
        callback_data("x", "я" * 40)
        raise SystemExit("предел 64 байт не сработал")
    except ValueError:
        pass
    print("ok")
