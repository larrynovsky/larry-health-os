"""Census-датчик: TESTING_CONTRACTS.md реестр датчиков синхронен с integrity_tests.

Класс бага (обзор 2026-07-02): TESTING_CONTRACTS ручной прозой отстал от кода
на 2+ месяца — не знал check_lab_canon_health и др. Теперь секция генерируется
(gen_testing_contracts.py из AST). Этот тест ловит дрейф: новый check() в
integrity_tests без регенерации doc → fail. Симметрия census, как для
consilium_roster.
"""
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[2]


def test_generated_section_is_current():
    """gen_testing_contracts.py --check: секция AUTOGEN не устарела."""
    r = subprocess.run(
        [sys.executable, "gen_testing_contracts.py", "--check"],
        cwd=str(_REPO), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (
        "TESTING_CONTRACTS.md отстал от integrity_tests.py — "
        "запусти python3.11 gen_testing_contracts.py и закоммить.\n" + r.stdout)


def test_every_check_appears_in_doc():
    """Прямой census: каждая функция check_* из integrity_tests есть в doc."""
    import ast
    src = (_REPO / "integrity_tests.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    registered = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "check" and len(node.args) >= 2
                and isinstance(node.args[1], ast.Name)):
            registered.add(node.args[1].id)
    doc = (_REPO / "TESTING_CONTRACTS.md").read_text(encoding="utf-8")
    missing = sorted(fn for fn in registered if f"`{fn}`" not in doc)
    assert not missing, f"Датчики integrity_tests не в TESTING_CONTRACTS: {missing}"
