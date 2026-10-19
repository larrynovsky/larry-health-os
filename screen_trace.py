#!/usr/bin/env python3.11
"""screen_trace.py — «след экрана»: какие шаблоны сообщений РЕАЛЬНО ушли человеку.

Нить bug-report (решение владельца 19.10): вместо скриншота в отчёт о сбое идут имена шаблонов
последних сообщений бота, без подставленных значений. Скриншот нёс бы всё, что на экране.

Почему на отправке, а не в i18n.t: t() зовётся и для текстов, которые не уходят (подписи,
заготовки). Слой отправки сверяет отправленный текст с последними выданными строками
(i18n.keys_in) и пишет только совпавшие ключи. Сообщение без единого шаблона — ответ модели или
сборка из данных — пишется меткой `free_text`: видно, что оно было, не видно, что в нём.

Носители отправки: бот (сетевой слой PTB, bot/main.py) и notify._telegram. Прямые отправки
мимо них (6 модулей на 19.10: hai_hypotheses, task_agent, generate_constitutions,
oura_freshness_check, food_quarterly, dashboard api_status_actions) в след не попадают — граница.

Файл общий для процессов установки (утренний отчёт шлёт cron, ответы — бот), строки помечены
каталогом ключей тенанта; recent() отдаёт только свои. Никогда не бросает: след не имеет права
уронить отправку.
"""
from __future__ import annotations

import fcntl
import json
import os
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

KEEP_LINES = 200
FREE = "free_text"


def _path() -> Path:
    return Path(os.environ.get("HEALTH_SCREEN_TRACE") or
                Path(__file__).resolve().parent / "logs" / "screen_trace.jsonl")


def _tenant() -> str:
    from secrets_paths import secrets_dir
    return Path(secrets_dir()).name


def note(sent_text: str) -> None:
    """Отправка состоялась — записать, какие шаблоны в ней были. Не бросает."""
    try:
        import i18n
        keys = i18n.keys_in(sent_text) or [FREE]
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "tenant": _tenant(), "keys": keys[:5]}
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            f.seek(0)
            lines = f.readlines()
            if len(lines) > KEEP_LINES * 2:
                f.seek(0)
                f.truncate()
                f.writelines(lines[-KEEP_LINES:])
    except Exception:  # silent-ok: след — подсказка разбору, его отказ не должен ронять отправку
        pass


def recent(limit: int = 20, days: int = 7) -> list[str]:
    """Последние строки следа своего тенанта: «ЧЧ:ММ ключ». Пусто при любом отказе чтения."""
    try:
        me = _tenant()
        since = datetime.now(timezone.utc) - timedelta(days=days)
        out: deque = deque(maxlen=limit)
        for line in _path().read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            ts = datetime.fromisoformat(rec["ts"])
            if rec.get("tenant") != me or ts < since:
                continue
            for k in reversed(rec["keys"]):
                out.append(f"{ts.astimezone().strftime('%H:%M')} {k}")
        return list(out)
    except Exception:  # silent-ok: нет следа — отчёт уходит без него, это не сбой
        return []
