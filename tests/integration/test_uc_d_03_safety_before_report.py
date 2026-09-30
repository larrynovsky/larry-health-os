"""
UC-D-03 — Срочный safety alert отправляется до основного отчёта.

Источник: USE_CASES.md §3.D → UC-D-03.
Status: `partial`.

Полный e2e — с моком telegram_bot.send_morning_report и safety_net.
Здесь — smoke на наличие нужной логики в коде.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_telegram_bot_sends_safety_before_report():
    """
    AST/grep: в `send_morning_report` safety_net check должен быть до
    основного `send_long(report)`.

    Sprint 6 C10 (2026-05-23): send_morning_report переехал в jobs/scheduled.py.
    """
    src = (Path(__file__).parents[2] / "jobs" / "scheduled.py").read_text(encoding="utf-8")

    # Минимально: есть упоминание urgent/critical safety и оно ДО send_long
    safety_idx = -1
    for marker in ["sn_result", "urgent", "critical", "safety_net"]:
        idx = src.find(marker)
        if idx != -1:
            safety_idx = idx
            break

    send_long_idx = src.find("send_long(context.bot")
    if send_long_idx == -1:
        send_long_idx = src.find("await send_long")

    assert safety_idx != -1, "safety_net логика не найдена в telegram_bot.py"
    assert send_long_idx != -1, "send_long не найден"
    # Safety должен идти ДО send_long в исходнике (порядок исполнения)
    assert safety_idx < send_long_idx, \
        "safety_net проверка должна быть ДО основного отчёта"


def test_safety_net_invocation_in_morning_flow():
    """В send_morning_report должен быть вызов safety_net.

    Sprint 6 C10 (2026-05-23): send_morning_report переехал в jobs/scheduled.py.
    """
    src = (Path(__file__).parents[2] / "jobs" / "scheduled.py").read_text(encoding="utf-8")
    # Якорь на ОПРЕДЕЛЕНИЕ функции, не на первое упоминание имени (первое — в docstring
    # модуля: окно 3000 символов от него зависело от того, что между docstring и телом
    # ничего не вставили — брифовый tz-хелпер сломал эту случайность 2026-07-17).
    # Тело функции целиком через AST, не окно в N символов: окно 3000 ломалось от любой
    # вставки выше вызова (второй раз — 27.09, замер лага Oura; класс BL-TEST-ANCHOR-1).
    import ast
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "send_morning_report")
    flow_block = ast.get_source_segment(src, fn)
    assert ("safety" in flow_block.lower() or
            "urgent" in flow_block.lower() or
            "sn_result" in flow_block), \
        "safety_net не вызывается в send_morning_report flow"
