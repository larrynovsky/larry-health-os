"""Детектор коллизии имён в дедупе staging (введён 2026-08-08).

Синтетическая коллизия: одинаковые дата и имя не доказывают идентичность
вещества. Разные вещества с таким ключом нельзя молча отправлять в rejected.
Числа и даты ниже придуманы независимо для проверки размерностей.
"""
from __future__ import annotations

import pytest

import lab_triage as lt

pytestmark = pytest.mark.unit

KEY_B12 = ("2021-09-08", lt.norm("Vitamin_B12"))
KEY_VITD = ("2021-09-08", lt.norm("Vitamin_D"))


def _row(name, value, unit, date="2021-09-08"):
    return {"canonical_name": name, "value": value, "unit": unit, "date": date,
            "value_agreement": "agree", "date_source": "read"}


def test_same_key_different_substance_goes_to_human():
    """⭐ Тот самый случай: активный B12 под именем общего. Ключ совпал, значение —
    нет. Отбрасывать нельзя, решает человек."""
    canon = {KEY_B12: (612.0, "pg/mL")}
    bucket, reason = lt._classify(_row("Vitamin_B12", 82.4, "pmol/L"), set(canon), canon)
    assert bucket == "review", f"настоящее измерение ушло в {bucket}: {reason}"
    assert "коллизи" in reason.lower(), reason


def test_same_measurement_in_si_units_is_still_a_duplicate():
    """НЕГАТИВНЫЙ КОНТРОЛЬ, без которого детектор был бы вредителем.

    `Vitamin_D` 58.3 нмоль/л и 23.3574 нг/мл — ОДНО измерение (÷2.496). Сравнение
    сырых чисел объявило бы коллизией каждую строку в СИ, и очередь человека
    забилась бы ложными вопросами — то есть детектор лечил бы одну немоту, заводя
    другую. Мутация «сравнивать value как есть, без to_conventional» роняет тест."""
    canon = {KEY_VITD: (23.3574, "ng/mL")}
    bucket, _ = lt._classify(_row("Vitamin_D", 58.3, "nmol/L"), set(canon), canon)
    assert bucket == "rejected", "пересчитываемая единица — не коллизия"


def test_exact_duplicate_still_rejected():
    """Позитив прежнего поведения: то же число — по-прежнему дубль."""
    canon = {KEY_B12: (612.0, "pg/mL")}
    bucket, _ = lt._classify(_row("Vitamin_B12", 612.0, "pg/mL"), set(canon), canon)
    assert bucket == "rejected"


def test_without_canon_values_behaviour_unchanged():
    """Совместимость: вызывающий, передавший только ключи, получает прежний вердикт.
    Иначе правка стала бы тихим изменением контракта для чужих вызывающих."""
    bucket, _ = lt._classify(_row("Vitamin_B12", 82.4, "pmol/L"), {KEY_B12})
    assert bucket == "rejected"
