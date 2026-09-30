"""
food_staples.py — SHARED reference: несезонные базовые продукты (мясо/яйца/молочное/
бакалея/жиры/рыба-консервы), всегда доступны. Для вариативности брифа сверх сезонного
(владелец 2026-07-14) и как каркас квартального документа.

Теги — язык рамки (food_profile.build_food_profile фильтрует/аннотирует по ним):
protein, lean, iron, b12, folate, d, ca, fiber, low_gi, unsat, sat_fat, calorie_dense,
lactose, lactose_low, purine, omega3, choline.
"""
from __future__ import annotations

import i18n

_STAPLE_KEYS: list[dict] = [
    {"item": "food.staple.poultry", "cat": "food.staple_category.meat",
     "tags": {"lean", "protein"}, "why": "food.benefit.lean_protein"},
    {"item": "food.staple.lean_beef", "cat": "food.staple_category.meat",
     "tags": {"iron", "protein", "sat_fat"}, "why": "food.staple_benefit.lean_beef"},
    {"item": "food.staple.liver", "cat": "food.staple_category.meat",
     "tags": {"b12", "folate", "iron", "purine"}, "why": "food.staple_benefit.liver"},
    {"item": "food.staple.eggs", "cat": "food.staple.eggs",
     "tags": {"b12", "choline", "d", "protein"}, "why": "food.staple_benefit.eggs"},
    {"item": "food.staple.greek_yogurt", "cat": "food.staple_category.dairy",
     "tags": {"ca", "lactose", "protein"}, "why": "food.staple_benefit.greek_yogurt"},
    {"item": "food.staple.kefir", "cat": "food.staple_category.dairy",
     "tags": {"ca", "lactose", "protein"}, "why": "food.staple_benefit.greek_yogurt"},
    {"item": "food.staple.cottage_cheese", "cat": "food.staple_category.dairy",
     "tags": {"ca", "lactose", "protein"}, "why": "food.staple_benefit.cottage_cheese"},
    {"item": "food.staple.hard_cheese", "cat": "food.staple_category.dairy",
     "tags": {"ca", "calorie_dense", "lactose_low", "protein", "sat_fat"}, "why": "food.staple_benefit.hard_cheese"},
    {"item": "food.staple.pulses", "cat": "food.staple_category.pantry",
     "tags": {"fiber", "folate", "iron", "low_gi", "protein"}, "why": "food.staple_benefit.pulses"},
    {"item": "food.staple.oats", "cat": "food.staple_category.pantry",
     "tags": {"fiber", "low_gi"}, "why": "food.staple_benefit.oats"},
    {"item": "food.staple.buckwheat", "cat": "food.staple_category.pantry",
     "tags": {"fiber", "iron", "low_gi"}, "why": "food.staple_benefit.buckwheat"},
    {"item": "food.staple.quinoa", "cat": "food.staple_category.pantry",
     "tags": {"fiber", "protein"}, "why": "food.staple_benefit.quinoa"},
    {"item": "food.staple.nuts", "cat": "food.staple_category.pantry",
     "tags": {"calorie_dense", "unsat"}, "why": "food.staple_benefit.nuts"},
    {"item": "food.staple.nut_butter", "cat": "food.staple_category.pantry",
     "tags": {"calorie_dense", "protein", "unsat"}, "why": "food.staple_benefit.nut_butter"},
    {"item": "food.staple.olive_oil", "cat": "food.staple_category.fats",
     "tags": {"calorie_dense", "unsat"}, "why": "food.staple_benefit.olive_oil"},
    {"item": "food.item.avocado", "cat": "food.staple_category.fats",
     "tags": {"calorie_dense", "folate", "unsat"}, "why": "food.staple_benefit.avocado"},
    {"item": "food.staple.tahini", "cat": "food.staple_category.fats",
     "tags": {"ca", "calorie_dense", "unsat"}, "why": "food.staple_benefit.tahini"},
    {"item": "food.staple.canned_fish", "cat": "food.staple_category.seafood",
     "tags": {"b12", "ca", "d", "omega3", "protein"}, "why": "food.staple_benefit.canned_fish"},
]

# Микронутриент → где взять (для секции «фокус» в документе/брифе).
_MICRO_FOOD_KEYS: dict[str, str] = {
    "B12": "food.micro.b12",
    "iron": "food.micro.iron",
    "folate": "food.micro.folate",
    "D": "food.micro.d",
    "Ca": "food.micro.ca",
}


def staples_for_person(lang: str | None = None) -> list[dict]:
    """Localized view; the curated tags are independent of language."""
    lang = lang or i18n.lang_of()
    return [{**s, **{field: i18n.t(s[field], lang) for field in ("item", "cat", "why")},
             "tags": set(s["tags"])} for s in _STAPLE_KEYS]


def micro_foods_for_person(lang: str | None = None) -> dict[str, str]:
    lang = lang or i18n.lang_of()
    return {m: i18n.t(k, lang) for m, k in _MICRO_FOOD_KEYS.items()}


# Compatibility for model-prompt readers outside this batch. No copied display strings.
STAPLES = staples_for_person("ru")
MICRO_FOODS = micro_foods_for_person("ru")
