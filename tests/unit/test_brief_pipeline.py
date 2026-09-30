"""
Ф2-расширение: gate_sleep_brief — вырезает недельную рамку глубокого сна, когда
хронический глубокий на кулдауне (sleep:deep:below_band suppressed). Pure unit.
"""
from __future__ import annotations

import pytest

import brief_pipeline as bp

pytestmark = pytest.mark.unit

_SLEEP = (
    "SLEEP 2032-04-12\n"
    "Score: 59/100  |  Duration: 6.1h  |  Awake: 36m\n"
    "Deep: 22m (score 47/100)  |  REM: 53m (score 62/100)\n"
    "7d avg (n=7): 6.4h  deep 31m  score 68\n"
    "30d avg (n=30): 6.7h  deep 38m\n"
    "⚠ низкий sleep score (59/100); мало глубокого сна (22м, норма >60м)"
)


def test_gate_sleep_keeps_when_shown():
    assert bp.gate_sleep_brief(_SLEEP, True) == _SLEEP


def test_gate_sleep_strips_chronic_when_suppressed():
    out = bp.gate_sleep_brief(_SLEEP, False)
    assert "Deep: 22m" in out              # сегодняшняя ночь осталась
    assert "мало глубокого" not in out     # хронический флаг убран
    assert "deep 31m" not in out           # недельная рамка (7д) убрана
    assert "deep 38m" not in out           # недельная рамка (30д) убрана
    assert "низкий sleep score" in out     # прочие флаги не тронуты
