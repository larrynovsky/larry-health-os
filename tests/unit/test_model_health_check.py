#!/usr/bin/env python3.11
"""Unit-тест датчика: детекция отказа без реального API."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import model_health_check as mh


class _Msgs:
    def __init__(self, bad):
        self.bad = bad
    def create(self, model, **kw):
        if model in self.bad:
            raise RuntimeError("simulated retirement")
        return type("M", (), {"model": model})()


class _Client:
    def __init__(self, bad):
        self.messages = _Msgs(bad)


def test_all_ok():
    r = mh.run_check(notify=False, client=_Client(bad=set()))
    assert all(v["ok"] for v in r.values()), r
    assert set(r) == set(mh.hai_core.MODEL_DEFAULTS), r


def test_one_retired_detected():
    bad = {next(iter(mh.hai_core.MODEL_DEFAULTS.values()))}
    r = mh.run_check(notify=False, client=_Client(bad=bad))
    fails = [v for v in r.values() if not v["ok"]]
    assert len(fails) == 1, r
    assert fails[0]["detail"], r


def test_temporary_failure_only_journals(monkeypatch, fault_journal):
    import notify
    sent = []
    monkeypatch.setattr(mh, "check_models", lambda **k: {
        "example": {"model": "fictional-model", "ok": False, "detail": "Timeout: fictional"}})
    monkeypatch.setattr(notify, "notify_operator", lambda text: sent.append(text))
    mh.run_check(notify=True)
    assert sent == [] and "model request failed" in fault_journal.read_text()


def test_unavailable_model_asks_for_choice(monkeypatch, fault_journal):
    import notify
    import i18n
    sent = []
    monkeypatch.setattr(i18n, "lang_of", lambda *a, **k: "ru")
    monkeypatch.setattr(mh, "check_models", lambda **k: {
        "example": {"model": "fictional-model", "ok": False, "detail": "retired/not_found: example"}})
    monkeypatch.setattr(notify, "notify_operator", lambda text: sent.append(text))
    mh.run_check(notify=True)
    assert len(sent) == 1 and "Варианты:" in sent[0] and "Если промолчишь" in sent[0]
    assert "MODEL_DEFAULTS" not in sent[0] and "fictional-model" not in sent[0]


if __name__ == "__main__":
    test_all_ok()
    test_one_retired_detected()
    print("TEST PASS")
