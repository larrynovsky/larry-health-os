"""CalDAV-адаптер напоминаний (docker-install, этап 3): выполненной считается задача со
STATUS:COMPLETED, номер — из метки [task_id:N] или нашего UID; будильник — в местный час
дня дедлайна. Краснеет, если адаптер начнёт закрывать невыполненное, потеряет номер задачи
на сложенной (RFC 5545) строке или поставит будильник не в тот час."""
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

import reminders_backend as rb

pytestmark = pytest.mark.unit
_NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def _todo(tid=7):
    return rb.vtodo(f"health-task-health-{tid}", "📋 Сдать анализ; кровь, натощак",
                    f"почему\n[task_id:{tid}]", date(2026, 10, 1), ZoneInfo("Europe/Berlin"), _NOW, time(9))


def test_невыполненная_не_закрывает_выполненная_закрывает():
    assert rb.completed_task_ids_in([_todo()]) == []
    assert rb.completed_task_ids_in([rb.mark_completed(_todo(), _NOW)]) == [7]


def test_номер_виден_и_на_сложенной_строке_и_без_метки():
    folded = rb.mark_completed(_todo(12), _NOW).replace("DESCRIPTION:почему", "DESCRIPTION:поч\r\n ему")
    assert rb.completed_task_ids_in([folded]) == [12]
    no_tag = "\r\n".join(l for l in rb.mark_completed(_todo(13), _NOW).split("\r\n")
                         if not l.startswith("DESCRIPTION"))
    assert rb.completed_task_ids_in([no_tag]) == [13]


def test_будильник_в_местный_час_дня_дедлайна():
    t = _todo()
    assert "DUE:20261001T070000Z" in t              # 09:00 Berlin (CEST, UTC+2) = 07:00 UTC
    assert "TRIGGER;VALUE=DATE-TIME:20261001T070000Z" in t
    assert "SUMMARY:📋 Сдать анализ\\; кровь\\, натощак" in t


def test_без_caldav_json_адаптер_выключен(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path))
    assert rb.caldav_configured() is False
    (tmp_path / "caldav.json").write_text('{"url": "https://x", "username": "u", "password": "p"}')
    assert rb.caldav_configured() is True


def test_задача_с_пустой_причиной_получает_напоминание(monkeypatch):
    """Переключение 30.09: перенос открытых задач в CalDAV упал на первой задаче с reason = NULL —
    `task.get("reason", "")` вернул None, и `+=` уронил весь перенос."""
    import task_agent
    got = {}
    monkeypatch.setattr(rb, "caldav_configured", lambda: True)
    monkeypatch.setattr(rb, "put_task", lambda tid, title, notes, *a, **k: got.update(notes=notes))
    monkeypatch.setattr(task_agent, "_reminders_list", lambda: "Health")
    import location_signal
    monkeypatch.setattr(location_signal, "tenant_timezone", lambda: "UTC")
    assert task_agent.create_macos_reminder({"id": 7, "content": "x", "reason": None, "type": "action"})
    assert got["notes"] == "\n[task_id:7]"


def test_настоящий_каталог_секретов_прогона_не_включает_caldav(tmp_path, monkeypatch):
    """Замер 30.09: полный прогон в контейнере владельца (секреты прогона — настоящие, с caldav.json)
    завёл в его ящике CalDAV тестовый список. Каталог, с которым стартовал прогон, для тестов
    CalDAV не включает; тест со своим каталогом видит свой файл (соседний тест выше)."""
    (tmp_path / "caldav.json").write_text('{"url": "https://x", "username": "u", "password": "p"}')
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tmp_path))
    monkeypatch.setenv("HEALTH_TEST_BOOT_SECRETS", str(tmp_path))   # этот каталог = каталог прогона
    assert rb.caldav_configured() is False
