"""
B2 (2026-07-15): _scrub_fabricated_sleep — при пустом Oura LLM не должен называть
числа сна (тот же класс, что выдуманный ген). Честную фразу без чисел не трогаем.
"""
from __future__ import annotations

import pytest

import gp_agent

pytestmark = pytest.mark.unit


def test_no_scrub_when_sleep_present():
    """Данные ЕСТЬ → числа легитимны, скраб no-op."""
    r = "Глубокий 18%, REM короткий. Днём прогулка."
    assert gp_agent._scrub_fabricated_sleep(r, True) == (r, [])


def test_fabricated_hours_stripped_when_empty():
    r = "Ты спал 7,2 часа, глубокого 18%. Днём прогулка."
    out, removed = gp_agent._scrub_fabricated_sleep(r, False)
    assert removed and "прогулка" in out and "7" not in out


def test_honest_absence_kept():
    r = "Кольцо не синхронизировало ночь — чисел сна нет. Опираюсь на readiness."
    out, removed = gp_agent._scrub_fabricated_sleep(r, False)
    assert removed == [] and out == r


def test_sleep_score_stripped():
    r = "Сон score 82/100 — неплохо."
    out, removed = gp_agent._scrub_fabricated_sleep(r, False)
    assert removed and "82" not in out


def test_bedtime_stripped():
    """Время отбоя тоже число сна — при пустом Oura его быть не должно."""
    r = "Ты заснул в 23:40 и проснулся отдохнувшим."
    out, removed = gp_agent._scrub_fabricated_sleep(r, False)
    assert removed


def test_non_sleep_numbers_kept():
    """Числа НЕ про сон (readiness/шаги) не трогаем."""
    r = "Readiness 65, шагов 8000 вчера. Хороший день."
    out, removed = gp_agent._scrub_fabricated_sleep(r, False)
    assert removed == [] and out == r


def test_absence_count_sentence_kept():
    """«кольцо молчит 2 ночи» — не измерение сна, честная фраза об отсутствии."""
    r = "Кольцо молчит уже вторую ночь подряд."
    out, removed = gp_agent._scrub_fabricated_sleep(r, False)
    assert removed == [] and out == r


def test_empty_report():
    assert gp_agent._scrub_fabricated_sleep("", False) == ("", [])


def test_spelled_out_percent_stripped():
    """«глубокого 12 процентов» словом (без %) — тоже выдуманное число сна."""
    r = "Глубокого всего 12 процентов за ночь."
    out, removed = gp_agent._scrub_fabricated_sleep(r, False)
    assert removed and "12" not in out


def test_spelled_out_minutes_stripped():
    r = "REM всего 40 минут."
    out, removed = gp_agent._scrub_fabricated_sleep(r, False)
    assert removed and "40" not in out


# ── N ночей без Oura → «надень кольцо» (B, порог владельца = 2) ───────────────────

def test_absent_streak_empty():
    assert gp_agent._absent_streak([]) == 0


def test_absent_streak_today_has_data():
    assert gp_agent._absent_streak([True, False, False]) == 0


def test_absent_streak_one_night():
    assert gp_agent._absent_streak([False, True]) == 1


def test_absent_streak_two_nights_triggers_at_N2():
    assert gp_agent._absent_streak([False, False, True]) == 2


def test_absent_streak_all_absent_bounded():
    assert gp_agent._absent_streak([False, False, False]) == 3
