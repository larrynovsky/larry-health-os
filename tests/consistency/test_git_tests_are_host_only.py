"""Тест, который зовёт git, обязан нести метку host_only.

В образе контейнера git нет (замер 02.11.2026: `command -v git` → пусто, HEALTH_RUNTIME=container),
а conftest пропускает такие тесты там ТОЛЬКО по метке host_only (решение владельца 30.09, вариант Б:
признак — метка, не отсутствие git). Без метки тест краснеет в контейнерной установке, и красный
прогон уходит в /bug постороннего. Так было трижды: C-66, C-87 и ночной ремонт 02.11
(test_evidence_commit_must_be_in_history_not_just_in_store) — каждый раз метку ставили точечно,
и следующий такой тест рождался без неё. На 02.11 без метки было 12 тестов; поставлены все.

Что считается «зовёт git»: вызов subprocess.run/check_output/check_call/call/Popen, первый
аргумент которого — список с "git" впереди или строка "git …", прямо в тесте или в функции-
помощнике того же файла. Граница: git, вызванный через модуль проекта (git_facts, staged, …),
этот сторож не видит — он судит тесты, а не код проекта.
"""
from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
_SUBPROCESS = {"run", "check_output", "check_call", "call", "Popen"}


def _is_git_call(n: ast.AST) -> bool:
    if not isinstance(n, ast.Call) or not n.args:
        return False
    f = n.func
    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
    if name not in _SUBPROCESS:
        return False
    a = n.args[0]
    if isinstance(a, (ast.List, ast.Tuple)) and a.elts and isinstance(a.elts[0], ast.Constant):
        return a.elts[0].value == "git"
    return isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.startswith("git ")


def _marked(decorators) -> bool:
    return any("host_only" in ast.unparse(d) for d in decorators)


def unmarked_git_tests(root: Path = TESTS) -> list[str]:
    out = []
    for p in sorted(root.rglob("test_*.py")):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        module_marked = any(
            isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "pytestmark" for t in n.targets)
            and "host_only" in ast.unparse(n.value) for n in tree.body)
        helpers = {n.name for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and not n.name.startswith("test_")
                   and any(_is_git_call(c) for c in ast.walk(n))}

        def visit(body, class_marked):
            for n in body:
                if isinstance(n, ast.ClassDef):
                    visit(n.body, class_marked or _marked(n.decorator_list))
                elif isinstance(n, ast.FunctionDef) and n.name.startswith("test_"):
                    calls = any(_is_git_call(c) or (isinstance(c, ast.Call) and getattr(c.func, "id", "") in helpers)
                                for c in ast.walk(n))
                    if calls and not (module_marked or class_marked or _marked(n.decorator_list)):
                        out.append(f"{p.relative_to(root.parent)}::{n.name}")
        visit(tree.body, False)
    return out


def test_every_test_that_calls_git_is_host_only():
    bad = unmarked_git_tests()
    assert not bad, ("тесты зовут git без @pytest.mark.host_only — в контейнере git нет, "
                     f"они там красные: {bad}")


def test_detector_sees_an_unmarked_git_test(tmp_path):
    """Положительный контроль: сторож видит нарушителя и в помощнике, и в самом тесте."""
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "test_x.py").write_text(
        "import subprocess, pytest\n"
        "def _sh():\n    subprocess.run(['git', 'status'])\n"
        "def test_direct():\n    subprocess.check_output('git log', shell=True)\n"
        "def test_helper():\n    _sh()\n"
        "@pytest.mark.host_only\ndef test_ok():\n    _sh()\n", encoding="utf-8")
    assert unmarked_git_tests(tmp_path / "t") == ["t/test_x.py::test_direct", "t/test_x.py::test_helper"]
