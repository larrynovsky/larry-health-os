"""Датчик назывного долга канона: чистая логика отбора.

Предикат тестируется отдельно от обхода тенантов сознательно — `integrity_tests`
на импорте исполняет весь ночной монитор, поэтому предикат вынесен функцией и
судится на рукодельном входе.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

CANON = {"Calprotectin", "HGB", "Urine_Casts_other"}


def _debt(rows):
    from integrity_tests import canon_naming_debt
    return canon_naming_debt(rows, CANON)


def test_document_row_with_unknown_name_is_debt():
    assert _debt([("Некий аналит Z", "doc:fixture_analyte_z.pdf")]) == ["Некий аналит Z"]


def test_known_name_is_not_debt():
    assert _debt([("Calprotectin", "doc:x.pdf")]) == []


def test_questionnaire_scores_are_not_debt():
    """⭐ Баллы опросников канону не принадлежат (§16), и отсекает их ИСТОЧНИК,
    а не список имён: список разъехался бы с приёмом молча. Без этого теста
    ратчет замораживал бы законные строки вместе с настоящим долгом."""
    assert _debt([("isi_total", "instrument:isi"),
                  ("pro12_energy", "instrument:pro12"),
                  ("mfsi_sf_general", "instrument:mfsi_sf")]) == []


def test_debt_is_counted_by_names_not_rows():
    """Одно имя, приехавшее трижды, — один долг: иначе ратчет двигала бы частота
    сдачи, а не число незакрытых имён."""
    rows = [("Зонулин", "doc:a.pdf"), ("Зонулин", "doc:b.pdf"), ("Зонулин", "doc:c.pdf")]
    assert _debt(rows) == ["Зонулин"]
