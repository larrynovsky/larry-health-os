"""BL-SEX-LITERAL-1: пол в пакете специалистов (MDT, /consult) берётся из профиля.

До 24.09 `_build_data_package` писал «Пол: мужской» литералом для любого тенанта —
специалист читал женщину как мужчину. Пол не угадывается: без identity.sex — «пол не указан».
"""
from __future__ import annotations

from datetime import date

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("sex, want", [
    ("female", "Пол: женский"),
    ("male", "Пол: мужской"),
    (None, "Пол: пол не указан"),
])
def test_package_sex_comes_from_profile(db, clock, sex, want):
    clock.set("2026-09-13")
    db.add_profile("identity.birth_date", value_text="1975-01-01", category="identity")
    if sex:
        db.add_profile("identity.sex", value_text=sex, category="identity")
    import wellally_consult as wc
    pkg = wc._build_data_package(end_date=date(2026, 9, 12), period_days=7)
    assert want in pkg
