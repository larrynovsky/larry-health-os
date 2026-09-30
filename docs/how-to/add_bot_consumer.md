[English](add_bot_consumer.en.md) · **Русский**

# Как безопасно добавить консьюмера owner chat_id

> **Тип документа:** How-to (Diátaxis) — рецепт для конкретной задачи.
> Reference API: [docs/reference/owner_chat_id_resolver.md](../reference/owner_chat_id_resolver.md).
> Зачем так: [docs/explanation/owner_chatid_lazy.md](../explanation/owner_chatid_lazy.md).

Задача: новый модуль/handler должен слать сообщение владельцу или проверять,
что апдейт пришёл от владельца.

## Правило одной строкой

Всегда `from bot.filters import owner_chat_id` и зови `owner_chat_id()` в точке
использования. **Никогда** не кэшируй значение в локальную константу и не читай файл
секрета сам — это заводит второй путь (split-brain), сторож
`test_no_second_owner_chat_id_constant` покраснеет.

## Если консьюмер — обычный CommandHandler / MessageHandler

Ничего вручную проверять не надо: передавай `owner`-фильтр при регистрации.

```python
def register(app, owner_filter):
    app.add_handler(CommandHandler("mycmd", cmd_mycmd, filters=owner_filter))
```

Сторож `test_all_command_handlers_have_owner_filter` требует owner-фильтр у каждого
`add_handler`. Без него — красный тест.

## Если консьюмер — CallbackQueryHandler (фильтр не принимается)

Встрой owner-чек вручную, **fail-closed**: недоступный резолв → блокируй всех.

```python
async def my_cb(update, context):
    from bot.filters import owner_chat_id
    try:
        _owner = owner_chat_id()
    except Exception:      # секрет недоступен → блокируем всех (НЕ fail-open)
        _owner = None
    if _owner is None or update.effective_chat is None \
            or update.effective_chat.id != _owner:
        return             # молчаливый выход, без раскрытия структуры callback_data
    ...
```

Антипаттерн (так делать нельзя — это баг F2):

```python
if OWNER_CHAT_ID is not None and id != OWNER_CHAT_ID:   # None → короткое замыкание → fail-OPEN
    return
```

## Если консьюмеру нужна отправка владельцу

```python
from bot.filters import owner_chat_id
await bot.send_message(chat_id=owner_chat_id(), text=...)
```

Если None-путь легитимен (например, «нет владельца — тихо выйти, не падать») — используй
`get_chat_id()`, который возвращает `None` вместо `raise`. Но для auth-чека это запрещено
(None-auth = fail-open).

## Проверка перед коммитом

```bash
# На Studio (канон):
/opt/homebrew/bin/python3.11 -m pytest tests/consistency/test_owner_chat_id_resolver.py \
    tests/unit/test_uc_i_02_sec.py -q
```

Оба зелёные → консьюмер добавлен корректно.
