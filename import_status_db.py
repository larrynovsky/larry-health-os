"""import_status_db.py — доменный модуль import_status. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import json
import os
import logging
from datetime import date, timedelta
from pathlib import Path


log = logging.getLogger(__name__)


def set_import_status(source: str, ts: str = None) -> None:
    """Записывает метку времени последнего успешного импорта источника."""
    from datetime import datetime as _dt
    ts = ts or _dt.now().isoformat(timespec="seconds")
    _hdb.save_memory("import_status", ts, key=f"last_import_{source}",
                confidence=1.0, source="system")


def get_import_staleness(source: str) -> tuple[bool, float]:
    """
    Возвращает (is_stale, age_hours) для источника данных.
    is_stale=True если данные устарели по conit-лимиту.
    Если метка отсутствует — считается свежей (graceful degradation).
    """
    from datetime import datetime as _dt
    _hdb._ensure_memory_table()
    with _hdb.get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM memory WHERE key=? AND active=1 ORDER BY updated_at DESC LIMIT 1",
            (f"last_import_{source}",)
        ).fetchone()
    if not row:
        return False, 0.0  # нет данных о статусе — не блокировать
    try:
        last_ts = _dt.fromisoformat(row["value"])
        age_h = (_dt.now() - last_ts).total_seconds() / 3600.0
        limit = get_conit_limit(source)
        return age_h > limit, round(age_h, 1)
    except Exception:
        return False, 0.0


def check_data_freshness(sources: list = None) -> dict:
    """
    Проверяет свежесть данных по всем или указанным источникам.
    Возвращает dict с предупреждениями для устаревших данных.
    Используется агентами для добавления контекста в ответы.
    """
    sources = sources or ["oura", "apple_health", "oncology_pdf", "withings"]
    warnings = {}
    for src in sources:
        is_stale, age_h = get_import_staleness(src)
        if is_stale:
            limit = get_conit_limit(src)
            warnings[src] = {
                "age_hours": age_h,
                "limit_hours": limit,
                "message": f"Данные {src} устарели: {age_h:.1f}ч (лимит {limit:.0f}ч)"
            }
    return warnings


def get_conit_limit(source: str) -> float:
    """Возвращает лимит свежести для источника данных (часы).
    Читает из system_config, fallback на _hdb.CONIT_LIMITS_HOURS."""
    val = _hdb.get_config(f"conit_limit.{source}")
    if val is not None:
        return float(val)
    return _hdb.CONIT_LIMITS_HOURS.get(source, 26.0)


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
