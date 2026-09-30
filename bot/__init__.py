"""
bot/ — entry-point package для Telegram-бота.

Sprint 6 декомпозиция telegram_bot.py:
- bot/filters.py    — _owner_filter, owner_chat_id(), secrets
- bot/errors.py     — _error_handler
- bot/utils.py      — send_long (для длинных сообщений)
- bot/main.py       — build_app + register loop + run_polling (после C12)

См. audit/sprint_6_plan_2026-05-23.md.
"""
