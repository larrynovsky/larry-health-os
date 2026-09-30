"""Сводка: отложенная доставка, одна отправка, сердцебиение и сохранение при отказе.

Все события выдуманы. Транспорты и инженерная очередь подменены; БД не нужна.
"""
import json
from datetime import datetime

import pytest

import i18n
import notify
import owner_weekly as weekly
import night_cycle

_real_standing_count = night_cycle.standing_count

pytestmark = pytest.mark.unit
# Сохраняем тело транспорта до autouse-глушителя. В тесте ниже subprocess полностью
# подменён; ключ и адрес — выдуманные файлы tmp_path, наружу ничего не уходит.
_telegram_transport = notify._telegram


@pytest.fixture
def channel(monkeypatch):
    monkeypatch.setattr(i18n, "lang_of", lambda *a, **k: "ru")
    monkeypatch.setattr(weekly.parked_decisions, "list_open", lambda *a: [])
    import night_cycle
    monkeypatch.setattr(night_cycle, "standing_count", lambda: 0)
    sent = []
    monkeypatch.setattr(notify, "notify_operator", lambda text: sent.append(text) or "telegram")
    return sent


def monday(hour=9, minute=10):
    return datetime(2030, 1, 7, hour, minute, tzinfo=weekly.owner_nag.HOME_TZ)


def test_a_line_waits_for_one_weekly_message(channel, owner_weekly_journal):
    notify.weekly("Учебная проверка снова проходит.")
    notify.weekly("Учебное описание обновлено.")
    assert channel == []
    assert len(owner_weekly_journal.read_text().splitlines()) == 2
    result = weekly.run(monday())
    assert len(channel) == 1
    assert channel[0].startswith("🗓 За неделю, к сведению:")
    assert "Учебная проверка" in channel[0] and "Учебное описание" in channel[0]
    assert result["via"] == "telegram"
    assert json.loads(weekly.receipt_path().read_text())["via"] == "telegram"
    assert owner_weekly_journal.read_text() == ""
    notify.weekly("Следующая учебная новость.")
    weekly.run(monday(10))
    assert len(channel) == 1
    assert "Следующая" in owner_weekly_journal.read_text()


def test_empty_week_sends_heartbeat(channel):
    weekly.run(monday())
    assert channel == ["🗓 За неделю: ничего важного, система работает."]


def test_outside_monday_and_before_0910_are_silent(channel):
    weekly.run(monday(9, 9))
    weekly.run(monday().replace(day=8))
    assert channel == []
    assert weekly.last_scheduled_at(monday(9, 9)).date().isoformat() == "2029-12-31"
    assert weekly.last_scheduled_at(monday()).date().isoformat() == "2030-01-07"


def test_ten_lines_and_queue_is_included(channel, monkeypatch):
    monkeypatch.setattr(weekly.parked_decisions, "list_open", lambda *a: [
        {"kind": "dev_fix", "created": "2030-01-01", "summary": "fictional"}])
    for n in range(12):
        notify.weekly(f"Учебное событие {n}.")
    weekly.run(monday())
    assert len(channel) == 1 and channel[0].count("\n• ") == 10
    assert "исправлений в работе: 1" in channel[0] and "…и ещё 3" in channel[0]


def test_queue_read_failure_is_visible(channel, monkeypatch, fault_journal):
    def broken(*args):
        raise OSError("fictional queue unavailable")
    monkeypatch.setattr(weekly.parked_decisions, "list_open", broken)
    weekly.run(monday())
    assert "Не удалось посчитать" in channel[0]
    assert "queue read failed" in fault_journal.read_text()


def test_summary_fits_backup_channel_without_losing_items(channel):
    for n in range(12):
        notify.weekly(f"Учебное событие {n}: " + "длинное описание " * 20)
    weekly.run(monday())
    assert len(channel[0]) <= 1000
    assert channel[0].count("\n• ") == 10 and channel[0].endswith("…и ещё 2")


@pytest.mark.parametrize("via", ["none", None, "unknown"])
def test_failed_delivery_keeps_lines_for_retry(channel, monkeypatch, owner_weekly_journal, via):
    notify.weekly("Учебная новость.")
    monkeypatch.setattr(notify, "notify_operator", lambda text: via)
    result = weekly.run(monday())
    assert result["via"] == via and "Учебная" in owner_weekly_journal.read_text()
    monkeypatch.setattr(notify, "notify_operator", lambda text: channel.append(text) or "fallback")
    weekly.run(monday(10))
    assert len(channel) == 1 and owner_weekly_journal.read_text() == ""
    assert json.loads(weekly.receipt_path().read_text())["via"] == "fallback"


def test_bad_journal_is_not_discarded(channel, owner_weekly_journal, fault_journal):
    owner_weekly_journal.write_text('{"broken": true}\n')
    weekly.run(monday())
    assert channel == [] and owner_weekly_journal.read_text() == '{"broken": true}\n'
    assert "journal unreadable" in fault_journal.read_text()


def test_receipt_failure_does_not_consume_queue(channel, monkeypatch, owner_weekly_journal):
    notify.weekly("Учебная новость.")
    def broken(*args):
        raise OSError("fictional disk failure")
    monkeypatch.setattr(weekly.os, "replace", broken)
    with pytest.raises(OSError, match="fictional disk failure"):
        weekly.run(monday())
    assert len(channel) == 1 and "Учебная" in owner_weekly_journal.read_text()


@pytest.mark.parametrize("response, expected", [
    ({"ok": True, "result": {"message_id": 17}}, True),
    ({"ok": False, "description": "fictional rejection"}, False),
    ({"ok": True, "result": {}}, False),
    ({"ok": True, "result": {"message_id": True}}, False),
    ({"ok": True, "result": {"message_id": 0}}, False),
])
def test_http_success_requires_telegram_message_receipt(tmp_path, monkeypatch, response, expected):
    from types import SimpleNamespace
    (tmp_path / "telegram_token").write_text("fictional-token-not-valid")
    (tmp_path / "telegram_chat_id").write_text("0")
    monkeypatch.setattr(notify.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps(response)))
    assert _telegram_transport("fictional heartbeat", secrets=tmp_path) is expected


def test_standing_findings_age_in_the_summary(channel, monkeypatch):
    """28.09: standing-находки (действия нет по решению владельца) раньше вёз дайджест триажа;
    после 4b они не ехали никуда. Теперь — одна строка счётом."""
    import night_cycle
    monkeypatch.setattr(night_cycle, "standing_count", _real_standing_count)
    monkeypatch.setattr(night_cycle, "_load_warnings", lambda: [
        ("w1", "учебная метка", "учебная метка", "standing"),
        ("w2", "другая метка", "другая метка", "fix")])
    weekly.run(monday())
    assert "мелких неполадок, которые пока не мешают: 1" in channel[0]
