"""
UC-J-05 (proposed) — contract-test для save_agent_report.

Источник: USE_CASES.md UC-J-05 (Internal API drift watcher).

Идея: сигнатура `health_db.save_agent_report` имеет 8 required-параметров.
Если кто-то добавит ещё один required, AST-сканер находит все callers и
проверяет, что каждый передаёт все required args (через kwargs или positional).

Это **дисциплинарный тест**, не behavioural. Прямое следствие BUG-AGENTREPORTS-SIG
2026-05-09 — `morning_test_summary.py` молча падал на missing args.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[2]


def _required_args(fn) -> list[str]:
    """Имена required-аргументов функции (без default)."""
    sig = inspect.signature(fn)
    return [
        name for name, param in sig.parameters.items()
        if param.default is inspect.Parameter.empty
        and param.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    ]


def _find_calls(source_path: Path, func_name: str) -> list[ast.Call]:
    """AST-вызовы где func.attr == func_name (например db.save_agent_report)."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        # db.save_agent_report(...) → Attribute
        if isinstance(f, ast.Attribute) and f.attr == func_name:
            out.append(node)
        # save_agent_report(...) (через from health_db import ...) → Name
        elif isinstance(f, ast.Name) and f.id == func_name:
            out.append(node)
    return out


def test_save_agent_report_callers_pass_all_required_args():
    """Все вызовы save_agent_report передают все required args (kwargs или positional)."""
    import sys
    sys.path.insert(0, str(ROOT))
    import health_db

    required = _required_args(health_db.save_agent_report)
    assert len(required) >= 8, (
        f"Ожидалось ≥8 required-параметров (на 2026-05-09 их 8), "
        f"получили {len(required)}: {required}. Сигнатура изменилась — обнови тест."
    )

    # Все .py в health_scripts (кроме тестов и venv).
    files = [
        p for p in ROOT.rglob("*.py")
        if "tests" not in p.parts
        and "__pycache__" not in p.parts
        and ".venv" not in p.parts
        and "venv" not in p.parts
    ]

    failures = []
    for src in files:
        for call in _find_calls(src, "save_agent_report"):
            # Имена kwargs
            kwarg_names = {kw.arg for kw in call.keywords if kw.arg}
            # Положительные args покрывают начало required
            n_pos = len(call.args)
            covered_by_pos = set(required[:n_pos])
            covered = covered_by_pos | kwarg_names

            missing = [r for r in required if r not in covered]
            if missing:
                failures.append(
                    f"  {src.name}:{call.lineno} — отсутствуют: {missing}"
                )

    assert not failures, (
        "Callers save_agent_report не передают все required args:\n"
        + "\n".join(failures)
        + "\n\nЭтот тест ловит регрессию класса BUG-AGENTREPORTS-SIG (2026-05-09)."
    )


def test_signature_drift_detection_smoke():
    """Sanity: required-имена совпадают с известным набором на 2026-05-09."""
    import sys
    sys.path.insert(0, str(ROOT))
    import health_db

    required = _required_args(health_db.save_agent_report)
    expected_subset = {
        "agent_type", "agent_name", "date_str",
        "has_findings", "data_queried", "pubmed_ids",
        "peers_reviewed", "changes_summary", "findings",
    }
    missing = expected_subset - set(required)
    assert not missing, (
        f"Сигнатура регрессировала — пропали required: {missing}. "
        f"Если это намеренное послабление контракта — обнови expected_subset."
    )
