"""Решение владельца 04.10: «надо сделать так, чтобы все ответили. Кому нужен неполный
консилиум». До этого не ответивший специалист выпадал, итог писался по оставшимся
(замер 04.10: 8 мнений из 17 — и «ok»)."""
import ast
import asyncio
from pathlib import Path

import pytest

import consilium_roster

ROOT = Path(__file__).resolve().parents[2]


def _asker(replies):
    seq = list(replies)

    async def ask():
        r = seq.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    return ask


def test_silent_participant_is_asked_again_and_counted():
    askers = {"cardio": _asker([{"ok": True, "opinion": "x"}]),
              "sleep": _asker([TimeoutError(), "ответ со второй попытки"])}
    got = asyncio.run(consilium_roster.ask_all(askers, lambda r: r.get("ok") if isinstance(r, dict) else bool(r), "t"))
    assert got == {"cardio": {"ok": True, "opinion": "x"}, "sleep": "ответ со второй попытки"}


def test_silent_after_retry_means_no_consilium_not_a_partial_one():
    askers = {"cardio": _asker([{"ok": True}]),
              "neuro": _asker([{"ok": False}, {"ok": False}])}
    with pytest.raises(consilium_roster.ConsiliumIncomplete) as e:
        asyncio.run(consilium_roster.ask_all(askers, lambda r: r.get("ok"), "t"))
    assert e.value.missing == ["neuro"] and e.value.total == 2
    assert "1" in str(e.value) and "2" in str(e.value)


def test_every_consilium_round_goes_through_ask_all():
    """Сборщик раунда, собирающий участников мимо ask_all, снова умеет неполный консилиум."""
    rounds = {"monthly_consilium.py": ["_run_round_a", "_run_round_b"],
              "wellally_consult.py": ["_run_deliberation_round"],
              "hypothesis_consilium_eval.py": ["_run_eval_round"]}
    bad = []
    for f, names in rounds.items():
        tree = ast.parse((ROOT / f).read_text(encoding="utf-8"))
        fns = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for name in names:
            calls = {getattr(c.func, "attr", getattr(c.func, "id", None))
                     for c in ast.walk(fns[name]) if isinstance(c, ast.Call)}
            if "ask_all" not in calls or "gather" in calls:
                bad.append(f"{f}:{name}")
    assert not bad, f"раунд консилиума мимо consilium_roster.ask_all: {bad}"


def test_monthly_incomplete_round_is_a_fault_and_no_hypotheses(monkeypatch):
    import monthly_consilium as mc
    import notify

    async def silent(*a, **k):
        raise consilium_roster.ConsiliumIncomplete("monthly_consilium round A", ["neuro"], 17)
    monkeypatch.setattr(mc, "_build_consilium_input", lambda end, days: "pkg")
    monkeypatch.setattr(mc, "_run_round_a", silent)
    monkeypatch.setattr(mc, "_run_coordinator", lambda *a: pytest.fail("координатор без полного состава"))
    faults = []
    monkeypatch.setattr(notify, "fault", lambda tech, person_key=None, **kw: faults.append((tech, person_key)))
    assert mc.run(30) == []
    assert len(faults) == 1 and faults[0][1] is None and "neuro" in faults[0][0]
