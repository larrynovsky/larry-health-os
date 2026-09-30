"""Unit-тесты на test_failure_handler.py — обработчик ночных failures."""
from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent

import pytest

pytestmark = pytest.mark.unit


# Импорт модуля (он рядом с tests/, а path в conftest)
import test_failure_handler as tfh


@pytest.fixture(autouse=True)
def _no_production_log(tmp_path, monkeypatch):
    """§20 (23.09): тесты не пишут в боевой logs/test_failure_handler.log. До этого фикстуры
    лог накопил 874 фальшивые строки «AUTO retry test_z::test_concurrent_write» с 08.05."""
    monkeypatch.setattr(tfh, "LOGS_DIR", tmp_path / "_logs")
    (tmp_path / "_logs").mkdir()


JUNIT_TEMPLATE = '''<?xml version="1.0"?>
<testsuites>
<testsuite name="pytest" tests="{tests}" failures="{failures}" errors="{errors}">
{cases}
</testsuite>
</testsuites>
'''


CASE_OK = '<testcase classname="tests.unit.test_x" name="test_pass"/>'

CASE_FAIL = dedent('''
<testcase classname="tests.unit.test_uc_i_02_sec" name="test_unauthorized_chat_blocked">
  <failure message="AssertionError: filter passed unauthorized chat">
    Traceback (most recent call last):
      File "tests/unit/test_uc_i_02_sec.py", line 12, in test_unauthorized_chat_blocked
        assert filter_passes is False
    AssertionError: filter passed unauthorized chat
  </failure>
</testcase>
''').strip()

CASE_DB_LOCKED = dedent('''
<testcase classname="tests.integration.test_z" name="test_concurrent_write">
  <failure message="OperationalError: database is locked">
    sqlite3.OperationalError: database is locked
  </failure>
</testcase>
''').strip()


def _write_junit(reports_dir: Path, layer: str, cases: list[str],
                  failures: int = 0) -> Path:
    junit = reports_dir / f"{layer}_junit.xml"
    junit.write_text(JUNIT_TEMPLATE.format(
        tests=len(cases), failures=failures, errors=0,
        cases="\n".join(cases),
    ), encoding="utf-8")
    return junit


def test_handle_no_failures(tmp_path: Path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    _write_junit(reports_dir, "unit", [CASE_OK], failures=0)

    res = tfh.handle_test_failures(reports_dir, dry_run=True)
    assert res.critical == []
    assert res.auto_fixed == []
    assert res.flaky == []


def test_handle_db_locked_pattern_marked_retry(tmp_path: Path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    _write_junit(reports_dir, "integration", [CASE_DB_LOCKED], failures=1)

    res = tfh.handle_test_failures(reports_dir, dry_run=True,
                                   rerun=lambda f: ("passed", "повтор отдельно прошёл"))
    assert len(res.auto_fixed) == 1
    assert "database_locked" in res.auto_fixed[0]
    assert res.critical == []


def test_handle_critical_unknown_uc(tmp_path: Path, monkeypatch):
    """
    Failing test без записи в USE_CASES.md → CRITICAL по дефолту.
    Это test против UC-I-02, а реальный USE_CASES.md его содержит как
    'implemented'. Чтобы тест был детерминирован — подменим SCRIPT_DIR.
    """
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    _write_junit(reports_dir, "unit", [CASE_FAIL], failures=1)

    # Подменяем SCRIPT_DIR на пустую — USE_CASES.md не найдётся → unknown → critical
    monkeypatch.setattr(tfh, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(tfh, "DIAGNOSIS_CACHE", tmp_path / "diag_cache")
    (tmp_path / "diag_cache").mkdir(exist_ok=True)

    res = tfh.handle_test_failures(reports_dir, dry_run=True)
    assert len(res.critical) == 1
    assert res.critical[0].name == "test_unauthorized_chat_blocked"


def test_uc_status_lookup_implemented():
    """Прямая проверка _uc_status_for_test против реального USE_CASES.md."""
    # UC-I-02 == implemented по нашему каталогу
    status = tfh._uc_status_for_test("test_uc_i_02_sec.py")
    assert status == "implemented"


def test_uc_status_lookup_partial():
    """UC-A-02 == partial (классификация не-лаб PDF, частичная)."""
    status = tfh._uc_status_for_test("test_uc_a_02_classify")
    assert status == "partial"


def test_uc_status_lookup_implemented_after_fix():
    """UC-I-03 был partial, после W2A-1 фикса → implemented."""
    status = tfh._uc_status_for_test("test_uc_i_03_null")
    assert status == "implemented"


def test_uc_status_lookup_no_match():
    status = tfh._uc_status_for_test("test_random_module")
    assert status == "unknown"


def test_idempotent_via_flag(tmp_path: Path, monkeypatch):
    """Второй вызов в тот же день — no-op (через flag-файл)."""
    monkeypatch.setattr(tfh, "LOGS_DIR", tmp_path / "logs")
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(tfh, "SCRIPT_DIR", tmp_path)

    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    _write_junit(reports_dir, "unit", [CASE_DB_LOCKED], failures=1)

    # Первый вызов — НЕ dry_run, ставит flag
    tfh.handle_test_failures(reports_dir, dry_run=False, rerun=lambda f: ("passed", "ok"))
    flag = tmp_path / "logs" / f"test_failure_done_{tfh.get_today()}.flag"
    assert flag.exists()

    # Второй — должен быть no-op
    res = tfh.handle_test_failures(reports_dir, dry_run=False)
    assert res.auto_fixed == []
    assert res.critical == []


def test_failure_entry_pattern_match():
    """Проверка regex-паттернов отдельно."""
    f = tfh.FailureEntry(
        layer="integration",
        classname="x",
        name="test_y",
        message="OperationalError: database is locked",
        longrepr="sqlite3.OperationalError: database is locked",
    )
    match = f.matches_pattern()
    assert match is not None
    assert match[0] == "database_locked"
    assert match[1] == "retry"


def test_failure_entry_no_pattern():
    f = tfh.FailureEntry(
        layer="unit",
        classname="x",
        name="test_y",
        message="AssertionError: expected 5 got 3",
        longrepr="",
    )
    assert f.matches_pattern() is None


# ── Оракул повтора (23.09, flaky_pattern_can_swallow_a_real_failure) ─────────
CASE_TIMEOUT = dedent('''
<testcase classname="tests.unit.test_llm_x" name="test_answer">
  <failure message="anthropic.APITimeoutError: Request timed out">
    anthropic.APITimeoutError: connection timeout
  </failure>
</testcase>
''').strip()


@pytest.mark.parametrize("outcome", ["failed", "inconclusive"])
def test_паттерн_не_глотает_падение_если_повтор_не_прошёл(tmp_path: Path, monkeypatch, outcome):
    """⭐ Настоящая поломка с «таймаутным» текстом: повтор упал (или не состоялся) → падение
    идёт дальше в разбор, а не в «случайное сегодня». До 23.09 оно уходило молча."""
    monkeypatch.setattr(tfh, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(tfh, "DIAGNOSIS_CACHE", tmp_path / "diag_cache")
    (tmp_path / "diag_cache").mkdir()
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    _write_junit(reports_dir, "unit", [CASE_TIMEOUT, CASE_DB_LOCKED], failures=2)
    res = tfh.handle_test_failures(reports_dir, dry_run=True, rerun=lambda f: (outcome, "x"))
    assert res.flaky == [] and res.auto_fixed == []
    assert len(res.critical) + len(res.expected_gap) == 2
    assert all("паттерн" in f.message for f in res.critical + res.expected_gap)


def test_паттерн_и_прошедший_повтор_остаётся_случайностью(tmp_path: Path):
    """Позитивный контроль: без него тест выше зеленел бы и на обработчике, который не
    признаёт случайным ничего."""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    _write_junit(reports_dir, "unit", [CASE_TIMEOUT], failures=1)
    res = tfh.handle_test_failures(reports_dir, dry_run=True, rerun=lambda f: ("passed", "ok"))
    assert len(res.flaky) == 1 and res.critical == []


def _mini_repo(tmp_path: Path, body: str) -> Path:
    d = tmp_path / "repo" / "tests" / "unit"
    d.mkdir(parents=True)
    (d / "test_probe_mini.py").write_text(body, encoding="utf-8")
    return tmp_path / "repo"


@pytest.mark.parametrize("body, expect", [
    ("def test_answer():\n    assert True\n", "passed"),
    ("def test_answer():\n    assert False\n", "failed"),
])
def test_настоящий_повтор_через_pytest(tmp_path: Path, monkeypatch, body, expect):
    """Оракул в той форме, в какой он бежит ночью: настоящий подпроцесс pytest по node id,
    собранному из junit classname. Не мок — именно этот путь и был пустой заглушкой."""
    monkeypatch.setattr(tfh, "SCRIPT_DIR", _mini_repo(tmp_path, body))
    f = tfh.FailureEntry(layer="unit", classname="tests.unit.test_probe_mini",
                         name="test_answer", message="timeout")
    assert tfh._node_id(f) == "tests/unit/test_probe_mini.py::test_answer"
    assert tfh._rerun_isolated(f)[0] == expect


def test_повтор_без_файла_не_случайность(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(tfh, "SCRIPT_DIR", tmp_path)
    f = tfh.FailureEntry(layer="unit", classname="tests.unit.нет_такого", name="t", message="m")
    assert tfh._rerun_isolated(f)[0] == "inconclusive"


def test_утро_берёт_вердикт_ночи_а_не_повторяет(tmp_path: Path, monkeypatch):
    """У вердикта «случайно» один дом — ночной повтор. Утренняя сводка читает его и не
    перезапускает тесты; нет записи — падение не признаётся случайным."""
    monkeypatch.setattr(tfh, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(tfh, "DIAGNOSIS_CACHE", tmp_path / "diag_cache")
    (tmp_path / "diag_cache").mkdir()
    monkeypatch.setattr(tfh, "_send_telegram", lambda *a, **k: None)
    monkeypatch.setattr(tfh, "_create_reminder", lambda *a, **k: True)
    monkeypatch.setattr(tfh, "_diagnose_failure", lambda *a, **k: None)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    _write_junit(reports_dir, "unit", [CASE_TIMEOUT], failures=1)
    night = tfh.handle_test_failures(reports_dir, dry_run=False, rerun=lambda f: ("passed", "ok"))
    assert len(night.flaky) == 1
    calls = []
    morning = tfh.handle_test_failures(reports_dir, dry_run=True, rerun=tfh.cached_rerun(reports_dir))
    assert len(morning.flaky) == 1 and not calls
    empty = tmp_path / "empty"
    empty.mkdir()
    _write_junit(empty, "unit", [CASE_TIMEOUT], failures=1)
    res = tfh.handle_test_failures(empty, dry_run=True, rerun=tfh.cached_rerun(empty))
    assert res.flaky == [], "без ночного вердикта совпавшее падение не должно стать случайным"
