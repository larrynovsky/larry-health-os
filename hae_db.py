"""hae_db.py — доменный модуль hae. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import json
import os
import logging
from datetime import date, timedelta
from pathlib import Path

from _time_inject import get_today

log = logging.getLogger(__name__)


def get_hae_registry() -> dict[str, dict]:
    """Возвращает {metric_name: row_dict} для всего реестра."""
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT metric_name, status, first_seen, last_seen, "
            "sample_values, unit, alerted_at, notes "
            "FROM hae_metric_registry"
        ).fetchall()
    return {r["metric_name"]: dict(r) for r in rows}


def upsert_hae_metric(metric_name: str, status: str | None = None,
                      unit: str | None = None,
                      sample_values: list | None = None,
                      alerted_at: str | None = None,
                      data_first: str | None = None,
                      data_last: str | None = None) -> None:
    """Вставляет или обновляет запись в реестре.

    Семантика first_seen/last_seen = ПОКРЫТИЕ ДАННЫХ (вариант A, решение владельца
    2026-07-12): first_seen = самая ранняя дата измерения, last_seen = самая
    поздняя. Слияние МОНОТОННОЕ — first_seen только раньше, last_seen только
    позже (покрытие не сужается), NULL-safe. Даты берутся из data_first/data_last
    (min/max дат записей, добывает hae_checker._parse_hae). Если даты не переданы
    (напр. alert-джоба ставит только alerted_at) — first_seen/last_seen НЕ трогаем.
    Раньше поля штамповались датой прогона чекера (смысл B) — заменено.
    """
    import json as _j
    with _hdb.get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM hae_metric_registry WHERE metric_name=?",
            (metric_name,)
        ).fetchone()
        if existing:
            updates, params = [], []
            # Покрытие: расширяем только наружу (first раньше / last позже), NULL-safe.
            if data_first is not None:
                updates.append("first_seen = CASE WHEN first_seen IS NULL "
                               "OR first_seen > ? THEN ? ELSE first_seen END")
                params.extend([data_first, data_first])
            if data_last is not None:
                updates.append("last_seen = CASE WHEN last_seen IS NULL "
                               "OR last_seen < ? THEN ? ELSE last_seen END")
                params.extend([data_last, data_last])
            if status is not None:
                updates.append("status=?"); params.append(status)
            if unit is not None:
                updates.append("unit=?");   params.append(unit)
            if sample_values is not None:
                updates.append("sample_values=?")
                params.append(_j.dumps(sample_values[:5], ensure_ascii=False))
            if alerted_at is not None:
                updates.append("alerted_at=?"); params.append(alerted_at)
            if not updates:
                return  # голый upsert без новых сведений — ничего не меняем
            params.append(metric_name)
            conn.execute(
                f"UPDATE hae_metric_registry SET {', '.join(updates)} WHERE metric_name=?",
                params
            )
        else:
            conn.execute(
                "INSERT INTO hae_metric_registry"
                "(metric_name, status, first_seen, last_seen, unit, sample_values) "
                "VALUES (?,?,?,?,?,?)",
                (metric_name, status or "new", data_first, data_last,
                 unit, _j.dumps(sample_values[:5] if sample_values else [],
                                ensure_ascii=False))
            )


def get_pending_hae_alerts(alert_cooldown_days: int = 7) -> list[dict]:
    """Возвращает метрики которые нужно алертить: status='new' AND
    (alerted_at IS NULL OR alerted_at < today - cooldown)."""
    cutoff = str(get_today() - timedelta(days=alert_cooldown_days))
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT metric_name, status, first_seen, last_seen, "
            "sample_values, unit, notes "
            "FROM hae_metric_registry "
            "WHERE status='new' AND (alerted_at IS NULL OR alerted_at < ?) "
            "ORDER BY first_seen",
            (cutoff,)
        ).fetchall()
    return [dict(r) for r in rows]


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
