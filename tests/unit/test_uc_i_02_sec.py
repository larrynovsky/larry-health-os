"""
UC-I-02 — Telegram fail-closed: только owner_chat_id() может взаимодействовать с ботом.

Источник: USE_CASES.md §4.I → UC-I-02 (alias `UC-SEC-001`).
План: tests/plans/UC-I-02.md.
Status: implemented · Confirmation: confirmed.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


# ── B (negative invariants): filter блокирует не-владельца ──────────────────


def test_owner_filter_passes_owner_chat_id(tg):
    """owner_chat_id() проходит через _owner_filter()."""
    from telegram_bot import _owner_filter, owner_chat_id
    update = tg.make_update(text="/report", chat_id=owner_chat_id())
    assert tg.filter_passes(_owner_filter(), update) is True


def test_owner_filter_blocks_other_chat_id(tg):
    """Главный инвариант UC-I-02: чужой chat_id заблокирован."""
    from telegram_bot import _owner_filter, owner_chat_id
    other = owner_chat_id() + 1
    update = tg.make_update(text="/report", chat_id=other)
    assert tg.filter_passes(_owner_filter(), update) is False


def test_owner_filter_blocks_far_random_chat_ids(tg):
    """Несколько разных не-owner chat_id — все блокируются."""
    from telegram_bot import _owner_filter, owner_chat_id
    for cid in [1, 999_999_999, owner_chat_id() - 1, owner_chat_id() + 1000]:
        if cid == owner_chat_id():
            continue
        update = tg.make_update(text="hi", chat_id=cid)
        assert tg.filter_passes(_owner_filter(), update) is False, \
            f"chat_id={cid} прошёл filter, должен быть заблокирован"


def test_owner_filter_blocks_negative_chat_id(tg):
    """Групповые чаты в TG имеют отрицательные chat_id."""
    from telegram_bot import _owner_filter
    update = tg.make_update(text="hi", chat_id=-1001234567890)
    assert tg.filter_passes(_owner_filter(), update) is False


# ── E (cross-check): owner_chat_id() реально загружен из secrets ──────────────


@pytest.mark.owner_env  # проверяет реальный owner chat_id > 0; в staging секреты фиктивны; nightly на каноне
@pytest.mark.owner_data
def test_owner_chat_id_loaded_from_secrets():
    """owner_chat_id() — int > 0 на каноне (секрет присутствует).

    Резолв ленивый (2026-07-17): импорт telegram_bot больше НЕ читает секрет.
    Fail-closed теперь на СТАРТЕ (assert_owner_configured в bot/main.main) и на
    ЧТЕНИИ (owner_chat_id() raise, не None) — не на импорте. См. bot/filters.py.
    """
    from telegram_bot import owner_chat_id
    assert isinstance(owner_chat_id(), int)
    assert owner_chat_id() > 0  # Telegram private chat_id всегда положителен


# ── E (cross-check): все handlers под owner-фильтром ────────────────────────


def _collect_add_handler_calls(source: str) -> list[ast.Call]:
    """AST-обход: возвращает все вызовы `*.add_handler(...)`."""
    tree = ast.parse(source)
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "add_handler":
                calls.append(node)
    return calls


def _has_owner_in_handler_call(call: ast.Call, source: str = "") -> bool:
    """
    Проверяет: в аргументе CommandHandler/MessageHandler есть `owner` —
    либо как `filters=owner` keyword, либо в позиционном выражении
    (например, `MessageHandler(filters.TEXT & owner, callback)` — где filter
    идёт первым позиционным аргументом).

    Для ConversationHandler — обёртка в `*_conv`, которая регистрируется
    отдельно (распознаём по имени-переменной).

    Для CallbackQueryHandler (BUG-DOCREV-OWNER-FILTER, 2026-05-12):
    `CallbackQueryHandler` не принимает `filters=`. Защита — inline в
    callback function: `if effective_chat.id != owner_chat_id(): return`.
    Если первый ast.Call — `CallbackQueryHandler(callback, ...)`, ищем
    body callback function в source и проверяем наличие owner_chat_id()
    check в первых строках.
    """
    if not call.args:
        return False
    first = call.args[0]

    # Case 1: app.add_handler(consult_conv) — это ConversationHandler;
    # его entry/end-handlers сами имеют filters=owner. Проверяется отдельно.
    if isinstance(first, ast.Name):
        return True

    if not isinstance(first, ast.Call):
        return False

    # Case 2: keyword `filters=owner` или `filters=filters.X & owner`
    for kw in first.keywords:
        if kw.arg == "filters" and _expr_references_owner(kw.value):
            return True

    # Case 3: позиционный — MessageHandler(filters.TEXT & owner, callback).
    # Первый позиционный аргумент — выражение фильтра. Проверяем его на owner.
    if first.args and _expr_references_owner(first.args[0]):
        return True

    # Case 4 (BUG-DOCREV-OWNER-FILTER): CallbackQueryHandler не принимает filters=.
    # Проверяем что callback function имеет inline owner_chat_id() check.
    handler_name = first.func.id if isinstance(first.func, ast.Name) else (
        first.func.attr if isinstance(first.func, ast.Attribute) else None
    )
    if handler_name == "CallbackQueryHandler" and first.args:
        callback_arg = first.args[0]
        if isinstance(callback_arg, ast.Name):
            callback_name = callback_arg.id
            if source and _callback_has_inline_owner_check(callback_name, source):
                return True

    return False


def _callback_has_inline_owner_check(callback_name: str, source: str) -> bool:
    """Ищет в source функцию <callback_name> и проверяет первые 10 строк
    тела на наличие owner_chat_id()-сравнения (паттерн UC-I-02 fail-closed).
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name != callback_name:
                continue
            # Сканируем первые 10 statement'ов тела (не глубоко в логику).
            for stmt in node.body[:10]:
                stmt_src = ast.unparse(stmt) if hasattr(ast, "unparse") else ""
                if "owner_chat_id" in stmt_src:
                    return True
            return False
    return False


_OWNER_FILTER_NAMES = {"owner", "owner_filter"}


def _expr_references_owner(node: ast.AST) -> bool:
    """Проверяет: выражение содержит `Name('owner')` или `Name('owner_filter')`
    где-то внутри. Sprint 6 ввёл handlers/*/register(app, owner_filter) — оба
    имени должны считаться валидными.
    """
    if isinstance(node, ast.Name) and node.id in _OWNER_FILTER_NAMES:
        return True
    if isinstance(node, ast.BinOp):
        return _expr_references_owner(node.left) or _expr_references_owner(node.right)
    if isinstance(node, ast.UnaryOp):
        return _expr_references_owner(node.operand)
    return False


def test_all_command_handlers_have_owner_filter():
    """
    AST-парсим все handlers/*.py + jobs/*.py + bot/main.py: для каждого
    add_handler с inline CommandHandler или MessageHandler — должен быть
    `filters=owner_filter` (или `... & owner_filter`), либо это
    CallbackQueryHandler с inline owner_chat_id()-check внутри callback.

    Если кто-то добавит новый handler без фильтра — тест краснеет.

    Sprint 6 (2026-05-23): handler-ы переехали из telegram_bot.py
    в handlers/*/register(app, owner_filter), параметр называется
    'owner_filter' (а не 'owner'). _expr_references_owner расширена
    обоими вариантами.
    """
    root = Path(__file__).parents[2]
    files_to_check = [
        root / "telegram_bot.py",
        root / "bot" / "main.py",
        *sorted((root / "handlers").glob("*.py")),
        *sorted((root / "jobs").glob("*.py")),
    ]
    files_to_check = [f for f in files_to_check if f.exists() and f.name != "__init__.py"]

    all_calls: list[tuple[str, ast.Call, str]] = []  # (file, call, source)
    for f in files_to_check:
        src = f.read_text(encoding="utf-8")
        for call in _collect_add_handler_calls(src):
            all_calls.append((f.name, call, src))

    assert all_calls, "не нашли ни одного add_handler — проблема с парсингом?"

    unprotected: list[str] = []
    for fname, call, src in all_calls:
        if not _has_owner_in_handler_call(call, source=src):
            line = call.lineno
            snippet = ast.unparse(call) if hasattr(ast, "unparse") else f"line {line}"
            unprotected.append(f"{fname}:{line}: {snippet[:120]}")

    assert not unprotected, (
        "Есть add_handler БЕЗ filters=owner/owner_filter:\n  "
        + "\n  ".join(unprotected)
    )


def test_conversation_handler_entry_points_have_owner_filter():
    """
    `ConversationHandler` (consult_conv) имеет entry_points и states —
    каждый CommandHandler внутри должен иметь filters=owner.
    """
    src = (Path(__file__).parents[2] / "telegram_bot.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    conv_handlers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "ConversationHandler":
                conv_handlers.append(node)

    if not conv_handlers:
        pytest.skip("ConversationHandler не используется")

    unprotected: list[str] = []
    for conv in conv_handlers:
        for kw in conv.keywords:
            # Ищем entry_points=[...] и fallbacks=[...]
            if kw.arg in ("entry_points", "fallbacks"):
                if isinstance(kw.value, ast.List):
                    for item in kw.value.elts:
                        if isinstance(item, ast.Call):
                            # Это CommandHandler(...)
                            has_owner = any(
                                k.arg == "filters" and _expr_references_owner(k.value)
                                for k in item.keywords
                            )
                            if not has_owner:
                                unprotected.append(
                                    f"line {item.lineno}: {kw.arg} без filters=owner"
                                )

    assert not unprotected, (
        "ConversationHandler entry_points/fallbacks без filters=owner:\n  "
        + "\n  ".join(unprotected)
    )


@pytest.mark.owner_env  # секрет присутствует только на каноне; в staging фиктивен
def test_start_gate_passes_on_canon():
    """Старт-гейт (fail-closed) проходит на каноне: секрет есть →
    assert_owner_configured() не райзит. Резолв ленивый (2026-07-17): импорт
    telegram_bot больше не читает секрет — fail-closed перенесён на СТАРТ.
    """
    import telegram_bot
    assert telegram_bot.CHAT_ID_FILE.exists(), \
        "CHAT_ID_FILE должен существовать на каноне"
    telegram_bot.assert_owner_configured()  # не райзит — владелец сконфигурирован
