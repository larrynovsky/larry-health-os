"""Дата анализа — кодом из напечатанной формы (нить lab-date-order, 2026-10-04).

Замер 04.10: бланк печатал дату получения материала днём вперёд, модель сама перевела её в ISO
с переставленными днём и месяцем (здесь — синтетический пример 07/11/2025 → 2025-07-11). Перестановка дня и месяца правдоподобна и ничем не
выделяется: тренд просто съезжает на четыре месяца. Теперь модель отдаёт дату как
напечатана, а порядок день/месяц код берёт из однозначных дат того же документа. Нечем
решить — строка уходит человеку (ambiguous_order), а не угадывается.
"""
from __future__ import annotations

import json

import pytest

import lab_recognizer as lr

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("printed, order", [
    (["Date of Birth: 25/03/1990", "07/11/2025"], "dmy"),   # 25 стоит только на месте дня
    (["12/24/2024"], "mdy"),
    (["25.03.1990", "12/24/2024"], None),                  # документ противоречит себе — не решаем
    (["07/11/2025", "04/10/2025"], None),                  # одни неоднозначные — нечем решить
    ([], None),
])
def test_date_order(printed, order):
    assert lr._date_order(printed) == order


@pytest.mark.parametrize("printed, order, expected", [
    ("07/11/2025", "dmy", ("2025-11-07", "by_order")),
    ("07/11/2025", "mdy", ("2025-07-11", "by_order")),
    ("07/11/2025", None, (None, "ambiguous")),
    ("25/03/1990", None, ("1990-03-25", "unambiguous")),
    ("12/24/2024", None, ("2024-12-24", "unambiguous")),
    ("05.05.23", None, ("2023-05-05", "unambiguous")),     # день = месяц: порядок не важен
    ("31/02/2024", "dmy", (None, "unparsed")),              # такой даты нет — не выдумываем
    ("2025-11-07", "dmy", (None, "unparsed")),              # ISO не трогаем: модель уже права по форме
    (None, "dmy", (None, "unparsed")),
])
def test_printed_iso(printed, order, expected):
    assert lr._printed_iso(printed, order) == expected


def _recognize(monkeypatch, tmp_path, rows, page_text):
    monkeypatch.setattr(lr, "_render_pages", lambda p, pages=None: [b"A"])
    monkeypatch.setattr(lr, "_vision_call", lambda img, prompt, model: [dict(r) for r in rows])
    monkeypatch.setattr(lr, "_doc_page_texts", lambda p: page_text)
    return lr.recognize(tmp_path / "x.pdf", "2030-01-01")["tests"]


ROW = {"canonical_name": "Glucose", "raw_name": "Glucose", "value": 5.0, "unit": "mmol/L",
       "date": "2025-07-11", "date_printed": "07/11/2025"}   # модель переставила день и месяц


def test_swapped_date_fixed_by_unambiguous_date_in_same_document(monkeypatch, tmp_path):
    t = _recognize(monkeypatch, tmp_path, [ROW], ["Date of Birth : 25/03/1990   Date received : 07/11/2025"])[0]
    assert t["date"] == "2025-11-07" and t["date_source"] == "read"
    assert json.loads(t["field_evidence"])["date"] == ["2025-07-11", "07/11/2025"]


def test_without_evidence_the_date_goes_to_a_human(monkeypatch, tmp_path):
    t = _recognize(monkeypatch, tmp_path, [ROW], [])[0]
    assert t["date_source"] == "ambiguous_order"           # lab_triage: не read → человеку
    assert t["date"] == "2025-07-11"                       # не угадываем, оставляем прочитанное


def test_row_without_printed_date_keeps_old_behaviour(monkeypatch, tmp_path):
    row = {k: v for k, v in ROW.items() if k != "date_printed"}
    t = _recognize(monkeypatch, tmp_path, [row], [])[0]
    assert t["date"] == "2025-07-11" and t["date_source"] == "read"


def test_page_neighbour_of_an_ambiguous_date_is_ambiguous_too(monkeypatch, tmp_path):
    neighbour = {"canonical_name": "Sodium", "raw_name": "Na", "value": 140.0, "unit": "mmol/L"}
    tests = _recognize(monkeypatch, tmp_path, [ROW, neighbour], [])
    by = {t["canonical_name"]: t for t in tests}
    assert by["Sodium"]["date"] == "2025-07-11"
    assert by["Sodium"]["date_source"] == "ambiguous_order"
