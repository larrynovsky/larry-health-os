#!/usr/bin/env python3.11
"""
calendar_sync.py — синкает Trip-события из KAYAK-календаря в таблицу periods.

Почему модель периодов такая и какие альтернативы отброшены:
docs/explanation/periods_model.md.
Запускается раз в час через launchd на MacBook.

Логика:
- Читает только события из 'Trips on KAYAK' календаря
- Фильтрует события с "Trip" в названии (сводные события поездки, не отдельные рейсы)
- Upsert по (name, start_date): обновляет end_date если изменилась
- Не трогает записи с source='manual'
- Логирует добавленные/обновлённые записи
"""

import re
import subprocess
import logging
import os
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
from datetime import date, timedelta
from _time_inject import get_today  # seam
from pathlib import Path

import health_db as _db  # respects HEALTH_DATA_DIR env var
ICALBUDDY = "/opt/homebrew/bin/icalbuddy"
LOG_PATH = Path("/tmp/calendar_sync.log")

logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# Только Trip-события из KAYAK
KAYAK_CAL = "Trips on KAYAK"


def _strip_ansi(s: str) -> str:
    return re.sub(r'\x1b\[[0-9;]*[mK]', '', s)


def _parse_relative_date(s: str) -> date | None:
    """Конвертирует relative label icalbuddy в дату."""
    s = s.strip().lower()
    today = get_today()
    if s == "today":
        return today
    if s == "tomorrow":
        return today + timedelta(days=1)
    if s == "day after tomorrow":
        return today + timedelta(days=2)
    if s == "yesterday":
        return today - timedelta(days=1)
    return None


def _parse_date(s: str) -> date | None:
    """Парсит дату вида '26 Apr 2026' или relative label."""
    s = s.strip()
    rel = _parse_relative_date(s)
    if rel:
        return rel
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return date(*[int(x) for x in
                          __import__('datetime').datetime.strptime(s, fmt).timetuple()[:3]])
        except ValueError:
            pass
    return None


def _parse_date_range(date_raw: str) -> tuple[date | None, date | None]:
    """
    Парсит строку вида:
      '15 Mar 2030'              → (2030-03-15, None)
      '10 Jan 2030 - tomorrow'   → (2030-01-10, <завтра>)
      '1 Feb 2030 - 4 Feb 2030'  → (2030-02-01, 2030-02-04)
    """
    # Убираем время если есть ("today at 8:00 AM - 9:00 AM")
    if " at " in date_raw.lower():
        return None, None

    if " - " in date_raw:
        left, right = date_raw.split(" - ", 1)
        return _parse_date(left), _parse_date(right)
    else:
        return _parse_date(date_raw), None


def _run_icalbuddy() -> str:
    try:
        result = subprocess.run(
            [ICALBUDDY, "-f", "-ec", "Birthdays", "eventsToday+180"],
            capture_output=True, text=True, timeout=30,
            env={"LANG": "en_US.UTF-8", "HOME": str(Path.home()),
                 "PATH": "/opt/homebrew/bin:/usr/bin:/bin"},
        )
        return _strip_ansi(result.stdout or "")
    except Exception as e:
        log.error(f"icalbuddy failed: {e}")
        return ""


def parse_kayak_trips(raw: str) -> list[dict]:
    """
    Извлекает Trip-события из KAYAK-календаря.
    Возвращает список {'name', 'start_date', 'end_date'}.
    """
    trips = []
    current = None

    for line in raw.splitlines():
        stripped = _strip_ansi(line)

        # Новое событие
        if stripped.startswith("• "):
            if current and current.get("is_kayak_trip"):
                trips.append(current)
            current = {"is_kayak_trip": False, "name": "", "date_raw": ""}

            # Извлекаем название и источник
            # Формат: "• Title (Calendar Name)"
            m = re.match(r'^•\s+(.+?)\s+\(([^)]+)\)\s*$', stripped)
            if m:
                current["name"] = m.group(1).strip()
                cal_name = m.group(2)
                current["is_kayak_trip"] = (
                    KAYAK_CAL in cal_name and "Trip" in current["name"]
                )
            continue

        if current is None:
            continue

        stripped_inner = stripped.strip()

        # Строка с датой (не начинается с известных меток)
        if (stripped_inner and
                not stripped_inner.startswith(("notes:", "location:", "attendees:", "url:")) and
                re.match(r'^(today|tomorrow|day after tomorrow|\d{1,2} \w{3} \d{4})', stripped_inner)):
            current["date_raw"] = stripped_inner.split(" at ")[0].strip()

    # Последнее событие
    if current and current.get("is_kayak_trip"):
        trips.append(current)

    result = []
    for t in trips:
        start, end = _parse_date_range(t["date_raw"])
        if start is None:
            log.warning(f"Cannot parse date for {t['name']!r}: {t['date_raw']!r}")
            continue
        result.append({
            "name": t["name"],
            "start_date": str(start),
            "end_date": str(end) if end else None,
        })
    return result


def sync_to_db(trips: list[dict]) -> None:
    con = _db.get_conn()
    con.row_factory = sqlite3.Row
    added, updated, skipped = 0, 0, 0

    for t in trips:
        name, start, end = t["name"], t["start_date"], t["end_date"]

        existing = con.execute(
            "SELECT id, end_date, source FROM periods WHERE name=? AND start_date=?",
            (name, start)
        ).fetchone()

        if existing is None:
            con.execute(
                "INSERT INTO periods (name, type, start_date, end_date, source, tags) "
                "VALUES (?, 'travel', ?, ?, 'calendar', '[\"travel\"]')",
                (name, start, end)
            )
            log.info(f"ADDED: {name} {start}–{end}")
            added += 1
        else:
            if existing["source"] == "manual":
                skipped += 1
                continue
            if existing["end_date"] != end:
                con.execute(
                    "UPDATE periods SET end_date=?, source='calendar' WHERE id=?",
                    (end, existing["id"])
                )
                log.info(f"UPDATED end_date: {name} {existing['end_date']} → {end}")
                updated += 1
            else:
                skipped += 1

    con.commit()
    con.close()
    log.info(f"Sync done: +{added} added, ~{updated} updated, {skipped} unchanged")
    print(f"calendar_sync: +{added} added, ~{updated} updated, {skipped} unchanged")


def main():
    raw = _run_icalbuddy()
    if not raw:
        log.warning("Empty icalbuddy output, skipping")
        return
    trips = parse_kayak_trips(raw)
    log.info(f"Found {len(trips)} KAYAK trips: {[t['name'] for t in trips]}")
    sync_to_db(trips)


if __name__ == "__main__":
    main()
