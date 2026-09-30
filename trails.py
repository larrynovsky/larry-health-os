"""
trails.py — SHARED reference: пешие тропы у дома тенантов (семейная активность).

Карта показывается каждому тенанту, когда ОН дома + выходной + погода ок. Список — данные
региона (private/region.yaml, ключ `trails`: {name, km|None, note}), не код: до 2026-09-23 он
стоял здесь литералом и выдавал место жительства (нить pub-prep, BL-PUB-1). Пакета нет →
тропы не предлагаются (pick_trail → None). Ре-скрап списка: docs/how-to/refresh_trail_list.md.
"""
from __future__ import annotations

import region_pack


def trails() -> list[dict]:
    """Тропы региона из пакета; нет пакета — пусто."""
    return list(region_pack.value("trails", []) or [])


def pick_trail(day_index: int) -> dict | None:
    """Детерминированный выбор тропы по индексу (ротация). day_index — стабильный счётчик
    (напр. ordinal даты); gate-кулдаун per-тропа не даёт повтора. None если список пуст."""
    ts = trails()
    if not ts:
        return None
    return ts[day_index % len(ts)]
