"""
food_genome.py — сезон×геном (S3, второй этап): выгодные продукты месяца ПЕР-ТЕНАНТ.

Дериват: сезонная таблица (SHARED) × геном тенанта (его БД) × WCRF/мед-ограничения →
что в сезоне И полезно ИМЕННО этому тенанту. Пер-тенант: геном берётся из БД процесса
(subject=self), партнёрский список — из health_partner-генома, не владельцаного.

ЧЕСТНАЯ ГРАНИЦА (факт): нутригеномика для большинства SNP слабо-доказательна. Опора —
СИЛЬНЫЕ связи + WCRF-онко-диета (противовоспалительное, клетчатка), НЕ хайповые гены:
- WCRF-база (широко полезно, особ. онко): томаты(ликопин), крестоцветные, клетчатка-фрукты,
  бобовые, полифенолы-ягоды/виноград/гранат, оливковое/орехи.
- Сильная гено-связь: MTHFR (нарушен фолатный цикл) → натуральный фолат из ЗЕЛЕНИ/бобовых
  (не синтетическая фолиевая кислота — она в avoid-списке профиля).

Refresh «раз в месяц» бесплатен: список пересчитывается из ТЕКУЩЕГО генома при каждом
брифе → обновление генома в БД само отражается на следующем брифе (materialized-view даром).
Кулдаун per-продукт (gate) даёт ротацию ~месяц, без day%5.
"""
from __future__ import annotations

import seasonal_produce as _sp
import i18n
import region_pack

# Продукт (как в seasonal_produce) → (benefit_tag, почему). WCRF/Med-обоснование.
_FOOD_BENEFITS_KEYS: dict[str, tuple[str, str, str]] = {
    'виноград': ('polyphenols', 'food.benefit.grapes', 'food.item.grapes'),
    'персики': ('vitamin_ac', 'food.benefit.peaches', 'food.item.peaches'),
    'нектарины': ('vitamin_ac', 'food.benefit.nectarines', 'food.item.nectarines'),
    'сливы': ('polyphenols', 'food.benefit.plums', 'food.item.plums'),
    'абрикосы': ('carotene', 'food.benefit.apricots', 'food.item.apricots'),
    'груши': ('fiber', 'food.benefit.pears', 'food.item.pears'),
    'клубника': ('vitamin_c', 'food.benefit.strawberries', 'food.item.strawberries'),
    'черешня': ('polyphenols', 'food.benefit.cherries', 'food.item.cherries'),
    'яблоки': ('fiber', 'food.benefit.pears', 'food.item.apples'),
    'киви': ('vitamin_c', 'food.benefit.kiwi', 'food.item.kiwi'),
    'айва': ('fiber', 'food.benefit.fiber', 'food.item.quince'),
    'цитрусовые': ('vitamin_c', 'food.benefit.citrus', 'food.item.citrus'),
    'апельсины': ('vitamin_c', 'food.benefit.citrus', 'food.item.oranges'),
    'мандарины': ('vitamin_c', 'food.benefit.kiwi', 'food.item.mandarins'),
    'грейпфрут': ('vitamin_c', 'food.benefit.grapefruit', 'food.item.grapefruit'),
    'лимоны': ('vitamin_c', 'food.benefit.kiwi', 'food.item.lemons'),
    'помидоры': ('lycopene', 'food.benefit.tomatoes', 'food.item.tomatoes'),
    'листовая зелень': ('folate', 'food.benefit.leafy_greens', 'food.item.leafy_greens'),
    'зелень': ('folate', 'food.benefit.leafy_greens', 'food.item.herbs'),
    'капуста': ('cruciferous', 'food.benefit.cabbage', 'food.item.cabbage'),
    'цветная капуста': ('cruciferous', 'food.benefit.cabbage', 'food.item.cauliflower'),
    'артишоки': ('folate_fiber', 'food.benefit.artichokes', 'food.item.artichokes'),
    'бобы': ('legumes', 'food.benefit.broad_beans', 'food.item.broad_beans'),
    'горошек': ('legumes', 'food.benefit.peas', 'food.item.peas'),
    'баклажаны': ('fiber', 'food.benefit.aubergines', 'food.item.aubergines'),
    'перец': ('vitamin_c', 'food.benefit.peppers', 'food.item.peppers'),
    'цукини': ('fiber', 'food.benefit.courgettes', 'food.item.courgettes'),
    'огурцы': ('hydration', 'food.benefit.cucumbers', 'food.item.cucumbers'),
    'тыква': ('carotene', 'food.benefit.pumpkin', 'food.item.pumpkin'),
    'сельдерей': ('fiber', 'food.benefit.fiber', 'food.item.celery'),
    'свёкла': ('folate_fiber', 'food.benefit.artichokes', 'food.item.beetroot'),
    'малина': ('polyphenols', 'food.benefit.plums', 'food.item.raspberries'),
    'смородина': ('vitamin_c', 'food.benefit.strawberries', 'food.item.currants'),
    'черника': ('polyphenols', 'food.benefit.cherries', 'food.item.blueberries'),
    'морковь': ('carotene', 'food.benefit.pumpkin', 'food.item.carrots'),
}

# Морепродукты → польза. Омега-3/постный белок, WCRF-нейтрально.
_SEAFOOD_BENEFITS_KEYS: dict[str, tuple[str, str, str]] = {
    'сардины': ('omega3', 'food.benefit.sardines', 'food.item.sardines'),
    'анчоусы': ('omega3', 'food.benefit.sardines', 'food.item.anchovies'),
    'скумбрия': ('omega3', 'food.benefit.sardines', 'food.item.mackerel'),
    'тунец': ('omega3', 'food.benefit.tuna', 'food.item.tuna'),
    'креветки': ('shellfish', 'food.benefit.prawns', 'food.item.prawns'),
    'кальмары': ('shellfish', 'food.benefit.lean_protein', 'food.item.squid'),
    'сельдь': ('omega3', 'food.benefit.sardines', 'food.item.herring'),
    'треска': ('lean_fish', 'food.benefit.lean_protein', 'food.item.cod'),
    'лосось': ('omega3', 'food.benefit.sardines', 'food.item.salmon'),
    'форель': ('omega3', 'food.benefit.sardines', 'food.item.trout'),
    'пикша': ('lean_fish', 'food.benefit.lean_protein', 'food.item.haddock'),
    'хек': ('lean_fish', 'food.benefit.lean_protein', 'food.item.hake'),
    'мидии': ('shellfish', 'food.benefit.prawns', 'food.item.mussels'),
}

# Алиас остаётся языком исходных данных; подпись и причина переводятся отдельно.
# Пакет установки заменяет весь каталог: нейтральные добавления не меняют её правила.
_FOOD_CATALOG = region_pack.value("food_catalog", {
    kind: {alias: {"tag": tag,
                   "why": {lang: i18n.t(why_key, lang) for lang in i18n.LANGS},
                   "label": {lang: i18n.t(label_key, lang) for lang in i18n.LANGS}}
           for alias, (tag, why_key, label_key) in entries.items()}
    for kind, entries in (("produce", _FOOD_BENEFITS_KEYS), ("seafood", _SEAFOOD_BENEFITS_KEYS))
})


def food_catalog_for_person(lang: str | None = None) -> dict[str, tuple[str, str]]:
    """Польза по исходному алиасу; перевод подписи не участвует в отборе."""
    lang = lang or i18n.lang_of()
    return {alias: (entry["tag"], i18n.pick(entry["why"], lang))
            for entries in _FOOD_CATALOG.values() for alias, entry in entries.items()}


# Читатели MODEL получают прежние русские причины из того же каталога.
FOOD_BENEFITS = {alias: (entry["tag"], i18n.pick(entry["why"], "ru"))
                 for alias, entry in _FOOD_CATALOG["produce"].items()}
_SEAFOOD_BENEFITS = {alias: (entry["tag"], i18n.pick(entry["why"], "ru"))
                     for alias, entry in _FOOD_CATALOG["seafood"].items()}


# Продукты с натуральным фолатом — усиливаем при патогенном MTHFR.
_FOLATE_FOODS = {"листовая зелень", "артишоки", "бобы", "горошек", "свёкла"}


def _has_pathogenic(genes_rows: list, gene: str) -> bool:
    """genes_rows — строки genetic_variants (dict/Row) тенанта. True если ген патогенный."""
    g = gene.lower()
    for r in genes_rows or []:
        rn = (r.get("gene") if hasattr(r, "get") else r["gene"]) or ""
        sig = (r.get("significance") if hasattr(r, "get") else r["significance"]) or ""
        if rn.lower() == g and ("Pathogenic" in sig or "Risk" in sig):
            return True
    return False


def _onco_focus_foods(conn) -> set:
    """Продукты онко-ЭМФАЗЫ ТОЛЬКО если у тенанта активно онко-состояние (гейт по проблем-листу
    через clinical_kb). Восстанавливает эмфазу, которую убрал Инкремент B вместе с онко-текстом,
    но теперь ГЕЙТОВАННО: онко-тенант получает, тенант без онко — нет. Источник — данные, не код."""
    if conn is None:
        return set()
    try:
        import clinical_kb as _ckb
        foods = set()
        for e in _ckb.active_entries(conn, "food"):
            if e.get("kind") == "benefit_focus" and e.get("condition_key") == "oncology":
                foods |= set((e.get("payload") or {}).get("foods") or [])
        return foods
    except Exception:  # silent-ok: clinical_kb недоступна → без онко-эмфазы, бриф не падает
        return set()


def beneficial_this_month(month: int, genes_rows: list = None, conn=None, *, lang: str | None = None) -> list[dict]:
    """Сезонное ∩ полезное для ЭТОГО тенанта → [{food, why, boosted}].
    Каталог — FOOD_BENEFITS (нейтральный нутриент-факт). Эмфаза (boosted вперёд): MTHFR-фолат
    (genes_rows, как раньше) + онко-крестоцветные/ликопин (гейт clinical_kb по проблем-листу, conn)."""
    if lang is None:
        from clinical_kb import clinical_kb_language
        lang = clinical_kb_language(conn)
    season = _sp.in_season(month)
    fruits = set(season.get("fruits") or [])
    seafood = set(season.get("seafood") or [])
    produce = (list(season.get("fruits") or []) + list(season.get("vegetables") or [])
               + list(season.get("seafood") or []))
    benefits = food_catalog_for_person(lang)
    labels = {alias: entry["label"] for entries in _FOOD_CATALOG.values()
              for alias, entry in entries.items()}
    mthfr = _has_pathogenic(genes_rows, "MTHFR")
    onco_foods = _onco_focus_foods(conn)
    out: list[dict] = []
    for p in produce:
        if p in benefits:
            tag, why = benefits[p]
            folate_boost = mthfr and p in _FOLATE_FOODS
            onco_boost = p in onco_foods
            boost = folate_boost or onco_boost
            if folate_boost:
                # НЕ называем ген в тексте брифа (гены ≤1/мес + скраб их режет);
                # отбор по геному остаётся, формулировка — про пользу, не про MTHFR.
                why = i18n.t('food.benefit.folate_boost', lang, why=why)
            elif onco_boost:
                why = i18n.t('food.benefit.current_boost', lang, why=why)
            kind = 0 if p in fruits else (2 if p in seafood else 1)
            out.append({"food": i18n.pick(labels[p], lang), "food_alias": p, "tag": tag, "why": why, "boosted": boost,
                        "is_fruit": p in fruits, "kind": kind})
    # Приоритет: ФРУКТЫ(0) > овощи(1) > морепродукты(2) (решение владельца), внутри — boosted вперёд.
    out.sort(key=lambda x: (x["kind"], not x["boosted"]))
    return out


# Паттерн категорий по дням (период 5): фрукты чаще (приоритет), овощи и рыба — регулярно.
_FOOD_PATTERN = ("fruit", "veg", "fruit", "sea", "fruit")  # 3/5 фрукты, 1/5 овощ, 1/5 рыба


def food_of_day(month: int, genes_rows: list, day_index: int, *, lang: str | None = None) -> dict | None:
    """Продукт дня — детерминированная ВЗВЕШЕННАЯ ротация по дате (решение владельца: ротация
    залипала на одном продукте). Проблема была: гейт по severity вечно брал один продукт. Теперь: паттерн
    категорий (фрукты 3/5 — приоритет, овощи 1/5, рыба 1/5), внутри категории — ротация по
    дате → день за днём разное, фрукты чаще, но и овощи с рыбой регулярно. day_index=ordinal."""
    items = beneficial_this_month(month, genes_rows, lang=lang)
    if not items:
        return None
    by_kind = {0: [], 1: [], 2: []}
    for it in items:
        by_kind[it["kind"]].append(it)
    cat = _FOOD_PATTERN[day_index % len(_FOOD_PATTERN)]
    pool = {"fruit": by_kind[0], "veg": by_kind[1], "sea": by_kind[2]}[cat] or items
    return pool[day_index % len(pool)]
