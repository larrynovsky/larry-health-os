"""Датчик затора промоута: чистая логика integrity_tests.promotion_backlog.

Проверяется, что возраст ожидающих строк считается верно,
а неизвестный возраст не выглядит
как нулевой (иначе датчик молчал бы именно тогда, когда данные битые).

RST: позитив раздельно по каждой ветке + границы, которые счастливый прогон
на живом каноне не покажет.
"""
from __future__ import annotations

from datetime import date

import pytest

import integrity_tests as it

pytestmark = pytest.mark.unit

TODAY = date(2026, 7, 29)


def test_empty_staging_is_not_a_backlog():
    assert it.promotion_backlog([], [], TODAY) == (0, None, None)


def test_row_already_in_canon_does_not_wait():
    canon = [("2026-07-17", "HGB")]
    staging = [("2026-07-17", "HGB", "2026-06-30 06:12:38")]
    assert it.promotion_backlog(canon, staging, TODAY) == (0, None, None)


def test_row_absent_from_canon_waits_and_ages():
    staging = [("2026-07-17", "HGB", "2026-06-30 06:12:38")]
    n, oldest, age = it.promotion_backlog([], staging, TODAY)
    assert (n, oldest) == (1, "2026-06-30")
    assert age == 29


def test_oldest_wins_not_newest():
    """Возраст затора — по самой СТАРОЙ строке. Взять свежую значило бы
    отчитаться о заторе как о вчерашнем."""
    staging = [("2026-07-17", "HGB", "2026-07-28 10:00:00"),
               ("2026-07-17", "WBC", "2026-06-30 06:12:38")]
    n, oldest, age = it.promotion_backlog([], staging, TODAY)
    assert (n, oldest, age) == (2, "2026-06-30", 29)


def test_name_matching_goes_through_canon_normalisation():
    """Канон и staging пишут имя по-разному; сравнение обязано идти через
    lab_canon.normalize, иначе датчик объявит затором уже промоученное."""
    canon = [("2026-07-17", "  hgb ")]
    staging = [("2026-07-17", "HGB", "2026-06-30 06:12:38")]
    assert it.promotion_backlog(canon, staging, TODAY)[0] == 0


def test_broken_timestamp_reports_unknown_age_not_zero():
    """Ключевая ветка: битый created_at даёт возраст None, а НЕ 0.
    Ноль означал бы «затора нет» — датчик замолчал бы ровно на тех данных,
    из-за которых его и заводили."""
    staging = [("2026-07-17", "HGB", "не дата")]
    n, oldest, age = it.promotion_backlog([], staging, TODAY)
    assert n == 1
    assert age is None


def test_threshold_constant_is_wired_and_sane():
    """Порог существует между ежедневной обработкой и месяцем молчания."""
    assert 1 <= it.PROMOTION_BACKLOG_DAYS <= 14
