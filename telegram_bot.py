#!/usr/bin/env python3.11
"""
telegram_bot.py — thin entry-point для launchd plist com.larry.health.bot.

Real logic в bot/main.py. Sprint 6 декомпозиция (2026-05-23): 1913 LOC → ~70 LOC.

Этот файл сохраняет backward-compat:
- Импорт `from telegram_bot import _owner_filter, owner_chat_id` для тестов.
- `python3.11 telegram_bot.py` запускает бота через bot.main.main().

См. audit/sprint_6_plan_2026-05-23.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ── Backward-compat re-exports ──────────────────────────────────────────────
# Существующие тесты и любые внешние callers продолжают импортить из telegram_bot.

# Sprint 6 C1: bot/ pkg
from bot.filters import (  # noqa: E402, F401
    TOKEN_FILE, CHAT_ID_FILE, owner_chat_id, assert_owner_configured,
    get_token, get_chat_id, save_chat_id, _owner_filter,
)
from bot.errors import _error_handler  # noqa: E402, F401
from bot.utils import send_long  # noqa: E402, F401

# Sprint 6 C9: services/recommendations
from services.recommendations import _signal_reason, evaluate_domain_need  # noqa: E402, F401

# Sprint 6 C10: bot/helpers (для callers которые могут импортить refresh_data из telegram_bot)
from bot.helpers import (  # noqa: E402, F401
    refresh_data,
    _send_problem_proposals,
    _send_tasks_from_report,
    _run_arbiter_background,
    _finalize_checkin_background,
)

# Sprint 6 C12: main вынесен в bot/main.py
from bot.main import main  # noqa: E402, F401


if __name__ == "__main__":
    main()
