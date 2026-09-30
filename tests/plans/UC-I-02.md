# Тест-план UC-I-02 — Telegram fail-closed

**Источник правды:** `USE_CASES.md` §4.I → UC-I-02
**Alias:** `UC-SEC-001`
**Status:** `implemented` · **Confirmation:** `confirmed`
**Type:** `check` (unit) · **Oracle:** `B + E`
**Owner:** `telegram_bot.py`
**Risk:** high (медицинские данные пациента)

---

## Что проверяем

**Прагматика:** персональные медицинские данные доступны только владельцу.
Чужой `chat_id` не должен получить ни одного исходящего сообщения от бота.

**Что система обязана делать:**
1. При старте бота — fail-closed: если `~/.health_secrets/telegram_chat_id`
   не существует, процесс падает с `RuntimeError` (предотвращает race condition,
   когда первый встречный мог бы стать владельцем).
2. `_owner_filter()` возвращает `filters.Chat(chat_id=[OWNER_CHAT_ID])`.
3. Все 26 `CommandHandler`, 3 `MessageHandler`, и `ConversationHandler`
   (entry + states) — обёрнуты в `filters=owner` либо `& owner`.

**Что система НЕ должна делать (NOT-Then):**
- Отправлять чужому чату: отчёты, кнопки approval, consult-ответы, labs, genome, tasks.
- Принимать команды от чужого `chat_id`.

---

## Стратегия теста

### Уровень: `unit` (без сетевых вызовов)

Использует fixture `tg` (TelegramMock из `tests/fixtures/telegram.py`):
- `tg.make_update(text=..., chat_id=...)` → создаёт лёгкий Update.
- `tg.filter_passes(filter_obj, update)` → применяет PTB-фильтр и возвращает bool.

### Оракулы

**B (negative invariants):**
- `_owner_filter().check_update(update_other)` → False для любого чужого `chat_id`.
- Это означает: PTB не вызовет handler для чужого chat — никаких outgoing.

**E (cross-check с реальным кодом):**
- Берём `OWNER_CHAT_ID` из загруженного `telegram_bot` (это значит, secrets-файл
  на машине существует, что само по себе подтверждает fail-closed на старте).
- Перечисляем все `add_handler(...)` в `telegram_bot.py` через AST-парсинг
  и проверяем, что в каждом есть `filters=owner` или `& owner`.

---

## Структура теста

```python
# tests/unit/test_uc_i_02_sec.py

pytestmark = pytest.mark.unit

def test_owner_filter_passes_owner_chat_id(tg):
    """B: filter принимает OWNER_CHAT_ID."""

def test_owner_filter_blocks_other_chat_id(tg):
    """B: filter не принимает чужой chat_id (главный инвариант UC-I-02)."""

def test_owner_filter_blocks_zero(tg):
    """B: chat_id=0 (часто значение по умолчанию у моков) — не проходит."""

def test_owner_filter_blocks_negative(tg):
    """B: отрицательные chat_id (групповые чаты в TG) — не проходят."""

def test_owner_chat_id_loaded_from_secrets():
    """E: OWNER_CHAT_ID — int, загружен из ~/.health_secrets/telegram_chat_id.
    Сам факт успешного импорта telegram_bot подтверждает fail-closed на старте."""

def test_all_command_handlers_have_owner_filter():
    """E: AST-парсим telegram_bot.py, для каждого add_handler(...)
    проверяем что в нём есть `filters=owner` или `& owner`."""
```

---

## Acceptance criteria

- ✅ Все тесты зелёные (`pytest tests/unit/test_uc_i_02_sec.py -v`).
- ✅ Если кто-то добавит новый `CommandHandler` без `filters=owner` →
  `test_all_command_handlers_have_owner_filter` краснеет.

## Что НЕ покрывает (явно)

- Не e2e: не запускаем реальный Telegram polling, не интегрируемся с TG API.
  Это unit-уровень.
- Не покрывает старт бота с отсутствующим `CHAT_ID_FILE` —
  для этого нужен subprocess-spawn, что слишком дорого. Защита там есть
  (`if not CHAT_ID_FILE.exists(): raise` на module-level), но тест на это
  потребовал бы перезапуска интерпретатора. Отложено до UC-D-04 e2e.

## Risk если тест красный

**high** — означает, что либо чужой chat может видеть пациентские данные,
либо новый handler добавлен без фильтра. Любое падение → CRITICAL,
TG-алерт + Reminder в 10:00.
