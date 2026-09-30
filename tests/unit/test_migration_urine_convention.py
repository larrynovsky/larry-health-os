"""Миграция конвенции имён мочи (2026-08-31, решение владельца: Urine_*).

Чистые предикаты — без БД. Если значения разных веществ совпадают,
сверка только по числу ошибочна: необходима карта идентичности вещества.
"""
import pytest

import migrations.urine_name_convention_20260831 as M

pytestmark = pytest.mark.unit


def _r(i, name, value, unit, specimen="urine", date="2021-06-14"):
    return {"id": i, "date": date, "test_name": name, "value": value,
            "unit": unit, "specimen": specimen, "source": "doc:x.pdf"}


def test_duplicate_found_through_unit_conversion():
    """Calcium 18.4321 mg/dL — конвертированный дубль Urine_Calcium 184.321 мг/л."""
    rows = [_r(1, "Calcium", 18.4321, "mg/dL"), _r(2, "Urine_Calcium", 184.321, "мг/л")]
    assert M.plan_duplicates(rows) == [(1, 2)]
    assert M.plan_renames(rows) == []          # дубль не переименовывается — удаляется


def test_raw_symbol_name_matches_by_map_not_by_value():
    """«Алюминий, Al» → Urine_Aluminium по СИМВОЛУ; нулевая кислота того же дня
    с тем же значением НЕ липнет к чужому нулю (негативный контроль карты)."""
    rows = [_r(1, "Алюминий, Al", 2.047, "мкг/л"), _r(2, "Urine_Aluminium", 2.047, "мкг/л"),
            _r(3, "Винная кислота (E334)", 0.0, "ммоль/моль креатинина"),
            _r(4, "Urine_Boron", 0.0, "мкг/л")]
    assert M.plan_duplicates(rows) == [(1, 2)]
    assert M.plan_renames(rows) == []          # кислота вне карты — не трогается


def test_plain_without_twin_is_renamed():
    """ММК мочи (ммоль/моль креатинина) двойника не имеет — переименование."""
    rows = [_r(1, "Methylmalonic_acid", 0.0, "ммоль/моль креатинина")]
    assert M.plan_duplicates(rows) == []
    assert M.plan_renames(rows) == [(1, "Methylmalonic_acid", "Urine_Methylmalonic_acid")]


def test_blood_rows_are_never_touched():
    """Кровь не судится вовсе: сывороточный кальций остаётся Calcium."""
    rows = [_r(1, "Calcium", 9.3, "mg/dL", specimen="blood"),
            _r(2, "Urine_Calcium", 184.321, "мг/л")]
    assert M.plan_duplicates(rows) == [] and M.plan_renames(rows) == []


def test_divergent_values_are_not_a_duplicate():
    """Значения не сходятся через конверсию → это ДВА измерения, оба живут."""
    rows = [_r(1, "Iron", 87.0, "ug/dL"), _r(2, "Urine_Iron", 312.518, "мкг/л")]
    assert M.plan_duplicates(rows) == []
    assert M.plan_renames(rows) == [(1, "Iron", "Urine_Iron")]


def test_sensor_flags_plain_and_silences_after(monkeypatch):
    """Тело ночного датчика (§20): plain-моча при известном Urine_-каноне — находка;
    кислота вне карты и Urine_*-строки молчат."""
    import sqlite3
    import integrity_tests as I
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE lab_results (test_name TEXT, specimen TEXT)")
    c.executemany("INSERT INTO lab_results VALUES (?,?)", [
        ("Calcium", "urine"), ("Urine_Iron", "urine"),
        ("Винная кислота (E334)", "urine"), ("Calcium", "blood")])
    cap = []
    monkeypatch.setattr(I, "warn", lambda n, d="": cap.append((n, d)))
    r = I.check_urine_name_convention(conn=c)
    assert r == {"plain_urine": 1}, r
    assert cap and "Calcium" in cap[0][1]
