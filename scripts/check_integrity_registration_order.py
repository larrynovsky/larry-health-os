#!/usr/bin/env python3.11
"""check_integrity_registration_order.py — MacBook-safe pre-commit страж integrity_tests.

Закрывает Studio-only ограничение runtime code-guard (test_on_studio шаг 5): тот ловит ВСЕ код-ошибки,
но только на Studio при запуске test_on_studio. Пропустил его — сломанный датчик доедет до ночного
монитора (алерт человеку). Этот линт ловит СТАТИЧЕСКИ (AST, без БД/Studio → работает в pre-commit
на MacBook) два класса гарантированного NameError при исполнении check(label, fn):

  1. forward-ref: fn использует модуль-уровневый хелпер, определённый НИЖЕ регистрации check()
     (класс, что укусил 2026-07-17 reschedule: check() исполняет fn сразу → NameError).
  2. undefined-call (B1): fn ВЫЗЫВАЕТ имя, не связанное НИГДЕ в модуле и не builtin
     (опечатка / удалённый хелпер) → NameError при исполнении check().

НЕ покрывает: несовпадение сигнатур, битые импорты, динамический getattr — их ловит runtime
code-guard на Studio (test_on_studio шаг 5) + ночной монитор. Оба класса выше РЕАЛЬНО прошли бы
pre-commit до этого линта.

Exit: 0 — чисто; 1 — есть нарушение (перечислены с указанием, что чинить).
"""
import ast
import builtins
import sys
from pathlib import Path

# Путь можно переопределить аргументом (для тестов); по умолчанию — канонический integrity_tests.py.
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "integrity_tests.py"
tree = ast.parse(SRC.read_text(encoding="utf-8"), str(SRC))

# 1. Модуль-уровневые имена → строка первого определения (def/assign/annassign/import).
module_defs: dict[str, int] = {}
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        module_defs.setdefault(node.name, node.lineno)
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name):
                module_defs.setdefault(t.id, node.lineno)
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        module_defs.setdefault(node.target.id, node.lineno)
    elif isinstance(node, ast.Import):
        for a in node.names:
            module_defs.setdefault(a.asname or a.name.split(".")[0], node.lineno)
    elif isinstance(node, ast.ImportFrom):
        for a in node.names:
            module_defs.setdefault(a.asname or a.name, node.lineno)

funcs = {n.name: n for n in tree.body
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _arg_names(fn) -> set[str]:
    names = {a.arg for a in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs)}
    if fn.args.vararg:
        names.add(fn.args.vararg.arg)
    if fn.args.kwarg:
        names.add(fn.args.kwarg.arg)
    return names


# Суперсет ИМЁН, СВЯЗАННЫХ где-либо в модуле (builtins ∪ def/class ∪ import ∪ любой Store ∪
# global ∪ аргументы любых функций). Вызов имени ВНЕ него = гарантированный NameError.
all_bound: set[str] = set(dir(builtins))
for sub in ast.walk(tree):
    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
        all_bound.add(sub.name)
        all_bound |= _arg_names(sub)
    elif isinstance(sub, ast.ClassDef):
        all_bound.add(sub.name)
    elif isinstance(sub, ast.Import):
        for a in sub.names:
            all_bound.add(a.asname or a.name.split(".")[0])
    elif isinstance(sub, ast.ImportFrom):
        for a in sub.names:
            all_bound.add(a.asname or a.name)
    elif isinstance(sub, ast.Global):
        all_bound.update(sub.names)
    elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
        all_bound.add(sub.id)


def _loaded_module_names(fn) -> dict[str, int]:
    """Bare-Name LOAD в теле fn, исключая параметры/локальные присваивания/локальные импорты."""
    local = _arg_names(fn)
    loaded: dict[str, int] = {}
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Assign):
            for t in sub.targets:
                if isinstance(t, ast.Name):
                    local.add(t.id)
        elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
            local.add(sub.target.id)
        elif isinstance(sub, ast.Import):
            for a in sub.names:
                local.add(a.asname or a.name.split(".")[0])
        elif isinstance(sub, ast.ImportFrom):
            for a in sub.names:
                local.add(a.asname or a.name)
        elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
            loaded.setdefault(sub.id, sub.lineno)
    return {n: ln for n, ln in loaded.items() if n not in local}


def _called_names(fn) -> dict[str, int]:
    """Имена, ВЫЗЫВАЕМЫЕ как X(...) в теле fn (func=Name), исключая локали/аргументы."""
    local = _arg_names(fn)
    called: dict[str, int] = {}
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Assign):
            for t in sub.targets:
                if isinstance(t, ast.Name):
                    local.add(t.id)
        elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
            local.add(sub.target.id)
        elif isinstance(sub, (ast.Import, ast.ImportFrom)):
            for a in sub.names:
                local.add(a.asname or (a.name.split(".")[0] if isinstance(sub, ast.Import) else a.name))
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
            if sub.func.id not in local:
                called.setdefault(sub.func.id, sub.lineno)
    return called


forward_refs = []   # (reg_line, fn_name, helper, def_line)
undefined = []      # (reg_line, fn_name, name, use_line)
for node in tree.body:
    if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name) and node.value.func.id == "check"
            and len(node.value.args) >= 2 and isinstance(node.value.args[1], ast.Name)):
        reg_line = node.lineno
        fn = funcs.get(node.value.args[1].id)
        if not fn:
            continue
        for helper, _use in _loaded_module_names(fn).items():
            def_line = module_defs.get(helper)
            if def_line and def_line > reg_line:
                forward_refs.append((reg_line, fn.name, helper, def_line))
        for name, use_line in _called_names(fn).items():
            if name not in all_bound:
                undefined.append((reg_line, fn.name, name, use_line))

if forward_refs or undefined:
    if forward_refs:
        print("❌ integrity registration-order: check() зарегистрирован ДО определения хелпера "
              "(forward-ref → NameError в мониторе, класс reschedule-инцидента):")
        for reg, fn_name, helper, dl in forward_refs:
            print(f"   строка {reg}: check(..., {fn_name}) использует '{helper}', "
                  f"определённый НИЖЕ на строке {dl} → перенеси регистрацию ниже {dl}.")
    if undefined:
        print("❌ integrity registration-order: check() вызывает имя, не связанное НИГДЕ в модуле "
              "(опечатка / удалённый хелпер → NameError при исполнении):")
        for reg, fn_name, name, ul in undefined:
            print(f"   строка {reg}: check(..., {fn_name}) вызывает '{name}()' (строка {ul}), "
                  f"которого нет в модуле → опечатка или удалённый хелпер.")
    sys.exit(1)

print(f"✅ integrity registration-order: 0 forward-ref, 0 undefined-call "
      f"({len(funcs)} функций).")
sys.exit(0)
