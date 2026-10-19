"""Ночной разбор отчётов /bug: одна карточка ночному ремонту на отпечаток сбоя. Значения выдуманы."""
import json

import pytest

import bug_inbox
import bug_report
import night_cycle
import parked_decisions
from _time_inject import get_now


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("BUG_INBOX_DB", str(tmp_path / "reports.db"))
    monkeypatch.setenv("HEALTH_PARKED_DB", str(tmp_path / "parked.json"))


def _send(rid, category="report_missing", faults=()):
    rep = bug_report.build(category, report_id=rid, release="v0.2.1", platform="linux/aarch64",
                           connected={}, faults=list(faults), screen=["07:30 jobs.brief.header"])
    assert bug_inbox.accept(json.dumps(rep).encode())[0] == 201


def _cards():
    return {c["id"]: c for c in parked_decisions.list_open(get_now().date())
            if c["id"].startswith(night_cycle.BUG_REPORT_PREFIX)}


def test_one_card_per_fingerprint_with_counts(store, owner_weekly_journal):
    g = {"type": "KeyError", "at": ["labs_db.py:build_lab_history_context"], "n": 2, "fp": "7f3a1c"}
    _send("0123456789abcdef:1", faults=[g])
    _send("fedcba9876543210:1", faults=[g])
    _send("fedcba9876543210:2", category="button_silent")
    out = night_cycle._bug_reports_to_repair()
    cards = _cards()
    assert out == {"bug_reports": 3, "bug_report_cards": 2}
    assert set(cards) == {"report:7f3a1c", "report:cat:button_silent"}
    s = cards["report:7f3a1c"]["summary"]
    assert "2 от 2 установок" in s and "KeyError в labs_db.py:build_lab_history_context" in s
    assert cards["report:7f3a1c"]["kind"] == "dev_fix"
    night_cycle._bug_reports_to_repair()                       # повтор ночи — не плодит карточек
    assert len(_cards()) == 2
    assert "Отчёты о сбоях от чужих установок" in owner_weekly_journal.read_text(encoding="utf-8")


def test_no_receiver_here_is_nothing(store):
    assert night_cycle._bug_reports_to_repair() == {"bug_reports": 0, "bug_report_cards": 0}
    assert _cards() == {}
