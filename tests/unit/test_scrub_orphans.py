"""Скраб генов не оставляет сирот (нить scrub-orphans, 28.09).

После удаления предложения его зависимое продолжение тоже должно исчезнуть.
Ниже независимо придуманные тексты о вымышленных маркерах: вводная, удаляемая
опора и отсылающая к ней фраза. Самостоятельные соседние предложения сохраняются.
"""
from __future__ import annotations

import pytest

import gp_agent

pytestmark = pytest.mark.unit


def test_invented_orphan_goes_with_cut_sentence():
    rep = ("Прогулка завершена. Маркер SYNGA1 отмечен в учебном наборе. "
           "Контекст примера делает это предметом проверки.")
    out, removed = gp_agent._strip_leaked_genes(rep, {"SYNGA1"})
    assert "SYNGA1" not in out
    assert "делает это" not in out            # сирота ушла
    assert "Прогулка завершена." in out       # предыдущее цело
    assert ["(опора вырезана)"] in removed    # сирота названа в журнале, не молча


def test_invented_orphan_paragraph_head():
    rep = ("Запись сохранена.\n\nSYNGB2 выделяет учебную группу. Практически это "
           "означает выбор другого примера. Дневник открыт.")
    out, _ = gp_agent._strip_leaked_genes(rep, {"SYNGB2"})
    assert "Практически это" not in out
    assert "Дневник открыт." in out           # независимое после сироты — цело
    assert "Запись сохранена." in out


def test_scrub_path_uses_same_rule(db):
    """Основной скраб (не страховка): rs-номер вне одобренных + сирота с «поэтому»."""
    rep = "Вариант rs10830963 связан с глюкозой. Поэтому сахар стоит держать в поле зрения. Сон 7 часов."
    out, _ = gp_agent._scrub_unapproved_genes(rep, set())
    assert "rs10830963" not in out and "Поэтому" not in out
    assert "Сон 7 часов." in out


def test_independent_neighbour_and_middle_cut_survive():
    rep = "Сон 7 часов. CLOCK удлиняет цикл. Днём — прогулка."
    out, _ = gp_agent._strip_leaked_genes(rep, {"CLOCK"})
    assert out == "Сон 7 часов. Днём — прогулка."


def test_chain_stops_at_first_standalone_sentence():
    rep = ("SYNGB2 учебный. Это значит выбрать пример. Заметка сохранена. Это полезная отметка.")
    out, _ = gp_agent._strip_leaked_genes(rep, {"SYNGB2"})
    assert out == "Заметка сохранена. Это полезная отметка."   # своё «это» после целой фразы не трогаем
