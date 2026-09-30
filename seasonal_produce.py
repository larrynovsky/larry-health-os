"""
seasonal_produce.py — SHARED reference: сезонные продукты региона по месяцам.

Как пересеять правила питания: docs/how-to/reseed_food_rules.md.

Одна таблица для ОБОИХ тенантов. Вход для сезон×геном (S3): месяц → доступные продукты,
дальше пересекается с геномом/WCRF тенанта. Таблица — данные региона (private/region.yaml,
ключ `seasonal`: месяц → {fruits, vegetables, seafood}), не код: до 2026-09-23 стояла здесь
литералом и выдавала место жительства (нить pub-prep, BL-PUB-1). Пакета нет → сезон пуст,
питание судится несезонными продуктами. Ре-скрап: docs/how-to/reseed_seasonal_produce.md.
"""
from __future__ import annotations

import region_pack

_EMPTY = {"fruits": [], "vegetables": [], "seafood": []}


def in_season(month: int) -> dict[str, list[str]]:
    """Продукты в сезоне за месяц (1-12). Пустые списки, если месяц вне 1-12 или пакета нет."""
    table = region_pack.value("seasonal", {}) or {}
    return table.get(int(month), dict(_EMPTY))
