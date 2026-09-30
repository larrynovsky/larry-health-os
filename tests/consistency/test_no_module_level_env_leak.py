"""Модуль теста не пишет в os.environ на уровне модуля — такая запись утекает на ВЕСЬ прогон.

Замер 2026-09-24 (приёмка урока установки свежим агентом): test_lab_recognizer_reconcile.py
на импорте делал os.environ.setdefault("HEALTH_DATA_DIR", tmp). У владельца переменная всегда
задана заранее — setdefault молчал; у постороннего по уроку (переменной нет) она утекала и роняла
3 теста в ДРУГИХ файлах, в зависимости от порядка сборки. Окружение тестов — дело conftest и
monkeypatch, а не импорта модуля.
"""
import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.consistency

TESTS = Path(__file__).resolve().parents[1]

# Осознанные исключения — с причиной; причина читается при ревью.
ALLOWED = {
    "consistency/test_intent_page_delta.py":
        "стаб секретов ТОЛЬКО при их отсутствии, объяснение и граница утечки — в самом файле",
}


def _writes_env(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Store) and "os.environ" in ast.unparse(n.value):
            return True
        if isinstance(n, ast.Call):
            f = ast.unparse(n.func)
            if f.startswith("os.environ.") and f.split(".")[-1] in {"setdefault", "update", "pop", "__setitem__", "clear"}:
                return True
            if f in {"os.putenv", "os.unsetenv"}:
                return True
    return False


def _leaks(src: str) -> list[int]:
    out = []
    for node in ast.parse(src).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if _writes_env(node):
            out.append(node.lineno)
    return out


def test_детектор_ловит_утечку_и_не_ловит_запись_внутри_функции():
    assert _leaks('import os\nos.environ.setdefault("X", "1")\n') == [2]
    assert _leaks('import os\nos.environ["X"] = "1"\n') == [2]
    assert _leaks('import os\ndef f():\n    os.environ["X"] = "1"\n') == []
    assert _leaks('import os\nY = os.environ.get("X")\n') == []


def test_ни_один_модуль_тестов_не_пишет_окружение_на_импорте():
    bad = {}
    for p in sorted(TESTS.rglob("*.py")):
        rel = p.relative_to(TESTS).as_posix()
        if p.name == "conftest.py" or rel in ALLOWED:
            continue
        try:
            lines = _leaks(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        if lines:
            bad[rel] = lines
    assert not bad, f"запись в os.environ на уровне модуля (утечёт на весь прогон): {bad}"
