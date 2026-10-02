#!/usr/bin/env python3
"""Первый контакт постороннего с ботом — так, как его увидит бот (нить first-contact, 02.10).

Зачем. Урок установки в CI доходил до отказа Telegram на поддельном токене и дальше не шёл:
/start на каждой установке в Докере падал первой строкой (запись в каталог ключей, смонтированный
только для чтения), а оба теста /start подменяли ровно эту строку. Здесь не подменено ничего из
бота: приложение собирает `bot.main.build_app` — те же обработчики, фильтр владельца и обработчик
ошибок, что слушают Telegram. Поддельный только сетевой слой Telegram (FakeTelegram ниже).

Что проходится: /start → всё знакомство до конца (кнопки, текст, дата, место) → /help.
Сбой — любое из трёх: вызов `notify.fault` (так бот говорит человеку «что-то сломалось», в том
числе из обработчиков, которые ловят ошибку сами), запись уровня ERROR в журнале, знакомство не
дошло до конца. Запускать ВНУТРИ контейнера бота, с его монтированием (ключи — только чтение):

    docker compose run --rm --no-deps -T bot python3 /app/scripts/first_contact_smoke.py

Код выхода 0 — человек прошёл знакомство; 1 — нет, причины напечатаны.

Граница: Telegram поддельный — длину сообщений, разметку, гонки кнопок проверка не видит. Модель
не зовётся: если шаг знакомства начнёт её звать, проверка покажет это сбоем (ключ в CI поддельный),
и решать, подставлять ли ответ модели, надо тогда, а не заранее.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram import Update  # noqa: E402
from telegram.request import BaseRequest  # noqa: E402

_MAX_STEPS = 60          # знакомство — 14 вопросов; запас на переспросы
_SAME_ITEM_LIMIT = 3     # один и тот же вопрос трижды подряд — застряли
_ANSWERS = {"text": "Smoke", "date": "1980-01-01", "number": "70"}
# Границы допустимого живут в коде проверки ответа, а не в методике (там только подсказка),
# поэтому по вопросу — ответ, который человек дал бы сам.
_BY_ITEM = {"height": "175", "weight": "70", "health": "ничего не беспокоит",
           "allergies": "нет", "meds": "нет"}


class FakeTelegram(BaseRequest):
    """Сетевой слой Telegram: отвечает как сервер, записывает, что бот отправил."""

    def __init__(self, sent: list):
        self.sent = sent
        self._mid = 1000

    @property
    def read_timeout(self):
        return None

    async def initialize(self):
        return None

    async def shutdown(self):
        return None

    async def do_request(self, url, method, request_data=None, read_timeout=None,
                         write_timeout=None, connect_timeout=None, pool_timeout=None):
        name = url.rsplit("/", 1)[-1]
        params = dict(request_data.parameters) if request_data is not None else {}
        for k, v in list(params.items()):
            if isinstance(v, str) and v[:1] in "{[":
                try:
                    params[k] = json.loads(v)
                except ValueError:
                    pass
        if name == "getMe":
            result = {"id": 1, "is_bot": True, "first_name": "smoke", "username": "smoke_bot"}
        elif name.startswith("send") and name != "sendChatAction":
            self._mid += 1
            result = {"message_id": self._mid, "date": int(time.time()),
                      "chat": {"id": int(params.get("chat_id", 0)), "type": "private"},
                      "text": params.get("text") or params.get("caption") or ""}
            markup = params.get("reply_markup")
            if isinstance(markup, dict) and "inline_keyboard" in markup:
                result["reply_markup"] = markup     # Telegram возвращает в сообщении только inline
            self.sent.append({"method": name, **result})
        else:
            result = True
        return 200, json.dumps({"ok": True, "result": result}).encode()


class _Errors(logging.Handler):
    def __init__(self, sink: list):
        super().__init__(logging.ERROR)
        self.sink = sink

    def emit(self, record):
        self.sink.append(f"ERROR в журнале ({record.name}): {record.getMessage()[:300]}")


def _last_inline(sent: list):
    for m in reversed(sent):
        kb = (m.get("reply_markup") or {}).get("inline_keyboard")
        if kb:
            return m, kb
    return None, None


async def walk(chat_id: int) -> tuple[list[str], list]:
    """Пройти первый контакт. Возврат: (проблемы, отправленное ботом)."""
    import notify
    import assessment_dialog as ad
    from bot.main import build_app

    problems: list[str] = []
    sent: list = []
    real_fault = notify.fault

    def fault(tech, *a, **kw):
        problems.append(f"бот сообщил о сбое: {tech[:300]}")
        return real_fault(tech, *a, **kw)

    notify.fault = fault
    # ERROR считаются со СБОРКИ приложения (с 02.10, BL-BRIEF-TZ-ERROR-1): журнал бота человек
    # читает с первой строки, и строки старта — это его первый контакт с журналом. До 02.10
    # сборка не судилась, и ложный ERROR о поясе у каждого новичка проходил мимо смоука.
    errors = _Errors(problems)
    logging.getLogger().addHandler(errors)
    app = build_app("123456:first-contact-smoke", request=FakeTelegram(sent))
    await app.initialize()
    bot, uid = app.bot, [0]

    user = {"id": chat_id, "is_bot": False, "first_name": "Smoke"}
    chat = {"id": chat_id, "type": "private"}

    async def push(payload: dict):
        uid[0] += 1
        await app.process_update(Update.de_json({"update_id": uid[0], **payload}, bot))

    def message(**fields):
        return {"message": {"message_id": 10_000 + uid[0], "date": int(time.time()),
                            "chat": chat, "from": user, **fields}}

    def command(cmd: str):
        return message(text=cmd, entities=[{"type": "bot_command", "offset": 0, "length": len(cmd)}])

    import health_db as db
    if db.get_patient_profile().get("identity.name"):
        # На заполненном профиле /start здоровается, знакомства нет — и проверка зеленела бы
        # на пустом месте (так и случилось на втором прогоне в том же томе, 02.10).
        problems.append("стенд не чистый: профиль уже заполнен — нужна свежая база (новый том)")
        await app.shutdown()
        notify.fault = real_fault
        logging.getLogger().removeHandler(errors)
        return problems, sent
    answered = 0
    try:
        await push(command("/start"))
        last_item, same = None, 0
        for _ in range(_MAX_STEPS):
            session = ad.get_active(chat_id)
            if not session:
                break
            item = ad.current_item(session) or {}
            same = same + 1 if item.get("id") == last_item else 1
            last_item = item.get("id")
            if same > _SAME_ITEM_LIMIT:
                said = " | ".join(m.get("text", "")[:160] for m in sent[-2:])
                problems.append(f"знакомство застряло на вопросе {last_item!r} ({item.get('kind')}); "
                                f"бот ответил: {said}")
                break
            kind = item.get("kind", "scale")
            answered += 1
            if kind == "location":
                await push(message(location={"latitude": 52.52, "longitude": 13.40}))  # Берлин — пояс урока
            elif kind in _ANSWERS:
                await push(message(text=_BY_ITEM.get(last_item, _ANSWERS[kind])))
            else:
                msg, kb = _last_inline(sent)
                data = next((b.get("callback_data") for row in (kb or []) for b in row
                             if str(b.get("callback_data", "")).startswith("cb_aa:")), None)
                if data is None:
                    problems.append(f"у вопроса {last_item!r} ({kind}) нет кнопок ответа")
                    break
                await push({"callback_query": {"id": str(uid[0]), "from": user,
                                               "chat_instance": "smoke", "data": data,
                                               "message": {k: v for k, v in msg.items()
                                                           if k != "method"}}})
        else:
            problems.append(f"знакомство не кончилось за {_MAX_STEPS} шагов")
        if ad.get_active(chat_id) and not any("застряло" in p or "нет кнопок" in p for p in problems):
            problems.append("знакомство не дошло до конца")
        if answered == 0:
            problems.append("знакомство не началось: после /start нет ни одного вопроса")
        elif db.get_patient_profile().get("identity.name") != _ANSWERS["text"]:
            problems.append("знакомство кончилось, но имя в профиль не записано")
        await push(command("/help"))
    finally:
        notify.fault = real_fault
        logging.getLogger().removeHandler(errors)
        await app.shutdown()
    if not sent:
        problems.append("бот не отправил ни одного сообщения")
    return problems, sent


def main() -> int:
    import health_db as db
    from bot.filters import owner_chat_id

    db.init_db()
    problems, sent = asyncio.run(walk(owner_chat_id()))
    print(f"бот отправил сообщений: {len(sent)}")
    if problems:
        print("⛔ первый контакт не пройден:")
        for p in problems:
            print("  -", p)
        return 1
    print("✅ первый контакт пройден: /start → знакомство до конца → /help")
    return 0


if __name__ == "__main__":
    sys.exit(main())
