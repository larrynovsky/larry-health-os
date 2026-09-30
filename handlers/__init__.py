"""
handlers/ — Telegram bot user-facing commands и сообщения.

Sprint 6 декомпозиция telegram_bot.py:
- handlers/meta.py        — start, help, app, memory, checkin, sleep, experiment
- handlers/reports.py     — report, weekly, monthly, labs
- handlers/tasks.py       — tasks, done, dismiss, visit
- handlers/problems.py    — problems, approve, reject
- handlers/hypotheses.py  — hypotheses, confirm, hreject, protocols, retire, hyp
- handlers/genome.py      — genome, genome_update
- handlers/consult.py     — /consult ConvHandler + state handlers
- handlers/messages.py    — handle_text, handle_photo, handle_location
- handlers/callbacks.py   — callback_doc_review, cb_router_with_owner_check

Каждый модуль экспортирует `register(app, owner_filter)` функцию.

См. audit/sprint_6_plan_2026-05-23.md.
"""
