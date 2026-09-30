"""
UC-H-02 — `user_qa.append()` строго до `_build_data_package()` и до раундов.

Источник: USE_CASES.md §4.H → UC-H-02.
Реализация: `wellally_consult.py:464 run_consultation_cycle_async`.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _load_function_node(filename: str, func_name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    src = (Path(__file__).parents[2] / filename).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            if node.name == func_name:
                return node
    raise AssertionError(f"function {func_name} not found in {filename}")


def _find_call_lines(node: ast.AST, func_pattern: str) -> list[int]:
    """Возвращает строки всех вызовов func_pattern (substring в repr).

    func_pattern — например 'user_qa.append', '_build_data_package',
    '_run_deliberation_round'.
    """
    lines: list[int] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            try:
                src = ast.unparse(child.func)
            except Exception:
                continue
            if func_pattern in src:
                lines.append(child.lineno)
    return sorted(lines)


def test_user_qa_append_before_data_package_rebuild():
    """
    append → _build_data_package (внутри ветки `if session.rounds:`)
    → _run_deliberation_round.
    """
    fn = _load_function_node("wellally_consult.py", "run_consultation_cycle_async")
    append_lines = _find_call_lines(fn, "user_qa.append")
    build_lines = _find_call_lines(fn, "_build_data_package")
    round_lines = _find_call_lines(fn, "_run_deliberation_round")

    assert append_lines, "user_qa.append не вызывается в run_consultation_cycle_async"
    assert build_lines, "_build_data_package не вызывается"
    assert round_lines, "_run_deliberation_round не вызывается"

    # append должен быть до КАЖДОГО _run_deliberation_round
    last_append = max(append_lines)
    first_round = min(round_lines)
    assert last_append < first_round, (
        f"user_qa.append (line {last_append}) должно быть ДО первого "
        f"_run_deliberation_round (line {first_round}). "
        f"Иначе специалисты теряют контекст диалога."
    )

    # И есть _build_data_package между append и round
    builds_after_append = [b for b in build_lines if b > last_append]
    assert builds_after_append, (
        "После user_qa.append должна быть пересборка _build_data_package, "
        "иначе data_package устарел"
    )
    assert builds_after_append[0] < first_round, (
        "Пересборка _build_data_package должна быть ДО _run_deliberation_round"
    )


def test_user_qa_append_inside_session_rounds_branch():
    """append вызывается только в ветке `if session.rounds`."""
    fn = _load_function_node("wellally_consult.py", "run_consultation_cycle_async")

    found_in_branch = False
    for child in ast.walk(fn):
        if isinstance(child, ast.If):
            # Проверяем условие — содержит ли session.rounds
            cond_src = ast.unparse(child.test) if hasattr(ast, "unparse") else ""
            if "session.rounds" in cond_src:
                # В теле этого if должен быть user_qa.append
                if _find_call_lines(child, "user_qa.append"):
                    found_in_branch = True
                    break

    assert found_in_branch, (
        "user_qa.append должен быть внутри `if session.rounds:` ветки. "
        "Без этой проверки append бы вызывался и при первом повороте, "
        "когда last_q ещё не существует."
    )


def test_round_b_uses_round_a_opinions():
    """
    Раунд B (`round_a_opinions=opinions_a`) вызывается ПОСЛЕ Раунда A
    (`round_a_opinions=None`). Это причинная связь: B видит A.
    """
    fn = _load_function_node("wellally_consult.py", "run_consultation_cycle_async")
    src = ast.unparse(fn) if hasattr(ast, "unparse") else ""

    # Простая проверка по тексту: есть оба варианта вызова
    assert "round_a_opinions=None" in src, "Раунд A (без opinions) не найден"
    assert "round_a_opinions=opinions_a" in src, "Раунд B (с opinions_a) не найден"

    # Порядок: round_a_opinions=None идёт ПЕРЕД round_a_opinions=opinions_a
    pos_a = src.find("round_a_opinions=None")
    pos_b = src.find("round_a_opinions=opinions_a")
    assert pos_a < pos_b, (
        "Раунд B (использует opinions_a) должен быть ПОСЛЕ Раунда A. "
        "Иначе нарушение причинного порядка."
    )
