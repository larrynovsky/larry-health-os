#!/usr/bin/env python3.11
"""
google_calendar_fetcher.py — получает события из Google Calendar API
и кэширует в ~/health/data/calendar_cache.json.

Запускается launchd каждый час. calendar_client.py читает кэш — без
зависимости от icalbuddy, EventKit или iCloud-синхронизации на Studio.

Первичная настройка (ОДНОКРАТНО, на MacBook с браузером):
    python3.11 google_calendar_fetcher.py --setup

После авторизации скопировать секреты на Studio:
    rsync -a ~/.health_secrets/google_calendar_token.json \\
          <studio_ssh>:~/.health_secrets/

Для профиля партнёра:
    HEALTH_SECRETS_DIR=~/.health_secrets_partner \\
    HEALTH_DATA_DIR=~/health_partner \\
    python3.11 google_calendar_fetcher.py
"""
from _time_inject import get_now  # seam
import argparse
import json
import logging
import os

from secrets_paths import owner_secrets_dir
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

SCOPES          = ["https://www.googleapis.com/auth/calendar.readonly"]
FETCH_DAYS_BACK = 2   # ловим идущие многодневные события
FETCH_DAYS_AHEAD = 30  # горизонт будущего

# Кэш считается устаревшим через N часов — предупреждение, данные всё равно отдаём
CACHE_WARN_AGE_H = 3
# Через N часов кэш пустой — ошибка, calendar_client вернёт []
CACHE_ERROR_AGE_H = 25


# ── Пути ──────────────────────────────────────────────────────────────────────

def _secrets_dir() -> Path:
    # Единый резолвер (secrets_paths) — локальная копия логики была дублем
    # значения (обзор 2026-07-02); датчик test_secrets_single_resolver.
    from secrets_paths import secrets_dir
    return secrets_dir()


def _data_dir() -> Path:
    env = os.environ.get("HEALTH_DATA_DIR")
    base = Path(env).expanduser() if env else Path.home() / "health"
    return base / "data"


def cache_path() -> Path:
    return _data_dir() / "calendar_cache.json"


def _client_file() -> Path:
    """OAuth-клиент ПРИЛОЖЕНИЯ — общий, а не тенантский (2026-09-02).

    Замер: client_id у владельца и у второго тенанта ОДИН И ТОТ ЖЕ (проект
    <gcp-project-id>) — это одно приложение, а не два. Тенантское здесь —
    google_calendar_token.json и google_calendar_account: КТО авторизовался. Клиент
    же адресован ОПЕРАТОРУ системы, как и другие служебные вещи (см. docstring
    secrets_paths.owner_secrets_dir), и нужен только в setup_auth — разовой браузерной
    авторизации, которую запускают руками на MacBook; ежедневная синхронизация живёт
    на refresh-токене и клиента не открывает вовсе.

    Прежнее чтение через _secrets_dir() требовало КОПИЮ общего секрета в каталоге
    каждого тенанта — и копия у партнёра появилась, а у владельца на Studio файла нет
    вообще. Реестр классов при этом объявлял бы реализацию вместо замысла.
    """
    return owner_secrets_dir() / "google_calendar_client.json"


def _token_file() -> Path:
    return _secrets_dir() / "google_calendar_token.json"


# ── OAuth ─────────────────────────────────────────────────────────────────────

def _get_credentials():
    """Возвращает действующие OAuth credentials или None."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        log.error("pip install google-api-python-client google-auth-oauthlib --break-system-packages")
        return None

    token_file = _token_file()
    creds = None

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_file.write_text(creds.to_json())
            log.info("Google Calendar token refreshed → %s", token_file)
            return creds
        except Exception as exc:
            log.error("Token refresh failed (%s). Запустите --setup заново.", exc)
            return None

    log.error(
        "Нет действующего Google Calendar токена. "
        "Запустите: python3.11 google_calendar_fetcher.py --setup"
    )
    return None


def setup_auth():
    """OAuth авторизация в браузере (запускать на MacBook, не на Studio)."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("pip install google-auth-oauthlib --break-system-packages")
        sys.exit(1)

    client_file = _client_file()
    if not client_file.exists():
        print(f"\n❌ Файл клиента не найден: {client_file}")
        print("\nЧто сделать:")
        print("  1. Открыть https://console.cloud.google.com/")
        print("  2. APIs & Services → Credentials → Create Credentials → OAuth client ID")
        print("  3. Application type: Desktop app")
        print("  4. Скачать JSON → сохранить как:")
        print(f"     {client_file}")
        print("  5. Повторить: python3.11 google_calendar_fetcher.py --setup")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)

    token_file = _token_file()
    token_file.write_text(creds.to_json())
    token_file.chmod(0o600)
    print(f"\n✅ Токен сохранён: {token_file}")
    print("\nСкопировать на Studio:")
    import infra_config
    print(f"  rsync -a {token_file} {infra_config.STUDIO_SSH}:{token_file}")


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_and_cache() -> bool:
    """Забирает события из Google Calendar и обновляет cache_path()."""
    try:
        from googleapiclient.discovery import build
    except ImportError:
        log.error("pip install google-api-python-client --break-system-packages")
        return False

    creds = _get_credentials()
    if creds is None:
        return False

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    # Список всех календарей пользователя
    try:
        cal_list = service.calendarList().list().execute()
    except Exception as exc:
        log.error("Не удалось получить список календарей: %s", exc)
        return False

    calendars = [
        {"id": c["id"], "summary": c.get("summary", c["id"])}
        for c in cal_list.get("items", [])
        if c.get("accessRole") in ("owner", "writer", "reader")
    ]

    # Личность аккаунта = id primary-календаря (это email авторизованного аккаунта).
    # Пишем в кэш → calendar_client сверяет с ожидаемым аккаунтом тенанта
    # (мультитенант-сторож против «токен авторизует чужой аккаунт»).
    account = next(
        (c["id"] for c in cal_list.get("items", []) if c.get("primary")), None
    )
    if account is None:
        log.warning("Не найден primary-календарь — account в кэше будет null "
                    "(календарь-сторож заблокирует, если тенант сконфигурирован).")

    now_utc  = get_now(tz=timezone.utc)
    time_min = (now_utc - timedelta(days=FETCH_DAYS_BACK)).isoformat()
    time_max = (now_utc + timedelta(days=FETCH_DAYS_AHEAD)).isoformat()

    all_events = []
    for cal in calendars:
        try:
            page_token = None
            while True:
                result = service.events().list(
                    calendarId=cal["id"],
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                    pageToken=page_token,
                    maxResults=250,
                ).execute()

                for ev in result.get("items", []):
                    if ev.get("status") == "cancelled":
                        continue

                    start     = ev.get("start", {})
                    end       = ev.get("end", {})
                    start_str = start.get("dateTime") or start.get("date") or ""
                    end_str   = end.get("dateTime")   or end.get("date")   or ""
                    is_all_day = "dateTime" not in start

                    all_events.append({
                        "id":         ev["id"],
                        "summary":    ev.get("summary", "(без названия)"),
                        "start":      start_str,
                        "end":        end_str,
                        "is_all_day": is_all_day,
                        "location":   ev.get("location", ""),
                        "calendar":   cal["summary"],
                        "event_type": ev.get("eventType", "default"),
                    })

                page_token = result.get("nextPageToken")
                if not page_token:
                    break

        except Exception as exc:
            log.warning("Ошибка получения календаря %s: %s", cal["id"], exc)

    all_events.sort(key=lambda e: e["start"])

    cache = {
        "fetched_at": now_utc.isoformat(),
        "account":    account,
        "calendars":  calendars,
        "events":     all_events,
    }

    cp = cache_path()
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    log.info("Calendar cache: %d событий → %s", len(all_events), cp)
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Google Calendar → JSON cache")
    parser.add_argument("--setup", action="store_true",
                        help="Однократная OAuth авторизация (нужен браузер)")
    args = parser.parse_args()

    if args.setup:
        setup_auth()
    else:
        ok = fetch_and_cache()
        sys.exit(0 if ok else 1)
