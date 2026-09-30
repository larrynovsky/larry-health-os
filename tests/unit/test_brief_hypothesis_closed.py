"""«Гипотеза закрыта» в брифе — только по настоящему вердикту.

Окно «последние N» теряет записи как после закрытия, так и после вытеснения.
Вымышленный профиль A имеет вердикт, профиль B — открытую вытесненную запись.
Объявлять закрытие можно только по вердикту и только о показанном.
"""
from __future__ import annotations

import pytest

import gp_agent
import hai_hypotheses as hh

pytestmark = pytest.mark.unit


def _dec(mid=1006, status="shown", was="Кофе после 14:00 сокращает глубокий сон",
         shown_on="2026-09-20"):
    return {"semantic_key": f"hypothesis:{mid}", "status": status, "state": "resolved",
            "provider": "hypotheses", "origin": "personal_external", "evidence": None,
            "was": was, "shown_on": shown_on}


def test_confirmed_verdict_announced_with_reminder():
    out = "\n".join(gp_agent._resolved_context_lines([_dec()], verdict_of=lambda m: "confirmed"))
    assert "ГИПОТЕЗА ЗАКРЫТА" in out
    assert "«Кофе после 14:00 сокращает глубокий сон» — подтвердилась данными" in out
    assert "показывали 20.09" in out


def test_rejected_verdict_announced():
    out = "\n".join(gp_agent._resolved_context_lines([_dec()], verdict_of=lambda m: "rejected"))
    assert "не подтвердилась данными" in out


def test_window_pushout_is_silent():
    """Открытую гипотезу вытеснило окно → вердикта нет → молчим."""
    assert gp_agent._resolved_context_lines([_dec()], verdict_of=lambda m: None) == []


def test_not_shown_in_episode_is_silent():
    assert gp_agent._resolved_context_lines([_dec(status="none")],
                                            verdict_of=lambda m: "confirmed") == []


def test_without_verdict_source_fail_closed():
    assert gp_agent._resolved_context_lines([_dec()], verdict_of=None) == []


def test_verdict_asked_for_right_memory_id():
    asked = []
    gp_agent._resolved_context_lines([_dec(mid=1010)],
                                     verdict_of=lambda m: asked.append(m) or None)
    assert asked == [1010]


def test_hypothesis_verdict_reads_status(db):
    ids = {st: hh.save_hypothesis("obs " + st, "mech", "pred", "test", status=st)
           for st in ("open", "confirmed", "rejected", "testing")}
    assert hh.hypothesis_verdict(ids["confirmed"]) == "confirmed"
    assert hh.hypothesis_verdict(ids["rejected"]) == "rejected"
    assert hh.hypothesis_verdict(ids["open"]) is None
    assert hh.hypothesis_verdict(ids["testing"]) is None
    assert hh.hypothesis_verdict(999999) is None
