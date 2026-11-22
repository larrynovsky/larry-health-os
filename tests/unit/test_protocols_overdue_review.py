"""Предупреждение о просроченном пересмотре называет протоколы и срок (карточка 2026-11-12).

Ночная строка «4 протоколов с просроченным review_date — требуют пересмотра» не несла
ни названий, ни сроков, и вопрос владельцу из неё не складывался. Значения выдуманы.
"""
from __future__ import annotations

from datetime import date

import pytest

import protocols_db

pytestmark = pytest.mark.unit

TODAY = date(2026, 11, 12)


def test_называет_протокол_и_дни_просрочки_старые_первыми():
    lines = protocols_db.overdue_review_details([
        {"id": 3, "title": "Прогулка", "review_date": "2026-11-02"},
        {"id": 7, "title": "Режим сна", "review_date": "2026-10-13"},
    ], TODAY)
    assert lines == [
        "#7 «Режим сна» — просрочен на 30 дн. (2026-10-13)",
        "#3 «Прогулка» — просрочен на 10 дн. (2026-11-02)",
    ]


def test_сегодняшний_будущий_и_без_даты_молчат():
    assert protocols_db.overdue_review_details([
        {"id": 1, "title": "A", "review_date": "2026-11-12"},
        {"id": 2, "title": "B", "review_date": "2026-12-01"},
        {"id": 4, "title": "C", "review_date": None},
        {"id": 5, "title": "D", "review_date": "мусор"},
    ], TODAY) == []
