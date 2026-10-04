"""Первый контакт постороннего и правдивый текст о сбое (нить first-contact, 02.10).

Два вопроса, две группы проверок:
1. Проходит ли человек /start и знакомство — тем же путём, что в CI (scripts/first_contact_smoke.walk),
   только на стенде тестов, а не в образе. Монтирование только для чтения воспроизводит тест
   tests/unit/test_handlers_meta.py::test_cmd_start_works_with_read_only_secrets; в образе — CI.
2. Что бот говорит при сбое: «передал на починку» — только когда чинящий жив; иначе оператору —
   код сбоя и куда с ним идти, тенанту — что узнает владелец. Код в сообщении = код в журнале.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def base(db):
    """Свежая база, засеянная как на установке (init_db) — то, что видит бот после урока."""
    import health_db
    health_db.init_db()
    return db


def test_walk_passes_start_and_whole_onboarding(base):
    from scripts.first_contact_smoke import walk
    from bot.filters import owner_chat_id

    problems, sent = asyncio.run(walk(owner_chat_id()))
    # Записи ERROR здесь не судятся: на станке нет timezonefinder, и конец знакомства пишет
    # BRIEF_TZ_FALLBACK от среды, а не от кода. Политику ERROR судит CI — в образе, где он есть.
    assert [p for p in problems if not p.startswith("ERROR в журнале")] == []
    assert len(sent) >= 14, "знакомство — 14 вопросов, бот обязан был их задать"


def test_walk_is_red_when_start_breaks(base, monkeypatch):
    """Негативный контроль смоука: /start падает — смоук обязан это сказать, а не позеленеть."""
    import handlers.meta as meta
    from scripts.first_contact_smoke import walk
    from bot.filters import owner_chat_id

    def broken_init():
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(meta.db, "init_db", broken_init)
    problems, _ = asyncio.run(walk(owner_chat_id()))
    assert any("сбое" in p for p in problems)
    assert any("не началось" in p for p in problems)


@pytest.fixture
def journal(tmp_path, monkeypatch):
    import notify
    path = tmp_path / "faults.jsonl"
    monkeypatch.setenv("HEALTH_FAULTS_JOURNAL", str(path))
    (tmp_path / notify.REPAIR_SEEN).unlink(missing_ok=True)   # conftest кладёт отметку — здесь её нет
    return path


def _seen(journal, age_s):
    """Отметка ремонта нужного возраста; ночной разбор — свежий (его судит test_repair_promise)."""
    import notify
    (journal.parent / notify.REPAIR_SEEN).write_text(str(int(time.time() - age_s)))
    _cycle_ran(journal.parent)


def _cycle_ran(logs):
    import datetime as _d
    import notify
    (logs / notify.NIGHT_CYCLE_RECEIPT).write_text(json.dumps({"ran_at": _d.datetime.now().isoformat()}))


def test_repairer_alive_promises_repair(journal):
    import notify
    _seen(journal, 3600)
    text = notify.fault("x.py:f: boom")
    assert "передал это на починку" in text


def test_no_repairer_operator_gets_code_and_where_to_go(journal, monkeypatch):
    import notify
    import secrets_paths
    monkeypatch.setattr(secrets_paths, "is_owner", lambda: True)
    text = notify.fault("x.py:f: boom")
    code = json.loads(journal.read_text().splitlines()[-1])["code"]
    assert code in text, "код в сообщении обязан совпадать с кодом в журнале"
    assert "bot_fault" in text and "ничего делать не нужно" not in text


def test_stale_repairer_is_no_repairer(journal, monkeypatch):
    import notify
    import secrets_paths
    monkeypatch.setattr(secrets_paths, "is_owner", lambda: True)
    _seen(journal, notify.REPAIR_FRESH_S + 3600)
    assert "bot_fault" in notify.fault("x.py:f: boom")


def test_no_repairer_tenant_is_told_owner_will_know(journal, monkeypatch):
    import notify
    import secrets_paths
    monkeypatch.setattr(secrets_paths, "is_owner", lambda: False)
    text = notify.fault("x.py:f: boom")
    assert "Владелец системы узнает" in text and "bot_fault" not in text


def test_walk_is_red_on_dirty_stand(base):
    """Заполненный профиль: /start только здоровается — смоук обязан отказаться, а не позеленеть."""
    import profile_db
    from scripts.first_contact_smoke import walk
    from bot.filters import owner_chat_id
    profile_db.upsert_profile("identity.name", value_text="Уже знаком")
    problems, _ = asyncio.run(walk(owner_chat_id()))
    assert any("стенд не чистый" in p for p in problems)


def test_custom_person_key_is_not_rewritten(journal):
    import notify
    assert notify.fault("x.py:f: boom", person_key=None) is None


def test_night_repair_mark_is_read_by_the_bot(tmp_path, monkeypatch):
    """Писатель отметки (ночной ремонт) и читатель (бот) сходятся на одном файле, а не на двух."""
    import night_repair
    import notify
    monkeypatch.setenv("HOME", str(tmp_path))            # ~/health/RUNTIME нет → рантайм native
    monkeypatch.setattr(night_repair, "REPO", tmp_path / "repo")
    monkeypatch.setenv("HEALTH_FAULTS_JOURNAL", str(tmp_path / "repo" / "logs" / "faults.jsonl"))
    assert not notify.repairer_alive()
    assert night_repair._mark_repairer_seen()
    _cycle_ran(tmp_path / "repo" / "logs")
    assert notify.repairer_alive()
