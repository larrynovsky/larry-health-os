"""tests/consistency/test_intent_regen_controls.py — позитивные контроли датчиков.

RST: сторож без позитивного контроля декоративен. Эти тесты НЕ про реальность
проекта, а про ЧУВСТВИТЕЛЬНОСТЬ проверок из intent_registry: кормим нарочно
сломанным и убеждаемся, что провенанс-свежесть и status-honesty ловят слом.
Детерминированы, на синтетических записях — реальные файлы не трогают.
"""
from __future__ import annotations

import pytest

import intent_registry as ir

pytestmark = pytest.mark.consistency

_ENTRY = {
    "id": "demo",
    "intent": "Демонстрационная запись для контроля датчиков.",
    "invariants": [
        {"id": "held", "claim": "held-out на поздних данных", "status": "designed_not_built",
         "warm_guard": {"absent": ["проверяет на отложенн", "held-out реализован"]}},
    ],
}


def test_provenance_green_when_fresh():
    """Контроль-зелёный: страница со свежей меткой провенанса НЕ устарела."""
    page = ir.provenance_comment(_ENTRY) + "\n# Тело\nПределы честно названы.\n"
    assert not ir.provenance_stale(_ENTRY, page)


def test_provenance_red_when_registry_moved():
    """Позитивный контроль: сдвинули смысл записи → та же страница краснеет."""
    page = ir.provenance_comment(_ENTRY) + "\n# Тело\nПределы честно названы.\n"
    moved = {**_ENTRY, "intent": "Смысл записи стал другим."}
    assert ir.provenance_stale(moved, page)


def test_provenance_red_when_marker_absent():
    """Позитивный контроль: страница без метки провенанса считается устаревшей."""
    assert ir.provenance_stale(_ENTRY, "# Страница вообще без метки провенанса\n")


def test_provenance_ignores_warm_guard_and_check():
    """Провенанс НЕ реагирует на warm_guard/check (F6): правка датчика ≠ регенерация."""
    base = ir.entry_provenance(_ENTRY)
    tweaked = {**_ENTRY, "invariants": [
        {**_ENTRY["invariants"][0], "warm_guard": {"absent": ["иная фраза"]}}]}
    assert ir.entry_provenance(tweaked) == base


def test_status_honesty_green_when_clean():
    """Контроль-зелёный: честная проза (гап назван, не отмыт) не даёт срабатываний."""
    clean = "В гейте held-out ещё нет — только замысел в спецификации. Пределы названы."
    assert ir.status_laundering(_ENTRY, clean) == []


def test_status_honesty_red_when_laundered():
    """Позитивный контроль: непостроенное подано как работающее → датчик ловит."""
    dirty = "Гейт проверяет на отложенной выборке и подтверждает связь. Работает."
    hits = ir.status_laundering(_ENTRY, dirty)
    assert hits and hits[0].startswith("held:")
