"""
N2-extended (Wave 3-DOC v2): параметризованные contract-тесты для всех
публичных функций health_db с required-параметрами.

Расширение `test_save_agent_report_contract.py`. Вместо одного теста для
одной функции — параметризация по всем функциям с required-args, найденным
динамически через inspect.signature(health_db.<name>).

Зачем: после Consolidation 2026-05-11 (TD-12..16) health_db получил
~10 новых публичных функций (get_lab_refs, upsert_profile, get_config,
get_conit_limit, get_domain_signals, get_patient_profile, etc.). Каждая
может стать источником регрессии класса BUG-AGENTREPORTS-SIG (2026-05-09 —
caller потерял required arg, баг проявился молча в production).

При добавлении новой публичной функции с required-args тест **автоматически**
её подхватит. Никаких manual updates не нужно.

Контракт: каждый caller должен передавать все required-args (positional
или через kwargs). Передача через **kwargs splat — допустима (не можем
статически верифицировать).

Связанные документы:
  - CLAUDE.md §5 — правило контракт-тестов
  - tests/unit/test_save_agent_report_contract.py — оригинальный шаблон
    (содержит дополнительный signature_drift smoke-test для save_agent_report,
    оставлен для обратной совместимости)
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import health_db  # noqa: E402


def _public_functions_with_required() -> list[tuple[str, list[str]]]:
    """Все публичные функции health_db с required-параметрами.

    Возвращает [(func_name, required_arg_names), ...]. Отфильтровано:
    - имена с _ (private)
    - не-callable атрибуты
    - функции без required-args
    - служебные (self/cls)
    """
    out: list[tuple[str, list[str]]] = []
    for name in dir(health_db):
        if name.startswith("_"):
            continue
        attr = getattr(health_db, name)
        if not callable(attr):
            continue
        # Не модули, не классы (пока)
        if inspect.ismodule(attr) or inspect.isclass(attr):
            continue
        try:
            sig = inspect.signature(attr)
        except (ValueError, TypeError):
            continue
        required = [
            n for n, p in sig.parameters.items()
            if p.default is inspect.Parameter.empty
            and p.kind in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            )
        ]
        if not required or required in (["self"], ["cls"]):
            continue
        out.append((name, required))
    return sorted(out)


@pytest.fixture(scope="module")
def caller_asts() -> dict[Path, ast.AST]:
    """Закешированные AST всех .py в проекте (исключая tests/, __pycache__, .venv).

    Парсится один раз, переиспользуется параметризованными тестами.
    """
    asts: dict[Path, ast.AST] = {}
    for p in ROOT.rglob("*.py"):
        if "tests" in p.parts:
            continue
        if "__pycache__" in p.parts:
            continue
        if ".venv" in p.parts or "venv" in p.parts:
            continue
        try:
            asts[p] = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        except SyntaxError:
            # Битый файл — пропустим, не блокируем тесты
            continue
    return asts


def _find_calls(tree: ast.AST, func_name: str) -> list[ast.Call]:
    """AST.Call где func.attr == func_name (db.X) или func.id == func_name (import X)."""
    out: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == func_name:
            out.append(node)
        elif isinstance(f, ast.Name) and f.id == func_name:
            out.append(node)
    return out


# Параметризация по всем публичным функциям с required-args.
# При добавлении новой функции — тест её подхватит автоматически.
PARAMS = _public_functions_with_required()


def test_param_list_is_nonempty():
    """Sanity: parametrize-список не пустой (защита от пустого scan)."""
    assert PARAMS, "Не найдено публичных функций с required-args — что-то сломалось"
    assert len(PARAMS) >= 30, (
        f"Ожидалось ≥30 функций с required-args, получили {len(PARAMS)}. "
        f"Возможно, health_db регрессировал к старой архитектуре."
    )


# Known regressions — true-positives, ожидающие фикса.
# Каждый key = func_name, value = причина xfail + ссылка на BACKLOG.
# Снимать запись после фикса в коде → тест автоматически вернётся в strict-PASS.
KNOWN_REGRESSIONS: dict[str, str] = {
    # save_checkin: BUG-CHECKIN-SIG закрыт 2026-05-12 (checkin_agent.py:_finalize).
}


@pytest.mark.parametrize(
    "func_name,required",
    PARAMS,
    ids=[p[0] for p in PARAMS],
)
def test_health_db_callers_pass_required_args(
    func_name: str,
    required: list[str],
    caller_asts: dict[Path, ast.AST],
    request: pytest.FixtureRequest,
):
    if func_name in KNOWN_REGRESSIONS:
        request.node.add_marker(
            pytest.mark.xfail(reason=KNOWN_REGRESSIONS[func_name], strict=True)
        )
    """Все callers <func_name> передают все required args (positional или kwargs).

    Допущения:
    - **kwargs splat (например ``func(**ctx)``) — пропускается, статически
      не верифицируется.
    - positional args покрывают начало required-списка по порядку.
    """
    failures: list[str] = []
    for path, tree in caller_asts.items():
        for call in _find_calls(tree, func_name):
            # **kwargs splat — пропускаем (статически нельзя верифицировать).
            has_splat = any(kw.arg is None for kw in call.keywords)
            if has_splat:
                continue

            kwarg_names = {kw.arg for kw in call.keywords if kw.arg}
            n_pos = len(call.args)
            covered = set(required[:n_pos]) | kwarg_names
            missing = [r for r in required if r not in covered]
            if missing:
                failures.append(
                    f"  {path.relative_to(ROOT)}:{call.lineno} — missing {missing}"
                )

    assert not failures, (
        f"Callers {func_name!r} не передают все required {required}:\n"
        + "\n".join(failures)
        + f"\n\nДобавь missing args в caller, либо измени сигнатуру "
        f"{func_name} с явным default."
    )
