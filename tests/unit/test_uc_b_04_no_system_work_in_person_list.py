"""UC-B-04 — работа системы не кладётся в список задач человека (решение владельца 28.09).

До 28.09 утренний отчёт заводил человеку задачи «показатель стабильно ниже нормы — нужно
разобрать изменения» / «ухудшается — проверить причины» (type=analysis), а обновление генома —
«пересмотреть гипотезы» (type=review). Исполнителя у них не было: у владельца закрывались
пустой галочкой, у партнёра висели с июля. Решение владельца «вариант А»: тренды — в разбор
(gp_context._build_trends_block), в списке человека — только то, что он может сделать сам.

Проверка по источнику: ни один производственный вызов save_task не создаёт задачу с типом
из SYSTEM_WORK. Граница вслух: тип, переданный переменной, этот сторож не видит.
"""
import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
SYSTEM_WORK = {"analysis", "review"}


def _task_types(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "save_task":
            for kw in node.keywords:
                if kw.arg == "type_" and isinstance(kw.value, ast.Constant):
                    yield node.lineno, kw.value.value


def test_no_production_code_puts_system_work_into_the_person_list():
    bad = []
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", "plans/", ".")) or "/." in rel:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        bad += [f"{rel}:{ln} type_={t!r}" for ln, t in _task_types(tree) if t in SYSTEM_WORK]
    assert not bad, "задачи-работа системы в списке человека:\n  " + "\n  ".join(bad)


def test_the_guard_sees_a_planted_call(tmp_path):
    """Негативный контроль: подложенный вызов сторож находит."""
    tree = ast.parse('db.save_task(source="x", type_="analysis", content="y")')
    assert list(_task_types(tree)) == [(1, "analysis")]
