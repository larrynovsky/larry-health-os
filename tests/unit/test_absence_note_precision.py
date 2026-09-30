"""Поправка, которую ЧИТАЕТ человек, не утверждает лишнего.

Найдено испытанием 14.09 прогоном модели: фраза «Витамин B6 (пиридоксаль-5-фосфат)
— не сдавался» давала три хита (B6, A, E), потому что синонимы всех трёх начинаются
со слова «витамин». У GP это безобидно (хит = перегенерировать), у чата и брифа
поправка печатается человеку — и лишний хит становится ложным фактом в тексте.

Сторож имеет право подозревать шире, чем утверждать: хиты остаются все (их читает
лог и датчик), в текст едет только названный в клаузе.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_note_names_only_the_analyte_actually_mentioned(db, clock):
    """Мутация: убрать _best_named_per_clause из judge_absence_claims → краснеет."""
    clock.set("2026-09-14")
    db.add_lab_result("2021-06-14", "Vitamin_B6", 14.2, unit="мкг/л")
    db.add_lab_result("2021-06-14", "Vitamin_A", 0.61, unit="мг/л")
    db.add_lab_result("2021-06-14", "Vitamin_E", 11.7, unit="мг/л")
    import gp_context as gc
    hits, note = gc.judge_absence_claims("витамин B6 не сдавался ни разу")
    assert "Vitamin_B6" in note
    assert "Vitamin_A" not in note and "Vitamin_E" not in note
    # Подозрение шире утверждения: хиты остаются все, они нужны логу и датчику.
    assert len(hits) >= 1


def test_single_hit_passes_through(db, clock):
    """Позитивный контроль: одиночный хит не теряется по дороге."""
    clock.set("2026-09-14")
    db.add_lab_result("2026-09-01", "TSH", 1.2, unit="мкМЕ/мл")
    import gp_context as gc
    hits, note = gc.judge_absence_claims("ТТГ никогда не сдавался")
    assert "TSH" in note and "2026-09-01" in note


def test_two_distinct_analytes_in_one_clause_both_survive(db, clock):
    """Граница: если в клаузе НАЗВАНЫ оба имени, оба обязаны остаться в поправке.

    Иначе фильтр точности превратился бы в глушитель: «B12 и фолат не проверены» —
    это две настоящие лжи, а не одна с шумом.
    """
    clock.set("2026-09-14")
    db.add_lab_result("2026-06-01", "Vitamin_B12", 400, unit="пг/мл")
    db.add_lab_result("2026-06-01", "Folate", 8.0, unit="нг/мл")
    import gp_context as gc
    hits, note = gc.judge_absence_claims("витамин B12 и фолат не проверены ни разу")
    assert "Vitamin_B12" in note and "Folate" in note
