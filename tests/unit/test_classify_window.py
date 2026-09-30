"""Окно классификатора: структурные сигналы читаются по ВСЕМУ тексту.

Вымышленный бланк с длинной шапкой ставит структурные маркеры за пределами
обоих старых окон (800 и 1500 символов). Классификаторы должны распознать
лабораторное тело независимо от длины шапки; одиночное слово в прозе — не бланк.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# Шапка длиннее обоих окон (800 у classify, 1500 у _classify_with_confidence).
LONG_HEADER = ("Example Lab Ltd. " * 40 + "Passport No: 000. " * 40 +
               "National ID: 000. " * 40 + "Doctor: REFERRAL. " * 40)
CBC_BODY = "Test Name Result Units\nWBC 7.83\nRBC 5.13\nHGB 15.6\nHCT 46.1\nPLT 312\n"


def test_header_fixture_actually_exceeds_both_windows():
    """Само-проверка фикстуры: если шапка вдруг короче окна, тест ниже станет
    зелёным по случайности и перестанет что-либо доказывать."""
    assert len(LONG_HEADER) > 1500


def test_cbc_beyond_window_is_recognised_as_lab():
    import import_all
    assert import_all.classify(Path("blank.pdf"), LONG_HEADER + CBC_BODY) == "lab"


def test_coordinator_agrees_with_classifier_on_the_same_blank():
    """Два классификатора не должны расходиться на одном документе: именно
    расхождение делало кнопку «🧪 Анализы» бесполезной — подтверждение человека
    переигрывалось повторной классификацией внутри coordinate_import."""
    import import_coordinator as ic
    doc_type, conf, _fmt = ic._classify_with_confidence(
        Path("blank.pdf"), LONG_HEADER + CBC_BODY)
    assert doc_type == "lab"
    assert conf >= 0.6


def test_prose_mentioning_one_marker_is_not_a_lab():
    """Одно упоминание маркера в прозе — не бланк. Порог «три из пяти» держит
    границу; без него выписка с фразой про гемоглобин уехала бы в лабораторный
    путь и потратила бы vision-прогон."""
    import import_all
    prose = LONG_HEADER + "Пациент выписан. Уровень HGB в норме, динамика good.\n"
    assert import_all.classify(Path("discharge_note.pdf"), prose) != "lab"


def test_substring_traps_do_not_fire_in_coordinator():
    """Граница, ради которой триада считается ОТДЕЛЬНО от bio_markers: в том
    списке есть «ast» и «alt», а они подстроки слов gastroscopy и alternative.
    Расширь им окно до полного текста — и протокол гастроскопии станет «лабом».
    """
    import import_coordinator as ic
    endo = (LONG_HEADER + "Gastroscopy protocol. Alternative therapy discussed. "
            "Last examination unremarkable. " * 5)
    doc_type, _c, _f = ic._classify_with_confidence(Path("endo.pdf"), endo)
    assert doc_type != "lab"
