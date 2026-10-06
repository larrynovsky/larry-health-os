"""Дата последнего PET в профиле берётся из документа-обследования (нить treatment-tails).

Синтетика. До 04.10.2026 бралось последнее событие, где «PET» встречался хоть где-то в
notes, — пересказ финансового документа выигрывал у самого обследования. Красный на старом коде.
"""
from __future__ import annotations

import sqlite3

import pytest

import profile_reconciler as pr

pytestmark = pytest.mark.unit


def _db(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE events (event_type TEXT, effective_date TEXT, notes TEXT, "
                 "attachments TEXT)")
    conn.executemany("INSERT INTO events VALUES (?,?,?,?)", rows)
    return conn


def test_пересказ_финансового_документа_не_становится_датой_обследования():
    conn = _db([
        ("encounter", "2031-04-26", "DOCS/PET 04.2031.pdf", "[]"),
        ("imaging", "2031-06-01", "Расчётный лист страховых выплат: PET-КТ и консультация", "[]"),
        ("encounter", "2031-07-01", "DOCS/Invoice-PET_CT_123.pdf", "[]"),
    ])
    assert pr._reconcile_pet(conn) == ("medical.last_pet_ct", "2031-04-26")


def test_имя_документа_из_attachments():
    conn = _db([("encounter", "2031-09-01", "заключение",
                 '"{\\"source_file\\": \\"DOCS/PET-CT 09.2031.pdf\\"}"')])
    assert pr._reconcile_pet(conn) == ("medical.last_pet_ct", "2031-09-01")


def test_нет_документа_обследования_профиль_не_трогаем():
    conn = _db([("imaging", "2031-06-01", "упоминание PET в тексте", "[]")])
    assert pr._reconcile_pet(conn) is None
