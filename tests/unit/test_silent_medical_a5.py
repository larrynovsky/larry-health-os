"""A5 (22.09): медицинские датчики не выключаются молча — поведенческие контроли.

Структурный сторож (tests/consistency/test_silent_handler_guard.py) видит ФОРМУ обработчика.
Здесь — поведение на трёх случаях, где тишина стоила бы дороже всего: кардио-сенсор ЭКГ,
сенсор HV-6 и различение «нет таблицы у тенанта» от любой другой ошибки чтения.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _I(monkeypatch, cap):
    import integrity_tests as I
    monkeypatch.setattr(I, "warn", lambda n, d="": cap.append((n, d)))
    return I


def test_absent_table_отличает_отсутствие_от_поломки():
    """Законна только «no such table». Блокировка, порча, чужая колонка — находка."""
    import integrity_tests as I
    c = sqlite3.connect(":memory:")
    with pytest.raises(sqlite3.OperationalError) as absent:
        c.execute("SELECT * FROM нет_такой")
    assert I._absent_table(absent.value)
    c.execute("CREATE TABLE t (a)")
    with pytest.raises(sqlite3.OperationalError) as col:
        c.execute("SELECT b FROM t")                       # схема разошлась — не «нет таблицы»
    assert not I._absent_table(col.value)
    assert not I._absent_table(sqlite3.OperationalError("database is locked"))
    assert not I._absent_table(ValueError("no such table"))  # не та ошибка вообще


def test_экг_сенсор_кричит_если_модуль_не_импортировался(monkeypatch):
    """⭐ До A5: `except Exception: return` — AFib за 48ч молча не проверялся."""
    cap = []
    I = _I(monkeypatch, cap)
    monkeypatch.setitem(sys.modules, "ecg_db", None)        # import ecg_db → ImportError
    I.check_ecg_nonsinus()
    assert any("ЭКГ" in n for n, _ in cap), cap


def test_hv6_кричит_если_гипотезы_не_прочитаны(monkeypatch):
    """До A5: сбой чтения → `return 0`, то есть «0 просроченных» — неотличимо от нормы."""
    cap = []
    I = _I(monkeypatch, cap)

    def boom():
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(I.db, "get_hypotheses_awaiting_evaluation", boom)
    assert I.check_unresolved_evaluations() == 0
    assert any("HV-6" in n for n, _ in cap), cap
