[English](owner_chat_id_resolver.en.md) · **Русский**

# Резолвер владельца: API и режимы

> **Жанр (Diátaxis): reference.** Сухая карта «функция → поведение → режим отказа».
> Зачем/развилки: [docs/explanation/owner_chatid_lazy.md](../explanation/owner_chatid_lazy.md).
> Как добавить консьюмера: [docs/how-to/add_bot_consumer.md](../how-to/add_bot_consumer.md).
> Модуль: `bot/filters.py`. Единый резолвер каталога секретов: `secrets_paths.secrets_dir()`.

## Функции

| Функция | Возвращает | Когда нет секрета | Мемо | Где звать |
|---------|-----------|-------------------|------|-----------|
| `owner_chat_id() -> int` | `chat_id` владельца из `<secrets_dir>/telegram_chat_id` | `raise RuntimeError` (НИКОГДА не None) | да (снимок при 1-м вызове) | в точке использования, не на импорте |
| `assert_owner_configured() -> None` | `None` при успехе | `raise RuntimeError` | — (делегирует в `owner_chat_id()`) | `bot/main.main()` первым делом (старт-гейт) |
| `_owner_filter() -> filters.BaseFilter` | `filters.Chat(chat_id=[owner_chat_id()])` | пробрасывает `raise` | — | регистрация handler-ов |
| `get_chat_id() -> int \| None` | `chat_id` или `None`, если файла нет | `None` (не raise) | нет (читает файл каждый раз) | доставка, где None-путь легитимен |
| `get_token() -> str` | Telegram-токен из `<secrets_dir>/telegram_token` | `raise` (файл не найден) | нет | старт `Application.builder` |

## Инварианты (под сторожами)

| Инвариант | Что значит | Доказывающий тест |
|-----------|-----------|-------------------|
| import-safe | `import bot.filters` без секрета НЕ падает | `test_bot_filters_import_safe_without_chat_id` |
| fail-closed чтение/старт | `owner_chat_id()`/`assert_owner_configured()` без секрета `raise`, не None | `test_owner_chat_id_and_start_gate_fail_closed_without_secret` |
| single-resolver (anti-split-brain) | ни один прод-модуль не держит второй `OWNER_CHAT_ID = ...` | `test_no_second_owner_chat_id_constant` |
| single-resolver каталога | резолв `HEALTH_SECRETS_DIR` только в `secrets_paths.py` | `test_secrets_single_resolver` |
| F2 fail-closed | `cb_router` при недоступном резолве блокирует всех | `test_assessment_router_*` (2 теста) |
| все handler-ы под owner | нет `add_handler` без owner-фильтра / inline-чека | `test_all_command_handlers_have_owner_filter` |

## Квитанции

- Старт: лог `owner resolved: <id>` (`~/health_bot.log`) — резолв состоялся, гейт пройден.
- Per-tenant: `HEALTH_SECRETS_DIR=~/.health_secrets_partner` → резолв даёт chat_id партнёра,
  отличный от владельца (доказательство отсутствия split-brain на уровне каталогов).

## Режимы отказа (сводка)

- Нет файла `telegram_chat_id` → `owner_chat_id()` и `assert_owner_configured()` → `RuntimeError`
  → `bot/main.main()` выходит до `run_polling` (бот не поднимается). Это желаемое поведение.
- Тенант без `HEALTH_SECRETS_DIR`, но с `HEALTH_DATA_DIR ≠ .../health` → `secrets_dir()` сам
  `raise` (fail-closed против кросс-тенант утечки), см. `secrets_paths.py`.
