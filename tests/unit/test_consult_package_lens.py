"""Пакет специалистов (MDT, /consult): строки профиля — по данным тенанта, не шаблоном.

До 24.09 «Диагноз / Лечение / Последний ПЭТ-КТ / Устройства» печатались всем: человеку без
диагноза специалисты читали «Последний ПЭТ-КТ ([дата не задана]): [результат не задан]» —
онко-рамку от шаблона, а при живых данных Apple Health — «Устройства: данные недоступны».
"""
from __future__ import annotations

from datetime import date

import pytest

pytestmark = pytest.mark.unit

END = date(2026, 9, 12)


def _pkg(db, clock):
    clock.set("2026-09-13")
    db.add_profile("identity.birth_date", value_text="1975-01-01", category="identity")
    import wellally_consult as wc
    return wc._build_data_package(end_date=END, period_days=7)


def test_no_diagnosis_no_onco_frame(db, clock):
    db.add_daily_metrics("2026-09-10", stand_min=12.0)
    pkg = _pkg(db, clock)
    for frame in ("ПЭТ-КТ", "Диагноз:", "Лечение:", "данные недоступны"):
        assert frame not in pkg, f"шаблонная строка без данных: {frame}"
    assert "Данные за 30 дней приходят от: Apple Health" in pkg


def test_diagnosis_and_pet_shown_when_present(db, clock):
    db.add_profile("medical.diagnosis", value_text="диагноз-X", category="medical")
    db.add_profile("medical.last_pet_ct", value_text="2026-05-01", category="medical")
    db.add_profile("medical.last_pet_ct_result", value_text="без очагов", category="medical")
    db.add_profile("medical.devices", value_text="кольцо, стимулятор", category="medical")
    db.add_daily_metrics("2026-09-10", readiness=80)
    pkg = _pkg(db, clock)
    assert "Диагноз (профиль): диагноз-X" in pkg
    assert "Последний ПЭТ-КТ (2026-05-01): без очагов" in pkg
    assert "приходят от: Oura" in pkg
    assert "Устройства (со слов, профиль): кольцо, стимулятор" in pkg


def test_no_data_says_so(db, clock):
    assert "приходят от: ни одного источника" in _pkg(db, clock)
