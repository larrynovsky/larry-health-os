"""
Разрежённый профиль (новый тенант) не должен провоцировать выдуманные тренды.

Если наблюдений меньше длины окна, среднее не доказывает устойчивый тренд.
Без числа наблюдений модель может принять разрежённые данные за полный ряд.
Фиксы: (1) _observation_window_note предупреждает LLM при n<7;
(2) брифы показывают (n=N) у средних.
"""
from __future__ import annotations

from datetime import date

import pytest

pytestmark = pytest.mark.unit


def test_observation_window_note_warns_when_sparse():
    import gp_agent
    note = gp_agent._observation_window_note(1)
    assert note, "при 1 дне данных должно быть предупреждение"
    assert "1" in note and "НОВЫЙ ПРОФИЛЬ" in note
    assert "тренды" in note.lower() or "паттерн" in note.lower()


def test_observation_window_note_silent_when_enough():
    import gp_agent
    assert gp_agent._observation_window_note(7) == ""
    assert gp_agent._observation_window_note(30) == ""


def _sleep_day():
    return {"sleep": {
        "totalSleep": 5.1, "deep": 0.95, "rem": 1.05, "awake": 0.6,
        "sleep_score": 64,
        "contributors": {"efficiency": 78, "restfulness": 70,
                         "deep_sleep": 68, "rem_sleep": 60},
        "sleepStart": "2021-03-10T00:55:00", "sleepEnd": "2021-03-10T06:25:00"}}


def test_sleep_brief_shows_sample_size_sparse():
    import lifestyle_agents as la
    stats1 = {"n_days": 1, "avg_sleep": 5.1, "avg_deep": 0.95, "avg_sleep_score": 64}
    brief = la.SleepAgent().generate_brief(_sleep_day(), stats1, stats1, date(2021, 3, 10))
    assert "n=1" in brief, "бриф обязан показывать число дней у среднего (n=1)"
    # средняя всё ещё присутствует, но теперь честно помечена
    assert "7d avg" in brief


def test_sleep_brief_shows_sample_size_full():
    import lifestyle_agents as la
    s7 = {"n_days": 7, "avg_sleep": 6.9, "avg_deep": 1.1, "avg_sleep_score": 78}
    s30 = {"n_days": 28, "avg_sleep": 7.0, "avg_deep": 1.1, "avg_sleep_score": 79}
    brief = la.SleepAgent().generate_brief(_sleep_day(), s7, s30, date(2021, 3, 10))
    assert "n=7" in brief and "n=28" in brief
