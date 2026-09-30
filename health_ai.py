#!/usr/bin/env python3.11
"""
health_ai — публичный интерфейс intelligence layer.

Re-exporter: весь код живёт в hai_*.py модулях.
Этот файл сохраняет обратную совместимость для telegram_bot.py, gp_agent.py
и всех LaunchAgent-скриптов, которые делают `import health_ai`.

Структура:
  hai_core.py        — client, system prompt, история сообщений, _strip_markdown
  hai_context.py     — context blocks, tool execution, smart context
  hai_analysis.py    — recovery index, drift detection
  hai_hypotheses.py  — гипотезы, протоколы, форматирование
  hai_reports.py     — утренний/еженедельный/ежемесячный отчёты
  hai_chat.py        — chat(), run_arbiter(), CLI
"""

# ── Core ──────────────────────────────────────────────────────────────────
from hai_core import (
    get_client,
    get_system_prompt,
    _build_system_prompt,
    _ensure_history_table,
    save_message,
    get_history,
    _strip_markdown,
    SYSTEM_PROMPT_FALLBACK,
    TZ,
    # ICLOUD снят 24.09 (BL-PUB-12): в hai_core это была мёртвая константа облачного пути, и
    # ни один читатель фасада её не брал (git grep `hai.ICLOUD`/`health_ai.ICLOUD` — пусто).
)

# ── Context ───────────────────────────────────────────────────────────────
from hai_context import (
    build_context_block_compact,
    HEALTH_TOOLS,
    _execute_tool,
    _detect_domains,
    build_smart_context,
    _DOMAIN_KEYWORDS,
    _FULL_CONTEXT_KEYWORDS,
)

# ── Analysis ──────────────────────────────────────────────────────────────
from hai_analysis import (
    compute_recovery_index,
    format_recovery_index,
    # `recovery_series` пропущена при заведении фасада — и это молчало (2026-08-11).
    # `brief_pipeline.py:95` зовёт `hai.recovery_series(...)`, ловит AttributeError
    # своим `except` и продолжает без карточки восстановления. Замер в логе бота
    # 11.08 08:30:07: «assemble_cards recovery провайдер упал: AttributeError».
    # Секция не собиралась неизвестно сколько дней, наружу не выходило ничего.
    recovery_series,
    detect_metric_drift,
    format_drift_report,
)

# ── Hypotheses & Protocols ────────────────────────────────────────────────
from hai_hypotheses import (
    save_hypothesis,
    get_open_hypotheses,
    update_hypothesis_status,
    generate_hypothesis_from_drift,
    confirm_hypothesis,
    reject_hypothesis,
    generate_protocol_from_hypothesis,
    save_protocol,
    get_active_protocols,
    retire_protocol,
    format_hypotheses_for_gp,
)

# ── Reports ───────────────────────────────────────────────────────────────
from hai_reports import (
    build_context_block,
    MORNING_PROMPT,
    generate_morning_report,
    WEEKLY_PROMPT,
    generate_weekly_report,
    MONTHLY_PROMPT,
    generate_monthly_check,
)

# ── Chat & Arbiter ────────────────────────────────────────────────────────
from hai_chat import (
    chat,
    chat_with_image,
    run_arbiter,
    judge_service_trouble,
    ARBITER_SYSTEM,
    ARBITER_PROMPT,
)
