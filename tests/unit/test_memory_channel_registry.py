"""tests/unit/test_memory_channel_registry.py — M6 датчик каналов сырого транскрипта.

Guard → два acceptance (правило #5): clean→0, injected→1 + census + scan-регресс.
"""
from __future__ import annotations
from pathlib import Path
import pytest

pytestmark = pytest.mark.unit

import memory_channel_registry as M


def test_real_repo_clean():
    """clean → 0: все читатели сырого conversation_history зарегистрированы."""
    root = Path(M.__file__).resolve().parent
    assert M.collect_channel_findings(root) == []


def test_injected_unregistered_flagged():
    """injected → 1: новый читатель вне реестра → находка + его имя."""
    f = M.audit_channels({"evil_mod.leaky_reader": {"file": "evil_mod.py", "line": 1}})
    assert f and any("evil_mod.leaky_reader" in x for x in f)


def test_census_stale_registry_flagged():
    """Census-симметрия: registered без реальной функции → реестр протух."""
    assert any("протух" in x for x in M.audit_channels({}))


def test_scan_catches_new_raw_reader(tmp_path):
    """Регресс: функция, читающая conversation_history, ловится сканером."""
    (tmp_path / "hai_core.py").write_text(
        "def sneaky():\n    return 'SELECT * FROM conversation_history'\n", encoding="utf-8")
    sc = M._scan_raw_readers(tmp_path)
    assert "hai_core.sneaky" in sc


def test_scan_ignores_unrelated_functions(tmp_path):
    """FP-контроль: функция без conversation_history не ловится."""
    (tmp_path / "hai_core.py").write_text(
        "def ok():\n    return 'SELECT * FROM daily_metrics'\n", encoding="utf-8")
    assert M._scan_raw_readers(tmp_path) == {}
