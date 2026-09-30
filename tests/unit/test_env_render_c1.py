"""
C1 (2026-07-15): _env_context_lines — что бриф говорит про среду.

Три случая: показанные находки / находки подавлены анти-повтором (тишина) /
среда чиста (в поездке «проверено — в норме», дома молчим). Тестируем чистую
функцию отдельно от рендера. Импортирует gp_agent (как test_brief_leak_hook).
"""
from __future__ import annotations

import pytest

import brief_cards as bc
import gp_agent

pytestmark = pytest.mark.unit


def _env_card(key="weather:uv:high", ev="UV до 9 — очень высокий"):
    return bc.Card(provider="env", semantic_key=key, lane="routine",
                   severity=0.5, evidence_summary=ev)


def test_shown_env_renders_findings_block():
    probe = {"known": True, "reachable": True, "away": False, "place": "Рейкьявик"}
    lines = gp_agent._env_context_lines([_env_card()], {"weather:uv:high"}, probe)
    assert lines[0] == "СРЕДА (Рейкьявик):"
    assert any("UV до 9" in l for l in lines)


def test_shown_env_travel_header():
    c = _env_card(key="air:pm:elevated", ev="воздух: PM2.5 30 µg/m³ — выше нормы")
    probe = {"known": True, "reachable": True, "away": True, "place": "Берлин"}
    lines = gp_agent._env_context_lines([c], {"air:pm:elevated"}, probe)
    assert lines[0] == "СРЕДА (Берлин, поездка):"


def test_findings_suppressed_are_silent():
    """Env-карта БЫЛА, но не в shown (подавлена) → молчим, НЕ «чисто»."""
    probe = {"known": True, "reachable": True, "away": True, "place": "Берлин"}
    assert gp_agent._env_context_lines([_env_card()], set(), probe) == []


def test_clean_travel_says_checked():
    """Поездка, env-карт нет вовсе, источник ответил → «проверено — в норме»."""
    probe = {"known": True, "reachable": True, "away": True, "place": "Берлин"}
    lines = gp_agent._env_context_lines([], set(), probe)
    assert lines and lines[0] == "СРЕДА (Берлин, поездка):"
    assert any("в норме" in l for l in lines)


def test_clean_home_is_silent():
    """Дома, чистая среда → молчим (не плодим ежедневный повтор-константу)."""
    probe = {"known": True, "reachable": True, "away": False, "place": "Рейкьявик"}
    assert gp_agent._env_context_lines([], set(), probe) == []


def test_travel_source_down_is_silent():
    """Поездка, но источник не ответил → молчим (не выдумываем «в норме»)."""
    probe = {"known": True, "reachable": False, "away": True, "place": "Берлин"}
    assert gp_agent._env_context_lines([], set(), probe) == []


def test_no_location_silent():
    probe = {"known": False, "reachable": False, "away": False, "place": None}
    assert gp_agent._env_context_lines([], set(), probe) == []


def test_probe_none_silent():
    assert gp_agent._env_context_lines([], set(), None) == []


def test_non_env_cards_ignored():
    """Не-env карты в env-блок не попадают (раздел смотрит только provider==env)."""
    food = bc.Card(provider="food", semantic_key="food:seasonal:x", lane="routine",
                   severity=0.4, evidence_summary="яблоки")
    probe = {"known": True, "reachable": True, "away": True, "place": "Берлин"}
    lines = gp_agent._env_context_lines([food], {"food:seasonal:x"}, probe)
    assert lines and "в норме" in lines[1]
