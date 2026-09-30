"""Сторож не спорит с моделью, которая сказала правду.

Вымышленный профиль показывает окно 180 дней. Если запись старше окна,
фраза «нет данных за 180 дней» честна: сторож не должен подменять окно своим.

Второй контроль различает название аналита и прилагательное к другому имени.
Короткое окончание падежа допускается; длинный хвост прилагательного — нет.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_declared_window_is_judged_by_its_own_number(db, clock):
    """Строка старше ЗАЯВЛЕННОГО окна → правда, сторож молчит.

    Мутация: судить по window_days вместо числа из текста → краснеет.
    """
    clock.set("2032-04-12")
    db.add_lab_result("2031-12-27", "Magnesium", 0.93, unit="ммоль/л")   # 107 дней назад
    import gp_context as gc
    hits = gc.absent_claims_contradicted(
        "магний — нет данных за 90 дн.", {"Magnesium": "2031-12-27"}, window_days=730)
    assert hits == []


def test_declared_window_still_catches_a_row_inside_it(db, clock):
    """Позитивный контроль: строка ВНУТРИ заявленного окна — ложь, и она ловится."""
    clock.set("2032-04-12")
    import gp_context as gc
    hits = gc.absent_claims_contradicted(
        "магний — нет данных за 180 дн.", {"Magnesium": "2032-04-02"}, window_days=730)
    assert [h["test"] for h in hits] == ["Magnesium"]
    assert hits[0]["kind"] == "в окне"


def test_unnumbered_window_falls_back_to_guard_window(db, clock):
    """«в окне» без числа — судим окном сторожа, как раньше (поведение не потеряно)."""
    clock.set("2032-04-12")
    import gp_context as gc
    hits = gc.absent_claims_contradicted(
        "магний — нет данных в окне", {"Magnesium": "2032-04-02"}, window_days=730)
    assert [h["test"] for h in hits] == ["Magnesium"]


def test_adjective_is_not_the_analyte(db, clock):
    """«Магний (эритроцитарный)» — про магний, а не про эритроциты.

    Мутация: убрать фильтр прилагательных → в хитах появляется RBC.
    """
    clock.set("2032-04-12")
    import gp_context as gc
    hits = gc.absent_claims_contradicted(
        "магний (эритроцитарный) не сдавался ни разу",
        {"Magnesium": "2031-12-27", "RBC": "2032-04-02"})
    assert [h["test"] for h in hits] == ["Magnesium"]


def test_noun_case_still_matches(db, clock):
    """Позитивный контроль: падеж существительного фильтром не съеден."""
    clock.set("2032-04-12")
    import gp_context as gc
    hits = gc.absent_claims_contradicted(
        "ферритина не было ни разу", {"Ferritin": "2032-04-02"})
    assert [h["test"] for h in hits] == ["Ferritin"]
