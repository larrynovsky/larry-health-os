"""region_pack — региональный пакет дома тенантов: единственный читатель private/region.yaml.

ЗАЧЕМ. Сезонная норма жары, фид предупреждений, город станции воздуха, таймзона, сезонные
продукты и тропы — это ДАННЫЕ региона, где живут тенанты, а не код. До 2026-09-23 они
стояли литералами в пяти модулях (env_context, env_sources, seasonal_produce, тропы,
расписания) и выдавали бы место жительства владельца в открытом репозитории (нить pub-prep,
BL-PUB-1; решение владельца 2026-09-23 — проект когда-нибудь откроется на GitHub).

Файла нет (публичный клон) → get() отдаёт default. Потребители обязаны вести себя честно
без пакета: жара — абсолютным порогом, моря/троп/предупреждений нет, таймзона UTC. Это
«молчим, не фантазируем» подсистемы среды, а не новая политика.

ГРАНИЦА: пакет один на установку — у тенантов общий дом. Тенант с другим регионом потребует
пакет per-tenant; на 2026-09-23 такого тенанта нет.
"""
from __future__ import annotations

import functools
import logging
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

PATH = Path(__file__).resolve().parent / "private" / "region.yaml"


@functools.lru_cache(maxsize=1)
def _load() -> dict:
    if not PATH.exists():
        log.warning("region_pack: %s нет — региональные якоря выключены", PATH.name)
        return {}
    return yaml.safe_load(PATH.read_text(encoding="utf-8")) or {}


def value(key: str, default=None):
    """Значение пакета по ключу; нет пакета или ключа → default."""
    return _load().get(key, default)
