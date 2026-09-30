"""
Дубль ратчета check_contracts §[6] как pytest-тест (2026-06-28, план v2).

Зачем дубль: сам ратчет живёт в check_contracts.py, который гоняет только
run_checks (post-commit). Этот тест переносит ту же проверку в pytest, чтобы
её подхватывали и 07:50-scheduled, и nightly-suite — даже если post-commit
не отработал (--no-verify, MacBook offline и т.п.). «Кто стережёт сторожа».

Инвариант: контракт каждого <domain>_db.py = его ре-экспорт
`from <domain>_db import (...)` в health_db. Необъявленная публичная функция
домена = протечка.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _reexports() -> dict[str, set]:
    text = (ROOT / "health_db.py").read_text(encoding="utf-8")
    out: dict[str, set] = {}
    for n in ast.parse(text).body:
        if isinstance(n, ast.ImportFrom) and n.module and n.module.endswith("_db"):
            out.setdefault(n.module, set()).update(a.name for a in n.names)
    return out


def test_domain_modules_have_no_undeclared_public():
    violations: dict[str, list] = {}
    for mod, declared in _reexports().items():
        f = ROOT / f"{mod}.py"
        assert f.exists(), f"{mod}: ре-экспорт в health_db есть, а файла нет"
        pub = {n.name for n in ast.parse(f.read_text(encoding="utf-8")).body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and not n.name.startswith("_")}
        undeclared = sorted(pub - declared)
        if undeclared:
            violations[mod] = undeclared
    assert not violations, (
        "Необъявленные публичные функции в доменных модулях "
        "(не в ре-экспорте health_db): "
        f"{violations}. Либо ре-экспортни их (= объяви контрактом), либо _private."
    )
