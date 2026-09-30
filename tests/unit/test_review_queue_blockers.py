"""Очередь ревью называет ПРИЧИНУ, а не подставляет человека (2026-08-08).

Датчик должен отличать ожидание решения человека от технического блока:
потерянного результата, неизвестного имени или отсутствующего canonical_name.
Направление технической ошибки в очередь человека не устраняет её причину.
"""
from __future__ import annotations

import sqlite3

import pytest

import integrity_tests as I

pytestmark = pytest.mark.unit


def _db(tmp_path, rows):
    """rows: (value, value_text, canonical_name, raw_name)"""
    p = tmp_path / "t.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE lab_results_staging "
              "(value REAL, value_text TEXT, canonical_name TEXT, raw_name TEXT, review_status TEXT)")
    c.executemany("INSERT INTO lab_results_staging VALUES (?,?,?,?,'review')", rows)
    c.commit(); c.close()
    return p


def test_no_result_is_named_as_such(tmp_path):
    """Строка без числа И без текста — подтверждать нечего. Это и есть большинство строк
    того разбора: имя со страницы есть, результата нет вовсе."""
    out = I._review_queue_blockers(_db(tmp_path, [(None, None, None, "ГЛЮКОЗА_СЫРОЕ")]))
    assert out["нет результата, подтверждать нечего"] == 1
    assert out["ждёт твоего решения"] == 0


def test_unknown_name_is_named_as_such(tmp_path):
    """Значение есть, имени в справочнике нет — человек в дашборде бессилен."""
    out = I._review_queue_blockers(_db(tmp_path, [(22.7, None, "ВыдуманныйАналит", "X")]))
    assert out["имя не сведено к канону"] == 1
    assert out["ждёт твоего решения"] == 0


def test_real_decision_is_counted_separately(tmp_path):
    """Позитив: значение есть, имя каноническое — вот это действительно к человеку.
    Без этой ветки датчик стал бы утверждать, что решений не бывает никогда."""
    out = I._review_queue_blockers(_db(tmp_path, [(5.4, None, "HbA1c", "HbA1c")]))
    assert out["ждёт твоего решения"] == 1


def test_stored_name_is_normalized_too(tmp_path):
    """НЕГАТИВНЫЙ КОНТРОЛЬ на расхождение ключей (поймано прогоном 08.08).

    Первая редакция брала `canonical_name` как есть, и строка `Plateletcrit` осталась
    в «имя не сведено» уже ПОСЛЕ того, как алиас `plateletcrit → PCT` был заведён:
    датчик судил сырое поле, промоут — нормализованное. Мутация «взять cname без
    normalize» роняет тест."""
    out = I._review_queue_blockers(_db(tmp_path, [(0.31, None, "Plateletcrit", "PCT")]))
    assert out["имя не сведено к канону"] == 0, "нормализованное имя объявлено несведённым"
    assert out["ждёт твоего решения"] == 1


def test_text_result_counts_as_a_result(tmp_path):
    """НЕГАТИВНЫЙ КОНТРОЛЬ: качественный результат — это результат, а не пустота.
    Мутация «считать пустым всё, где value IS NULL» роняет тест. Без него датчик
    объявил бы «подтверждать нечего» про строку с честным «НЕГАТИВНО» на бланке."""
    out = I._review_queue_blockers(_db(tmp_path, [(None, "НЕГАТИВНО", "HbA1c", "HbA1c")]))
    assert out["нет результата, подтверждать нечего"] == 0
    assert out["ждёт твоего решения"] == 1
