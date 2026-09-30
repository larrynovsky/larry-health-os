"""Датчик покрытия конверсии единиц — исполнение ТЕЛА (§20), не только чистой
`unit_convergence_gaps`. До 2026-08-31 тело не исполнялось ни одним тестом;
в тот же день оно стало читать колонку `specimen` — ошибка в SQL доехала бы
только до ночного прогона.
"""
import sqlite3

import pytest

import integrity_tests as I

pytestmark = pytest.mark.unit


def _conn(rows):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE lab_results (test_name TEXT, unit TEXT, value REAL, specimen TEXT)")
    c.executemany("INSERT INTO lab_results VALUES (?,?,?,?)", rows)
    c.commit()
    return c


def test_specimen_from_column_separates_urine_from_blood(monkeypatch):
    """Придуманные измерения: колонка материала разделяет кровь и мочу.
    Имена аналитов и единицы сохраняют ветки проверки конверсии."""
    cap = []
    monkeypatch.setattr(I, "warn", lambda n, d="": cap.append((n, d)))
    r = I.check_unit_conversion_coverage(conn=_conn([
        ("Phosphorus", "mg/dL", 4.8, "blood"),
        ("Phosphorus", "мкг/л", 920.0, "urine"),
        ("Aldosterone", "пг/мл", 65.0, "blood"),
        ("Aldosterone", "нг/мл", 0.065, "blood"),
    ]))
    assert r == {"gaps": 0}, (r, cap)
    assert cap == []


def test_real_gap_is_still_named(monkeypatch):
    """Негативный контроль: единицы одного материала, не сводимые правилом, — находка."""
    cap = []
    monkeypatch.setattr(I, "warn", lambda n, d="": cap.append((n, d)))
    r = I.check_unit_conversion_coverage(conn=_conn([
        ("Phosphorus", "mg/dL", 4.8, "blood"),
        ("Phosphorus", "мкг/л", 920.0, "blood"),     # та же строка, но якобы кровь
    ]))
    assert r == {"gaps": 1}
    assert cap and "Phosphorus" in cap[0][1]
