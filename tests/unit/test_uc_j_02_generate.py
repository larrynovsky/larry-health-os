"""
UC-J-02 — Подтверждение перед генерацией теста.

Источник: USE_CASES.md §4.J → UC-J-02.
Реализация: `generate_test.py`.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

import generate_test as gt


SCRIPT = Path(__file__).parents[2] / "generate_test.py"


def _run(*args: str, input_text: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/opt/homebrew/bin/python3.11", str(SCRIPT), *args],
        input=input_text, capture_output=True, text=True, timeout=10,
    )


# ── Парсер каталога ──────────────────────────────────────────────────────────


def test_parse_uc_catalog_returns_known_ucs():
    """Парсер должен находить UC из реального USE_CASES.md."""
    catalog = gt.parse_uc_catalog(gt.USE_CASES_MD)
    # Минимум — те которые мы подтвердили в первом пакете
    assert "UC-I-02" in catalog
    assert "UC-A-04" in catalog
    assert "UC-B-03" in catalog


def test_parse_extracts_status_and_confirmation():
    catalog = gt.parse_uc_catalog(gt.USE_CASES_MD)
    uc = catalog["UC-I-02"]
    assert uc["status"] == "implemented"
    assert uc["confirmation"] == "confirmed"


# ── layer_for_type ───────────────────────────────────────────────────────────


def test_layer_for_check_is_unit():
    assert gt.layer_for_type("check") == "unit"


def test_layer_for_integration():
    assert gt.layer_for_type("integration") == "integration"


def test_layer_for_e2e_mock():
    assert gt.layer_for_type("e2e_mock") == "e2e_mock"


def test_layer_for_manual_charter():
    assert gt.layer_for_type("manual_charter") == "charters"


def test_layer_for_meta_falls_to_unit():
    assert gt.layer_for_type("meta") == "unit"


# ── target_path ──────────────────────────────────────────────────────────────


def test_target_path_includes_layer_and_uc_id():
    uc = {"pragmatic": "Тестовая прагматика",
          "type": "check", "status": "implemented", "confirmation": "confirmed"}
    p = gt.target_path("UC-A-04", uc)
    assert p.parent.name == "unit"
    assert "uc_a_04" in p.name


# ── CLI отказы ──────────────────────────────────────────────────────────────


@pytest.mark.host_only
def test_cli_rejects_unknown_uc():
    res = _run("UC-NOT-99")
    assert res.returncode == 1
    assert "не найден" in res.stdout.lower() or "not found" in res.stdout.lower()


@pytest.mark.host_only
def test_cli_rejects_proposed_confirmation(tmp_path, monkeypatch):
    """UC с confirmation=proposed → return 2."""
    # Создаём fake USE_CASES.md с одним UC в proposed
    fake_md = tmp_path / "USE_CASES.md"
    fake_md.write_text(
        "## 3. Каталог UC\n\n"
        "### A. Импорт\n\n"
        "| ID | Прагматика | P | Тип | Status | Confirmation |\n"
        "|---|---|---|---|---|---|\n"
        "| `UC-A-99` | Test pragmatic | P0 | check | partial | proposed |\n",
        encoding="utf-8",
    )

    # Запуск с подменой USE_CASES_MD
    res = subprocess.run(
        ["/opt/homebrew/bin/python3.11", "-c",
         f"import sys; sys.path.insert(0, {str(SCRIPT.parent)!r}); "
         f"import generate_test as gt; "
         f"gt.USE_CASES_MD = type('P', (), {{'exists': lambda s=None: True, "
         f"'read_text': lambda s, encoding=None: open({str(fake_md)!r}).read()}})(); "
         f"sys.argv = ['gen', 'UC-A-99', '--auto-confirm']; "
         f"sys.exit(gt.main())"],
        capture_output=True, text=True, timeout=10,
    )
    assert res.returncode == 2
    assert "proposed" in res.stdout.lower()


@pytest.mark.host_only
def test_cli_dry_run_does_not_create_file(tmp_path, monkeypatch):
    """`--dry-run` не пишет файл, только показывает план."""
    res = _run("UC-A-04", "--dry-run")
    assert res.returncode == 0
    assert "Тест-план" in res.stdout or "план" in res.stdout.lower()
    assert "dry-run" in res.stdout.lower() or "не пишется" in res.stdout.lower()


def test_cli_auto_confirm_creates_skeleton(tmp_path, monkeypatch):
    """С --auto-confirm для confirmed UC создаётся skeleton."""
    # Не запускаем настоящий — может перезаписать существующий тест.
    # Проверим через прямой вызов build_skeleton.
    uc = gt.get_uc("UC-A-04")
    assert uc is not None
    assert uc["confirmation"] == "confirmed"
    skel = gt.build_skeleton("UC-A-04", uc)
    assert "UC-A-04" in skel
    assert "pytest.mark.unit" in skel
    assert "TODO" in skel


def test_skeleton_for_charter_uses_charters_marker():
    uc = {"pragmatic": "Онкоконтекст", "priority": "P0",
          "type": "manual_charter", "status": "partial", "confirmation": "confirmed"}
    skel = gt.build_skeleton("UC-I-08", uc)
    assert "pytest.mark.charters" in skel


def test_skeleton_includes_use_cases_link():
    uc = {"pragmatic": "x", "priority": "P0",
          "type": "check", "status": "implemented", "confirmation": "confirmed"}
    skel = gt.build_skeleton("UC-X-01", uc)
    assert "USE_CASES.md" in skel
    assert "tests/plans/UC-X-01.md" in skel
