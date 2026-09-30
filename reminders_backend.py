#!/usr/bin/env python3
"""Задачи человека в CalDAV-списке напоминаний: создать · прочитать выполненные · закрыть.

Зачем (нить docker-install, этап 3; решение владельца 29.09 — «свой CalDAV за Tailscale»):
в контейнере нет Reminders.app и osascript, а обновлённые напоминания iCloud по CalDAV
недоступны. Проба 29.09 замкнула цикл на живом iPhone: задача создана на сервере, закрыта на
телефоне, закрытие прочитано с сервера (15:53).

Включается наличием `caldav.json` в каталоге секретов тенанта ({"url", "username",
"password"}); нет файла — `caldav_configured()` ложно, и вызывающие остаются на AppleScript (натив).
Связь задачи с напоминанием — детерминированный UID (`health-task-<список>-<id>`) и прежняя
метка `[task_id:N]` в описании; своей таблицы нет: второй дом связи разъехался бы с первым.

Голый HTTP через requests, а не библиотека caldav: трёх операций хватает, а библиотека
принесла бы в замок восемь пакетов (замок обновляется по одному, docs/how-to/dependency_updates.md).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timezone
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

from _time_inject import get_now   # единый шов часов (docs/explanation/time-inject-contract.md)

log = logging.getLogger(__name__)

_DAV = "{DAV:}"
TASK_ID = re.compile(r"\[task_id:(\d+)\]")
ALARM_HOUR_KEY = "reminders.alarm_hour"   # system_config; сид — health_db.init_db (code_seed)


def alarm_at() -> time:
    """Местный час будильника в день дедлайна — предпочтение человека, живёт в данных (§9)."""
    import config_db
    return time(int(config_db.get_config(ALARM_HOUR_KEY, 9)), 0)


def _conf() -> dict | None:
    import secrets_paths
    secrets = Path(secrets_paths.secrets_dir())   # форма «имя / "файл"» — её видит сторож читателей секретов
    p = secrets / "caldav.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def caldav_configured() -> bool:
    try:
        return _conf() is not None
    except (OSError, ValueError, RuntimeError) as e:   # битый файл или отказ резолвера секретов — громко
        log.warning("reminders_backend: caldav.json не читается (%s) — CalDAV выключен", e)
        return False


def _session(conf: dict):
    import requests
    s = requests.Session()
    s.auth = (conf["username"], conf["password"])
    return s


def _slug(list_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", list_name.lower()).strip("-") or "health"


def _collection(conf: dict, list_name: str) -> str:
    base = conf["url"].rstrip("/") + "/" + quote(conf["username"]) + "/"
    return base + _slug(list_name) + "/"


def _uid(list_name: str, task_id: int) -> str:
    return f"health-task-{_slug(list_name)}-{task_id}"


def _esc(s: str) -> str:
    """Текст iCalendar (RFC 5545 §3.3.11): \\ ; , и перевод строки экранируются."""
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ensure_list(s, url: str, list_name: str) -> None:
    r = s.request("PROPFIND", url, headers={"Depth": "0"}, timeout=20)
    if r.status_code == 207:
        return
    body = ("<?xml version='1.0' encoding='utf-8'?>"
            "<C:mkcalendar xmlns:D='DAV:' xmlns:C='urn:ietf:params:xml:ns:caldav'><D:set><D:prop>"
            f"<D:displayname>{list_name}</D:displayname>"
            "<C:supported-calendar-component-set><C:comp name='VTODO'/></C:supported-calendar-component-set>"
            "</D:prop></D:set></C:mkcalendar>")
    r = s.request("MKCALENDAR", url, data=body.encode("utf-8"),
                  headers={"Content-Type": "application/xml"}, timeout=20)
    r.raise_for_status()


def vtodo(uid: str, title: str, notes: str, due: date, tz, now: datetime, at_time: time) -> str:
    """Текст задачи. Будильник — на at_time местного времени в день дедлайна (UTC в файле)."""
    at = datetime.combine(due, at_time, tzinfo=tz).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//health-os//reminders_backend//RU",
        "BEGIN:VTODO", f"UID:{uid}", f"DTSTAMP:{stamp}", f"SUMMARY:{_esc(title)}",
        f"DESCRIPTION:{_esc(notes)}", f"DUE:{at}",
        "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(title)}",
        f"TRIGGER;VALUE=DATE-TIME:{at}", "END:VALARM",
        "END:VTODO", "END:VCALENDAR", ""])


def put_task(task_id: int, title: str, notes: str, due: date, list_name: str, tz) -> bool:
    """Положить задачу. Повтор с тем же task_id — не дубль (If-None-Match: *)."""
    conf = _conf()
    s = _session(conf)
    url = _collection(conf, list_name)
    _ensure_list(s, url, list_name)
    uid = _uid(list_name, task_id)
    body = vtodo(uid, title, notes, due, tz, get_now(timezone.utc), alarm_at())
    r = s.put(url + uid + ".ics", data=body.encode("utf-8"),
              headers={"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"}, timeout=20)
    if r.status_code == 412:
        log.info("reminders_backend: задача %s уже в списке", task_id)
        return True
    r.raise_for_status()
    return True


def _unfold(ics: str) -> str:
    return re.sub(r"\r?\n[ \t]", "", ics)


def completed_task_ids_in(items: list[str]) -> list[int]:
    """Чистое ядро: тексты .ics → task_id выполненных. Выполнена — STATUS:COMPLETED
    (так пишет iOS при галочке); номер — из метки [task_id:N] либо из нашего UID."""
    out = set()
    for ics in items:
        text = _unfold(ics)
        if not re.search(r"^STATUS:COMPLETED\s*$", text, re.M):
            continue
        m = TASK_ID.search(text.replace("\\n", "\n")) or re.search(r"^UID:health-task-.*-(\d+)\s*$", text, re.M)
        if m:
            out.add(int(m.group(1)))
    return sorted(out)


def _items(s, url: str) -> list[tuple[str, str, str]]:
    """[(href, etag, текст)] всех задач списка: PROPFIND Depth 1 + GET."""
    r = s.request("PROPFIND", url, headers={"Depth": "1"}, timeout=20)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    out = []
    root = ElementTree.fromstring(r.content)
    for resp in root.iter(f"{_DAV}response"):
        href = resp.findtext(f"{_DAV}href") or ""
        if not href.endswith(".ics"):
            continue
        g = s.get(_abs_url(url, href), timeout=20)
        g.raise_for_status()
        out.append((href, g.headers.get("ETag", ""), g.text))
    return out


def _abs_url(collection: str, href: str) -> str:
    from urllib.parse import urljoin
    return urljoin(collection, href)


def completed_task_ids(list_name: str) -> list[int]:
    conf = _conf()
    s = _session(conf)
    return completed_task_ids_in([t for _, _, t in _items(s, _collection(conf, list_name))])


def complete_task(task_id: int, list_name: str) -> bool:
    """Закрыть задачу на сервере (закрыли в боте) — чтобы в Напоминаниях не висела."""
    conf = _conf()
    s = _session(conf)
    url = _collection(conf, list_name)
    done = 0
    for href, etag, ics in _items(s, url):
        text = _unfold(ics)
        if f"[task_id:{task_id}]" not in text.replace("\\n", "\n") \
                and not re.search(rf"^UID:health-task-.*-{task_id}\s*$", text, re.M):
            continue
        new = mark_completed(ics, get_now(timezone.utc))
        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        if etag:
            headers["If-Match"] = etag
        s.put(_abs_url(url, href), data=new.encode("utf-8"), headers=headers, timeout=20).raise_for_status()
        done += 1
    return done > 0


def mark_completed(ics: str, now: datetime) -> str:
    """STATUS:COMPLETED и COMPLETED:<UTC> внутри VTODO; прежний STATUS заменяется."""
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [l for l in re.split(r"\r?\n", _unfold(ics)) if not l.startswith(("STATUS:", "COMPLETED:"))]
    i = lines.index("END:VTODO")
    lines[i:i] = ["STATUS:COMPLETED", f"COMPLETED:{stamp}"]
    return "\r\n".join(lines)


if __name__ == "__main__":
    sample = vtodo("health-task-health-7", "📋 Сдать анализ; кровь", "почему\n[task_id:7]",
                   date(2026, 10, 1), timezone.utc, datetime(2026, 9, 29, tzinfo=timezone.utc), time(9))
    assert completed_task_ids_in([sample]) == []
    assert completed_task_ids_in([mark_completed(sample, datetime(2026, 9, 30, tzinfo=timezone.utc))]) == [7]
    print("ok")
