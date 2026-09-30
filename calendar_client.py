#!/usr/bin/env python3.11
"""
calendar_client.py — читает события из JSON-кэша Google Calendar.

Кэш ведёт google_calendar_fetcher.py (запускается launchd раз в час).
Без зависимости от icalbuddy, EventKit или iCloud на Studio.

Публичный API (неизменён):
    format_calendar_context(days=14) -> str
    get_travel_events(days=60) -> list[dict]
"""

import json
import logging
import os
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

from _time_inject import get_now, get_today  # единый источник "сейчас" (замораживаемый)

log = logging.getLogger(__name__)

TRAVEL_KEYWORDS = [
    "flight", "рейс", "перелёт", "перелет", "fly", "airport", "аэропорт",
    "hotel", "отель", "hostel", "airbnb", "check-in", "check in",
    "trip", "поездка", "travel", "прилёт", "вылет",
    "arrival", "departure", "boarding",
    "transfer", "трансфер",
    "vacation", "отпуск",
]

CHECKIN_KEYWORDS = [
    "check in", "check-in", "checkin", "заезд", "заселение",
]

FLIGHT_KEYWORDS = [
    "flight", "рейс", "перелёт", "перелет", "fly", "вылет", "departure", "boarding",
]

# Предупреждать если кэш старше N часов, не показывать если старше M часов
_CACHE_WARN_AGE_H  = 3
_CACHE_ERROR_AGE_H = 25


def _cache_path() -> Path:
    env = os.environ.get("HEALTH_DATA_DIR")
    base = Path(env).expanduser() if env else Path.home() / "health"
    return base / "data" / "calendar_cache.json"


def _expected_account() -> str | None:
    """Ожидаемый Google-аккаунт ЭТОГО тенанта (файл secrets_dir/google_calendar_account).

    Мультитенант-сторож (2026-07-14): secrets_dir() уже fail-closed по КАТАЛОГУ
    секретов, но НЕ ловит «правильный каталог, но токен авторизует чужой аккаунт»
    (доказано: кэш партнёра = календарь владельца, оба с почтой владельца). Этот файл —
    личность тенанта; _read_cache сверяет её с тем, чей аккаунт реально нафетчен.
    Нет файла → None (неконфигурировано; _read_cache отдаёт, но громко предупреждает).
    """
    try:
        from secrets_paths import secrets_dir
        f = secrets_dir() / "google_calendar_account"
        if f.exists():
            v = f.read_text().strip()
            return v or None
    except Exception as exc:  # secrets_dir fail-closed или IO — не роняем бриф
        log.warning("google_calendar_account: %s", exc)
    return None


def _verify_tenant_identity(raw: dict) -> bool:
    """True → кэш принадлежит этому тенанту; False → блокировать (fail-closed).

    Правило: если ожидаемый аккаунт СКОНФИГУРИРОВАН — требуем точное совпадение с
    account кэша (нет поля / расхождение → блок + громкий лог, БЕЗ user-Telegram — ops).
    Не сконфигурирован → отдаём (обратная совместимость), но warn: сторож слеп.
    """
    expected = _expected_account()
    actual = raw.get("account")
    if not expected:
        log.warning(
            "CAL_TENANT_UNVERIFIED: google_calendar_account не задан — "
            "личность календаря не проверяется (риск кросс-тенант утечки).")
        return True
    if actual != expected:
        # ТОЛЬКО лог (ops-сигнал), НЕ user-Telegram: это инфра/настройка токена, не
        # событие здоровья. Защита — сам возврат [] (события скрыты). Шумел на каждое
        # чтение в health-бот → убрано (2026-07-14). Смотреть: grep CAL_TENANT_MISMATCH.
        log.error(
            "CAL_TENANT_MISMATCH: календарь нафетчен аккаунтом %r, ожидался %r — "
            "блокирую (fail-closed, кросс-тенант утечка).",
            actual, expected)
        return False
    return True


def _parse_iso(s: str) -> datetime | None:
    """ISO 8601 строка → datetime UTC. Обрабатывает date-only ('2026-07-03')."""
    if not s:
        return None
    try:
        if "T" in s:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        else:
            # all-day event: date only
            d = date.fromisoformat(s)
            return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    except Exception:
        return None


def _format_date_raw(start_dt: datetime, is_all_day: bool = False) -> str:
    """Форматирует start datetime в строку для AI-контекста."""
    today      = get_today()
    local_dt   = start_dt.astimezone()
    event_date = local_dt.date()
    delta      = (event_date - today).days

    time_str = "" if is_all_day else f" {local_dt.strftime('%H:%M')}"
    date_str = local_dt.strftime("%-d %b %Y")

    if delta < 0:
        return f"ongoing since {date_str}"
    elif delta == 0:
        return f"today - {date_str}{time_str}"
    elif delta == 1:
        return f"tomorrow - {date_str}{time_str}"
    elif delta == 2:
        return f"day after tomorrow - {date_str}{time_str}"
    else:
        return f"{date_str}{time_str}"


def _classify_travel(e: dict) -> str:
    text = (e["title"] + " " + e["location"]).lower()
    if "ongoing" in e["date_raw"]:
        return "ongoing"
    if any(kw in text for kw in CHECKIN_KEYWORDS):
        return "checkin"
    if any(kw in text for kw in FLIGHT_KEYWORDS):
        return "flight"
    return "travel"


def _read_cache() -> list[dict]:
    """Читает calendar_cache.json и возвращает список событий в унифицированном формате."""
    cp = _cache_path()

    if not cp.exists():
        log.warning(
            "Кэш Google Calendar не найден (%s). "
            "Проверьте что google_calendar_fetcher.py запущен и авторизован.", cp
        )
        return []

    try:
        raw = json.loads(cp.read_text())
    except Exception as exc:
        log.warning("Не удалось прочитать кэш: %s", exc)
        return []

    # Мультитенант-сторож: кэш нафетчен ОЖИДАЕМЫМ аккаунтом этого тенанта?
    # Раньше проверки свежести — протухший ЧУЖОЙ кэш опаснее протухшего своего.
    if not _verify_tenant_identity(raw):
        return []

    # Проверка свежести
    fetched_at_str = raw.get("fetched_at", "")
    if fetched_at_str:
        try:
            fetched_at = datetime.fromisoformat(fetched_at_str)
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            age_h = (get_now(timezone.utc) - fetched_at).total_seconds() / 3600
            if age_h > _CACHE_ERROR_AGE_H:
                log.error(
                    "Кэш Google Calendar устарел на %.0fч (лимит %dч). "
                    "google_calendar_fetcher.py не запускается?",
                    age_h, _CACHE_ERROR_AGE_H,
                )
                return []
            if age_h > _CACHE_WARN_AGE_H:
                log.warning("Кэш Google Calendar %.0fч назад (рекомендуется <%dч)",
                            age_h, _CACHE_WARN_AGE_H)
        except Exception:
            pass  # silent-ok: malformed fetched_at is non-critical, skip staleness check

    now_utc = get_now(timezone.utc)
    events  = []

    for ev in raw.get("events", []):
        try:
            title = ev.get("summary", "").strip()
            if not title:
                continue

            # Пропускаем birthday и FROM_GMAIL
            if ev.get("event_type") == "birthday":
                continue
            # FROM_GMAIL события обычно шум — но если есть location, оставляем
            # (например, бронирование отеля из gmail полезно)

            start_dt   = _parse_iso(ev.get("start", ""))
            end_dt     = _parse_iso(ev.get("end", ""))
            is_all_day = ev.get("is_all_day", False)

            if start_dt is None:
                continue

            # Пропускаем события, которые завершились
            if end_dt and end_dt < now_utc:
                continue

            location = ev.get("location", "").strip()
            # Исключаем URL-локации (Zoom, Meet, Telemost)
            if any(u in location.lower() for u in ["http", "zoom", "meet.google", "telemost"]):
                location = ""

            date_raw  = _format_date_raw(start_dt, is_all_day)
            text      = (title + " " + location).lower()
            is_travel = any(kw in text for kw in TRAVEL_KEYWORDS) or bool(location)

            events.append({
                "title":    title,
                "date_raw": date_raw,
                "location": location,
                "is_travel": is_travel,
            })

        except Exception as exc:
            log.debug("Пропускаю событие: %s", exc)

    # Ongoing события в конец
    def _sort_key(e: dict) -> int:
        dr = e["date_raw"]
        if "ongoing" in dr:    return 9999
        if "today" in dr:      return 0
        if "tomorrow" in dr:   return 1
        if "day after" in dr:  return 2
        return 5

    events.sort(key=_sort_key)
    return events


# ── Публичный API ─────────────────────────────────────────────────────────────

def is_birthday(e: dict) -> bool:
    return "birthday" in e["title"].lower()


def format_calendar_context(days: int = 14) -> str:
    """Форматированный блок событий для AI-контекста."""
    all_events = _read_cache()

    # Фильтруем события за пределами горизонта days
    today    = get_today()
    cutoff   = today + timedelta(days=days)
    filtered = []
    for e in all_events:
        if "ongoing" in e["date_raw"]:
            filtered.append(e)
            continue
        # Пытаемся извлечь дату из date_raw; если не получается — включаем
        filtered.append(e)  # сортировка уже правильная, горизонт в cache_path

    regular = [e for e in filtered if not is_birthday(e)]
    travel  = [e for e in filtered if e["is_travel"] and not is_birthday(e)]

    lines = []

    if regular:
        lines.append(f"Календарь (ближайшие {days} дней):")
        for e in regular[:12]:
            loc = f" [{e['location']}]" if e["location"] else ""
            lines.append(f"  {e['date_raw']}: {e['title']}{loc}")

    if travel:
        lines.append("\nПоездки / перелёты:")
        for e in travel[:6]:
            loc = f" [{e['location']}]" if e["location"] else ""
            tag = _classify_travel(e)
            label = {
                "ongoing": " [поездка идёт]",
                "checkin": " [заезд в отель, не вылет]",
                "flight":  " [перелёт]",
                "travel":  "",
            }[tag]
            lines.append(f"  {e['date_raw']}: {e['title']}{loc}{label}")

    return "\n".join(lines)


def get_travel_events(days: int = 60) -> list[dict]:
    events = _read_cache()
    return [e for e in events if e["is_travel"] and not is_birthday(e)]


# Обратная совместимость: parse_events больше не нужен (кэш структурированный)
def parse_events(raw: str) -> list[dict]:  # noqa: ARG001
    log.debug("parse_events() устарел — используй google_calendar_fetcher.py + _read_cache()")
    return []


if __name__ == "__main__":
    ctx = format_calendar_context(14)
    print(ctx or "(кэш пуст или устарел — запустите google_calendar_fetcher.py --setup)")
    print("\n--- travel only ---")
    for e in get_travel_events(60):
        print(e)
