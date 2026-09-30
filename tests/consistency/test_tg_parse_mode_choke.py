"""Разметка Telegram уходит только через один чокпоинт с plain-повтором.

Зачем. Прямой вызов `send_message/reply_text/edit_message_text(..., parse_mode=...)`
с текстом, в котором есть данные или ответ модели (имя теста с `_`, непарная `*`),
получает BadRequest «can't parse entities» — и сообщение не доходит вовсе. Так
терялись: catch-up владельца 03.07, ревью конституций 25.09. Лечение одно —
`bot.utils.send_md` / `send_long` (повтор тем же текстом без разметки при ошибке
разбора). До 25.09 мимо чокпоинта шли 32 вызова в 8 файлах.

Сторож краснеет на НОВОМ обходе: вызове с parse_mode не-None вне bot/utils.py и
HTTP-теле с ключом "parse_mode" вне списка ниже. parse_mode=None разрешён — plain
не ломается. Красный — вопрос «почему не send_md», а не повод дописать строку.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

pytestmark = pytest.mark.consistency

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Сам чокпоинт.
ALLOW_CALL = {"bot/utils.py": "_md_call — единственный вызов с разметкой и plain-повтором"}
# HTTP-отправители мимо python-telegram-bot, у которых СВОЙ plain-повтор.
ALLOW_HTTP = {
    "generate_constitutions.py":
        "_tg_notify: Markdown, при HTTP 400 тот же кусок plain "
        "(тест test_constitutions_tg_fallback)",
}


_SKIP_TOP = {"tests", "plans", "archive", "data", "logs", "outputs", "node_modules"}


def _production_py():
    # Обход файлов, не `git ls-files`: полный прогон идёт в staging-копии без .git
    # (первый прогон 25.09 покраснел именно на этом, а не на нарушении).
    for p in ROOT.rglob("*.py"):
        parts = p.relative_to(ROOT).parts
        if parts[0] in _SKIP_TOP or any(x.startswith(".") or x == "__pycache__" for x in parts):
            continue
        yield "/".join(parts)


def violations(rel: str, src: str) -> list[str]:
    """Строки-нарушения файла (пусто = чисто). Отдельно — чтобы проверить на синтетике."""
    bad = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Call) and rel not in ALLOW_CALL:
            for k in n.keywords:
                if k.arg == "parse_mode" and not (
                        isinstance(k.value, ast.Constant) and k.value.value is None):
                    bad.append(f"{rel}:{n.lineno} вызов с parse_mode мимо send_md")
        if rel in ALLOW_HTTP:
            continue
        if isinstance(n, ast.Dict):
            if any(isinstance(k, ast.Constant) and k.value == "parse_mode" for k in n.keys):
                bad.append(f"{rel}:{n.lineno} HTTP-тело с parse_mode без plain-повтора")
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) \
                and n.slice.value == "parse_mode" and isinstance(n.ctx, ast.Store):
            bad.append(f"{rel}:{n.lineno} HTTP-тело с parse_mode без plain-повтора")
    return bad


def test_no_markup_send_outside_choke():
    bad = []
    for rel in _production_py():
        p = ROOT / rel
        if p.exists():
            bad += violations(rel, p.read_text(encoding="utf-8"))
    assert not bad, "Разметка мимо чокпоинта (используй bot.utils.send_md):\n" + "\n".join(bad)


def test_detector_sees_each_bypass_shape():
    """Отрицательный контроль: каждая форма обхода видна, plain — нет."""
    shapes = {
        "m.reply_text(t, parse_mode='Markdown')": 1,
        "bot.send_message(chat_id=1, text=t, parse_mode=ParseMode.MARKDOWN)": 1,
        "body = {'chat_id': 1, 'text': t, 'parse_mode': 'HTML'}": 1,
        "body['parse_mode'] = 'Markdown'": 1,
        "bot.send_message(chat_id=1, text=t, parse_mode=None)": 0,
        "send_md(m.reply_text, text=t)": 0,
    }
    for code, n in shapes.items():
        assert len(violations("x.py", code)) == n, code


def test_allowlist_entries_still_exist():
    for rel in (*ALLOW_CALL, *ALLOW_HTTP):
        assert (ROOT / rel).exists(), f"{rel} исчез — вычеркни из списка"
