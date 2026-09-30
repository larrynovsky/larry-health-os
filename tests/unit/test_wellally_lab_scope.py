"""Лаб-срез консилиума: без ручного списка имён, с объявленной границей окна.

Ручной список имён мог скрыть существующую строку канона: специалист ошибочно
объявлял анализ неизмеренным, GP повторял вывод, а сторож отчёта его отклонял.
Срез должен читать канон с явной границей окна, а не ограничиваться allowlist.
Имена, даты и значения в тестах ниже — вымышленные входы для этой регрессии.

Второй оракул здесь — про форму: со снятым списком в срез доезжают качественные строки
(value=None), и прежний рендер `f"{None:>8}"` уронил бы сборку контекста ВСЕГО
консилиума. Тест ломается именно на этом, а не на «где-то поменялся текст».
"""
from __future__ import annotations

from datetime import date

import pytest

pytestmark = pytest.mark.unit


def _minimal_identity(db):
    """Пакету нужна дата рождения — иначе он падает раньше лаб-блока, не на нём."""
    db.add_profile("identity.birth_date", value_text="1975-01-01", category="identity")


def test_specialists_see_canon_not_allowlist(db, clock):
    clock.set("2026-09-13")
    _minimal_identity(db)
    db.add_lab_result("2026-08-25", "PSA", 1.12, unit="ng/ml")
    db.add_lab_result("2021-03-15", "Insulin", 6.1, unit="мкЕд/мл")
    import wellally_consult as wc
    pkg = wc._build_data_package(end_date=date(2026, 9, 12), period_days=7)
    assert "PSA" in pkg                       # пример аналита за пределами прежнего allowlist
    assert "ГРАНИЦА ОКНА" in pkg              # граница объявлена в самом срезе
    assert "2021-03-15" in pkg                # забор за окном назван датой
    assert "Insulin" not in pkg               # но не именем: сырые написания за окном не раскрываются (§19)


def test_qualitative_row_does_not_break_the_package(db, clock):
    """Качественный результат (value=None) — строка канона, а не сбой сборки."""
    clock.set("2026-09-13")
    _minimal_identity(db)
    db.add_lab_result("2026-08-25", "Blood_group", None, value_text="B(III) Rh-")
    import wellally_consult as wc
    pkg = wc._build_data_package(end_date=date(2026, 9, 12), period_days=7)
    assert "Blood_group" in pkg
