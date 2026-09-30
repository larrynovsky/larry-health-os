"""dashboard_filters — Jinja2 фильтры для dashboard templates.

Sprint 5 step 4 (2026-05-22): извлечено из dashboard.py.

Регистрируются как `templates.env.filters[...]` в main модуле:
  pretty_json, short, age_days, iso_date
"""
from __future__ import annotations
from _time_inject import get_now  # seam

import json
from datetime import datetime, timedelta


def _pretty_json(v) -> str:
    """JSON pretty-print с indent=2 и ensure_ascii=False.
    На invalid JSON или None — возвращает str(v) или ""."""
    if v is None:
        return ""
    try:
        return json.dumps(json.loads(v), ensure_ascii=False, indent=2)
    except (TypeError, ValueError, json.JSONDecodeError):
        return str(v)


def _short(v, n: int = 80) -> str:
    """Обрезает строку до n символов + многоточие если длиннее."""
    if v is None:
        return ""
    s = str(v)
    return s[:n] + "…" if len(s) > n else s


def _age_days(ts: str | None) -> int | None:
    """Дней с timestamp до сейчас. Принимает '%Y-%m-%d' или '%Y-%m-%d %H:%M:%S'."""
    if not ts:
        return None
    try:
        if " " in ts:
            dt = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
        else:
            dt = datetime.strptime(ts[:10], "%Y-%m-%d")
        return (get_now() - dt).days
    except (ValueError, TypeError):
        return None


def _iso_date(value):
    """First 10 chars = YYYY-MM-DD. Без многоточия (в отличие от _short(10))."""
    if not value:
        return ""
    return str(value)[:10]


# Сколько минут карточка считает консилиум «идущим» (2026-08-29): фоновая задача
# не переживает рестарт дашборда, поэтому без TTL карточка крутилась бы вечно.
# Полный консилиум сегодня — 105–170 с (17 участников × 2 раунда + синтез).
EVAL_TTL_MIN = 15


def _eval_running(payload: dict | None) -> bool:
    """Идёт ли фоновой консилиум по гипотезе: status=testing и eval_started_at
    моложе EVAL_TTL_MIN. Шаблон по этому решает, опрашивать ли карточку;
    хендлер eval — отказывать ли второму запуску."""
    if not payload or payload.get("status") != "testing":
        return False
    ts = payload.get("eval_started_at")
    if not ts:
        return False
    try:
        started = datetime.fromisoformat(str(ts))
    except (ValueError, TypeError):
        return False
    if started.tzinfo is not None:
        started = started.replace(tzinfo=None)
    return (get_now() - started) < timedelta(minutes=EVAL_TTL_MIN)
