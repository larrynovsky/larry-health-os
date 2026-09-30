"""
generate_constitutions._metrics_or_sparse_note — genome-only тенант.

У вымышленного нового профиля геномные данные есть, а продольного ряда нет.
Наличие SNP позволяет собрать конституцию с явной пометкой об отсутствии
истории; выдумывать тренды запрещено.
"""
from __future__ import annotations

import pytest

import generate_constitutions as gc

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("empty", ["", "нет данных", None])
def test_sparse_note_when_no_metrics(empty):
    note = gc._metrics_or_sparse_note(empty)
    assert "НЕ описывай" in note
    assert "тренд" in note.lower()
    assert "геном" in note.lower()


def test_metrics_passthrough_when_present():
    real = "7d avg (n=7): 6.9h  deep 66m"
    assert gc._metrics_or_sparse_note(real) == real
