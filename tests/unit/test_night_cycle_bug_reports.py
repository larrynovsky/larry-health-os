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


def _send(rid, category="report_missing", faults=(), release="v0.2.1"):
    rep = bug_report.build(category, report_id=rid, release=release, platform="linux/aarch64",
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
    assert out == {"bug_reports": 3, "bug_report_cards": 2, "bug_report_closed": 0}
    assert set(cards) == {"report:7f3a1c", "report:cat:button_silent"}
    s = cards["report:7f3a1c"]["summary"]
    assert "2 от 2 установок" in s and "KeyError в labs_db.py:build_lab_history_context" in s
    assert cards["report:7f3a1c"]["kind"] == "dev_fix"
    night_cycle._bug_reports_to_repair()                       # повтор ночи — не плодит карточек
    assert len(_cards()) == 2
    assert "Отчёты о сбоях от чужих установок" in owner_weekly_journal.read_text(encoding="utf-8")


def test_no_receiver_here_is_nothing(store):
    assert night_cycle._bug_reports_to_repair() == {"bug_reports": 0, "bug_report_cards": 0,
                                                     "bug_report_closed": 0}
    assert _cards() == {}


def test_fix_closes_card_and_only_a_post_fix_fault_reopens_it(store, owner_weekly_journal, monkeypatch):
    """08.11: карточка не закрывалась никогда — цикл перечитывает все отчёты, park()
    переоткрывал решённую. Реестр bug_fixes.yaml закрывает её (автор fixed); старый отчёт той
    же версии — не рецидив (сбой мог быть до обновления), сбой с меткой версии исправления —
    рецидив. Мутации «не закрывать» и «рецидив по версии отчёта >=» — красные."""
    g = {"type": "KeyError", "at": ["labs_db.py:build_lab_history_context"], "n": 2, "fp": "7f3a1c"}
    _send("0123456789abcdef:1", faults=[g], release="v0.2.6")
    night_cycle._bug_reports_to_repair()
    assert "report:7f3a1c" in _cards()
    monkeypatch.setattr(night_cycle, "_bug_fixes",
                        lambda: {"7f3a1c": {"commit": "abc1234", "fixed_in": "v0.2.6"}})
    out = night_cycle._bug_reports_to_repair()
    assert out["bug_report_closed"] == 1 and "report:7f3a1c" not in _cards()
    rec = parked_decisions.get("report:7f3a1c")
    assert rec["decided_by"] == "fixed" and "abc1234" in rec["decision"]
    night_cycle._bug_reports_to_repair()                       # следующая ночь — закрытая не встаёт
    assert "report:7f3a1c" not in _cards()
    _send("0123456789abcdef:2", faults=[dict(g, rel="v0.2.6")], release="v0.2.6")
    night_cycle._bug_reports_to_repair()
    card = _cards()["report:7f3a1c"]
    assert card["summary"].startswith("Рецидив после исправления abc1234") and "1 от 1 установок" in card["summary"]


def test_fault_journal_stamps_release_and_report_keeps_newest(tmp_path):
    lines = [{"ts": "2026-11-07T10:00:00+00:00", "where": "x.py:f", "exc_type": "KeyError",
              "frames": ["x.py:f"], "release": r} for r in ("v0.2.5", "v0.2.10", "v0.2.9", "local")]
    j = tmp_path / "f.jsonl"
    j.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    from datetime import datetime, timezone
    [g] = bug_report.faults_from_journal(j, now=datetime(2026, 11, 8, tzinfo=timezone.utc))
    assert g["rel"] == "v0.2.10" and g["n"] == 4
    assert bug_report._fault_ok(g) and not bug_report._fault_ok(dict(g, rel="0.2.10"))


def test_detail_from_writer_through_receiver_to_card(store, owner_weekly_journal, tmp_path, monkeypatch):
    import notify
    journal = tmp_path / "faults.jsonl"
    monkeypatch.setenv("HEALTH_FAULTS_JOURNAL", str(journal))
    notify.fault("monthly_consilium: synthetic private text 123", person_key=None,
                 detail=["neurology:TimeoutError", "glucose 123"])
    [g] = bug_report.faults_from_journal(journal)
    # An older report comes first: aggregation must still surface newer diagnostics.
    _send("0123456789abcdef:1", faults=[{k: v for k, v in g.items() if k != "detail"}])
    _send("0123456789abcdef:2", faults=[g])
    night_cycle._bug_reports_to_repair()
    summary = _cards()["report:" + g["fp"]]["summary"]
    assert "monthly_consilium:neurology:TimeoutError" in summary
    assert "123" not in summary and "private" not in summary
