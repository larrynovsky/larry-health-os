"""
repertoire.py — выбор и ротация блюд из необязательного частного репертуара.

Читает methodology/repertoire.json — пользовательский каталог с категориями и ссылками.
Если файла нет (например, в публичном клоне), репертуар пуст и бриф блюд не предлагает.
Наличие блюда в каталоге не доказывает, что его готовили. Вкус-фильтр отсекает
жёстко несовместимые варианты; настройки пользователя определяют допустимую форму блюда.
Подборка строится по снимку каталога.

suggest_set разносит подборку по категориям и ротирует по seed, чтобы за месяцы всплывал весь
репертуар, а не одни и те же 12 блюд («сервис, не проклятие»).
"""
from __future__ import annotations

import json
import logging
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

import i18n

_log = logging.getLogger(__name__)
_PATH = Path(__file__).resolve().parent / "methodology" / "repertoire.json"


@lru_cache(maxsize=1)
def load_repertoire() -> tuple:
    try:
        dishes = json.loads(_PATH.read_text(encoding="utf-8")).get("dishes", []) or []
        return tuple(json.dumps(d, ensure_ascii=False) for d in dishes)  # хэшируемо для кэша
    except Exception as e:
        _log.warning("repertoire load failed: %s", e)
        return tuple()


def _dishes() -> list[dict]:
    return [json.loads(s) for s in load_repertoire()]


def _acceptable(dish: dict) -> bool:
    """Отсечь только ЖЁСТКИЙ бан вкусового профиля (taste.verdict) в названии/белке/овощах.
    Dislike-ингредиент внутри блюда — ок (его блюда = уже приемлемая форма). Старую форму
    (protein/veg) читаем defensively — новые данные их не содержат."""
    import taste
    tokens = [dish.get("title", ""), dish.get("protein", "")] + list(dish.get("veg") or [])
    return all(taste.verdict(i18n.pick(t, "ru"))["status"] != "hard_avoid" for t in tokens)


def _why(dish: dict, lang: str | None = None) -> str:
    cat = i18n.pick(dish.get("category", ""), lang)
    return (i18n.t("food.repertoire.reason_category", lang, category=cat) if cat
            else i18n.t("food.repertoire.reason", lang))


def suggest(day_index: int = 0, month: int | None = None, *, lang: str | None = None) -> dict | None:
    """Одно блюдо «из своего» (обратная совместимость). None если репертуар пуст."""
    lang = lang or i18n.lang_of()
    dishes = [d for d in _dishes() if _acceptable(d)]
    if not dishes:
        return None
    d = dishes[day_index % len(dishes)]
    return {"title": i18n.pick(d["title"], lang), "why": _why(d, lang), "url": d.get("url", ""), "tags": []}


def suggest_set(seed: int = 0, n: int = 6, *, lang: str | None = None) -> list[dict]:
    """До n блюд «из своего», разнесённых по категориям, вкус-совместимых, ротация по seed.
    Детерминировано; пустой список если репертуар пуст. Round-robin по категориям + сдвиг
    внутри категории — так месяц к месяцу подборка обновляется и покрывает весь репертуар."""
    lang = lang or i18n.lang_of()
    dishes = [d for d in _dishes() if _acceptable(d)]
    if not dishes:
        return []
    by_cat: "OrderedDict[str, list]" = OrderedDict()
    for d in dishes:
        by_cat.setdefault(i18n.pick(d.get("category", ""), "ru"), []).append(d)
    cats = list(by_cat.keys())
    out: list[dict] = []
    used: set[str] = set()
    ci, guard = seed % len(cats), 0
    while len(out) < n and guard < len(cats) * 4:
        pool = by_cat[cats[ci % len(cats)]]
        for k in range(len(pool)):
            d = pool[(seed + k) % len(pool)]
            if i18n.pick(d["title"], "ru") not in used:
                used.add(i18n.pick(d["title"], "ru"))
                out.append(d)
                break
        ci += 1
        guard += 1
    return [{"title": i18n.pick(d["title"], lang), "why": _why(d, lang), "url": d.get("url", ""),
             "category": i18n.pick(d.get("category", ""), lang)} for d in out]
