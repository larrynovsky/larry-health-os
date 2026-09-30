"""check_wellally_updates: Telegram только при изменении ПРОМПТОВ; состояние — рантайм (2026-08-31).

Замер: коммит «docs: optimize README» (upstream 16.07) пришёл в чат 31.08 как 🆕 с нулём
файлов, потому что tracked-файл состояния откатился на Studio к апрельскому SHA. Два дефекта,
два check'а: коммит без файлов — не уведомление; состояние читается из logs/.
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.unit


def _run(monkeypatch, tmp_path, result):
    import check_wellally_updates as cw
    monkeypatch.setattr(cw, "STATE_FILE", tmp_path / "logs" / "state.json")
    monkeypatch.setattr(cw, "check_repo", lambda o, r, st: dict(result, repo=f"{o}/{r}"))
    sent = []
    import notify
    monkeypatch.setattr(notify, "weekly", lambda text: sent.append(text))
    cw.main(notify=True)
    return sent, cw


def test_commit_without_prompt_changes_does_not_notify(monkeypatch, tmp_path):
    sent, cw = _run(monkeypatch, tmp_path, {"new_commits": True, "latest_sha": "f604350abc", "latest_date": "2026-07-16",
                                             "latest_message": "docs: README", "new_files": [], "changed_files": [], "errors": []})
    assert sent == []
    assert json.loads(cw.STATE_FILE.read_text())["huifer/WellAlly-health"]["last_commit_sha"] == "f604350abc", "состояние всё равно двигается"


def test_changed_prompt_notifies(monkeypatch, tmp_path):
    sent, _ = _run(monkeypatch, tmp_path, {"new_commits": True, "latest_sha": "abc", "latest_date": "2026-08-01",
                                          "latest_message": "feat", "new_files": [], "changed_files": ["oncologist.md"], "errors": []})
    assert len(sent) == 1 and "вышли обновления" in sent[0] and ".md" not in sent[0]

