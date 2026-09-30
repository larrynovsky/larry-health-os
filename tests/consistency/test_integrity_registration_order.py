"""tests/consistency/test_integrity_registration_order.py

Дублирует pre-commit forward-ref-линт (scripts/check_integrity_registration_order.py) в pytest,
чтобы класс ловился и в test_on_studio — даже если pre-commit обойдён (--no-verify /
HEALTH_ALLOW_STUDIO_COMMIT / авто-бэкап). Класс: check() в integrity_tests зарегистрирован ДО
определения хелпера (напр. _iter_tenant_ro) → NameError в мониторе (reschedule-инцидент 2026-07-17).

RED-first: второй тест подсовывает синтетический forward-ref и убеждается, что линт его ловит.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LINT = ROOT / "scripts" / "check_integrity_registration_order.py"


def _run(path: Path | None = None):
    args = [sys.executable, str(LINT)] + ([str(path)] if path else [])
    return subprocess.run(args, capture_output=True, text=True)


@pytest.mark.consistency
def test_integrity_no_forward_ref():
    """Канонический integrity_tests.py — 0 forward-ref (все check() после хелперов)."""
    r = _run()
    assert r.returncode == 0, f"forward-ref в integrity_tests:\n{r.stdout}\n{r.stderr}"


@pytest.mark.consistency
def test_lint_catches_forward_ref(tmp_path):
    """RED-first: check() использует хелпер, определённый НИЖЕ регистрации → линт падает exit 1."""
    broken = tmp_path / "broken.py"
    broken.write_text(
        "def check_x():\n"
        "    return _late_helper()\n\n"
        "check('x', check_x)\n\n"
        "def _late_helper():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    r = _run(broken)
    assert r.returncode == 1, f"линт НЕ поймал forward-ref (exit={r.returncode}):\n{r.stdout}"
    assert "_late_helper" in r.stdout


@pytest.mark.consistency
def test_lint_catches_undefined_helper(tmp_path):
    """RED-first (B1): fn у check() ВЫЗЫВАЕТ имя, не связанное НИГДЕ в модуле → линт exit 1.
    Шире forward-ref: ловит опечатку/удалённый хелпер (гарантированный NameError при исполнении),
    статически, без БД → работает в pre-commit на MacBook."""
    broken = tmp_path / "broken_undef.py"
    broken.write_text(
        "def check_y():\n"
        "    return _typoed_helper()\n\n"
        "check('y', check_y)\n",
        encoding="utf-8",
    )
    r = _run(broken)
    assert r.returncode == 1, f"линт НЕ поймал undefined helper (exit={r.returncode}):\n{r.stdout}"
    assert "_typoed_helper" in r.stdout


@pytest.mark.consistency
def test_lint_allows_builtins_and_imports(tmp_path):
    """Контроль ложных срабатываний: fn вызывает builtin/импорт/локаль → линт НЕ падает."""
    ok = tmp_path / "clean.py"
    ok.write_text(
        "import json\n\n"
        "def _helper():\n"
        "    return 1\n\n"
        "def check_z():\n"
        "    x = len([1, 2])\n"
        "    return json.dumps(_helper()) + str(x)\n\n"
        "check('z', check_z)\n",
        encoding="utf-8",
    )
    r = _run(ok)
    assert r.returncode == 0, f"ложное срабатывание на builtin/import/локали:\n{r.stdout}"
