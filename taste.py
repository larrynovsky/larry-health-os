"""
taste.py — ВКУСОВОЙ слой (тир 3, нить data-in-code-9).

Детерминированный: читает methodology/taste_profile.json (оракул — владелец, не LLM). Порядок в
системе: пол безопасности (абсолют) > ВКУС (hard_avoid/repulsion, почти абсолют) > польза (WCRF) >
мягкие предпочтения. Невкусное НЕ подсовываем: для полезного-но-невкусного — приемлемая форма или
замена, иначе пропуск. `no_taste_shaping` — система не спорит с вкусом.

Мост «русский пищевой keyword → вердикт» вынесен в methodology/taste_bridge.json (§9: данные, не
код; оракул — владелец). Мост связывает профиль с уточнениями допустимой формы блюда.
Порядок важен: hard_avoid/disliked раньше form/loved. status: hard_avoid|disliked|form|loved|neutral.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

_log = logging.getLogger(__name__)
_DIR = Path(__file__).resolve().parent / "methodology"
_PATH = _DIR / "taste_profile.json"
_BRIDGE_PATH = _DIR / "taste_bridge.json"


@lru_cache(maxsize=1)
def load_taste() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception:  # silent-ok: профиля нет → вкусовой слой нейтрален, ничего не режет
        return {}


@lru_cache(maxsize=1)
def _bridge() -> tuple:
    """Мост keyword→вердикт из methodology/taste_bridge.json (§9: данные, не код; оракул — владелец).
    Порядок сохраняется. Возвращает кортеж (compiled_regex, status, note). Сбой чтения → пусто
    (слой нейтрален, ничего не режет — как load_taste)."""
    try:
        raw = json.loads(_BRIDGE_PATH.read_text(encoding="utf-8")).get("bridge", []) or []
        out = []
        for e in raw:
            pat = e.get("pattern")
            if pat:
                out.append((re.compile(pat), e.get("status", "neutral"), e.get("note", "")))
        return tuple(out)
    except Exception as e:  # мост-файла нет/битый → вкус нейтрален, ничего не режем
        _log.warning("taste bridge load failed: %s", e)
        return tuple()


def verdict(food_name: str) -> dict:
    """Вкусовой вердикт по названию продукта/блюда. {status, note}. neutral если не распознано."""
    low = (food_name or "").lower()
    for rx, status, note in _bridge():
        if rx.search(low):
            return {"status": status, "note": note}
    return {"status": "neutral", "note": ""}


def acceptable(food_name: str) -> bool:
    """Подавать ли продукт. False только для hard_avoid/disliked (невкусное не подсовываем).
    form/loved/neutral → True (form — с приемлемой формой в note)."""
    return verdict(food_name)["status"] not in ("hard_avoid", "disliked")


def taste_note(food_name: str) -> str:
    """Короткая вкусовая пометка (форма/бан-причина). Пусто, если сказать нечего."""
    return verdict(food_name)["note"]


def rank_key(food_name: str) -> int:
    """Ключ приоритета: любимое вперёд (0), нейтраль/форма (1), нежелательное в конец (2)."""
    s = verdict(food_name)["status"]
    return {"loved": 0, "hard_avoid": 2, "disliked": 2}.get(s, 1)


def filter_items(names: list[str]) -> list[str]:
    """Убрать неподаваемое (hard_avoid/disliked), любимое — вперёд. Стабильно."""
    keep = [n for n in names if acceptable(n)]
    return sorted(keep, key=rank_key)
