"""
food_profile.py — персональный пищевой профиль (per-tenant, МЕДКАРТА-доминантный).

Несущий принцип (N12 проекта): решает ДЕТЕРМИНИРОВАННЫЙ слой (medical_frame по курируемым
правилам), LLM только рендерит текст. Медицинские решения принимает КОД, не модель — поэтому
«постное тому, кому медкарта это запрещает» структурно невозможно.

Иерархия входов (жёсткая): медкарта (problem_list+profile) → активные гипотезы (диет-релевантные
паттерны) → геном (сильные связи) → WCRF-база. Геном НЕ отменяет медицинскую рамку: при
energy=gain/maintain генетические ограничения жира/порций СНИМАЮТСЯ (калории важнее).

Per-tenant derived (subject=self, из БД процесса). Materialized view — считается из текущих
входов при обращении. Дизайн: план docs/handoff + PLAN_food_profile.
"""
from __future__ import annotations

import logging

import i18n
from clinical_kb import clinical_kb_language

# ── Курируемые правила состояний ПЕРЕНЕСЕНЫ в clinical_kb (brief-neutralization Фаза 2 B2.2).
# Условие→рамка (медкарта/ИМТ/геном) теперь в methodology/clinical_kb/{_index,food}.yaml, читается
# через clinical_kb.active_entries(kind='frame_rule') в medical_frame. §9: имя болезни/ген — данные
# с оракулом, не хардкод в коде. Паритет старой рамки доказан 13/13 (включая suppress-when-gain).
# Здесь остаётся только СТРУКТУРНАЯ механика движка (suppress-доминанта), не клиническое знание.
_SUPPRESSED_WHEN_GAIN = {"sat_fat_limit", "portion_aware"}


def _bmi(profile: dict) -> float | None:
    ident = (profile or {}).get("identity", {}) or {}
    try:
        h = float(ident.get("height_cm")) / 100.0
        w = float(ident.get("weight_kg"))
        return round(w / (h * h), 1) if h > 0 else None
    except Exception:  # silent-ok: нет/битый рост-вес → ИМТ неизвестен, рамка без BMI-правила
        return None


# Геном→рамка (lactase/APOE/FTO/MTHFR-cycle/TCF7L2) ПЕРЕНЕСЕНЫ в clinical_kb как genome-triggered
# frame_rule (_index.yaml условия + food.yaml по_condition). Точные rsid/генотипы сохранены;
# паритет доказан. Хардкод _genotype/_genome_flags удалён (§9).

# Source messages are translated only when the stored text still matches.
_FRAME_REASON_KEYS = {
    "food_resection_frame": "food.frame_reason.food_resection_frame",
    "food_b12_folate_frame": "food.frame_reason.food_b12_folate_frame",
    "food_iron_frame": "food.frame_reason.food_iron_frame",
    "food_dysglycemia_frame": "food.frame_reason.food_dysglycemia_frame",
    "food_gout_frame": "food.frame_reason.food_gout_frame",
    "food_nutritional_risk_frame": "food.frame_reason.food_nutritional_risk_frame",
    "food_low_bmi_frame": "food.frame_reason.food_low_bmi_frame",
    "food_lactose_free": "food.frame_reason.food_lactose_free",
    "food_apoe_satfat": "food.frame_reason.food_apoe_satfat",
    "food_fto_portion": "food.frame_reason.food_fto_portion",
    "food_mthfr_cycle_micro": "food.frame_reason.food_mthfr_cycle_micro",
    "food_tcf7l2_lowgi": "food.frame_reason.food_tcf7l2_lowgi",
}

_ENERGY_ORDER = {"standard": 0, "maintain": 1, "gain": 2}


def _merge_frame(base: dict, rf: dict) -> dict:
    """Наложить frame сгенерированного правила на базовую рамку. Энергию НЕ понижаем,
    protein=high побеждает, micro/constraints объединяем. Возвращает НОВУЮ рамку (копию)."""
    out = dict(base)
    out["micro"] = set(base.get("micro", set()) or set())
    out["constraints"] = set(base.get("constraints", set()) or set())
    e = rf.get("energy")
    if e in _ENERGY_ORDER and _ENERGY_ORDER[e] > _ENERGY_ORDER.get(out.get("energy", "standard"), 0):
        out["energy"] = e
    if rf.get("protein") == "high":
        out["protein"] = "high"
    out["micro"] |= set(rf.get("micro", []) or [])
    out["constraints"] |= set(rf.get("constraints", []) or [])
    return out


def _ckb_med_text(conn, profile: dict | None = None) -> str:
    """Текст тенанта — из ЕДИНСТВЕННОГО дома clinical_kb.patient_med_text (сведено 2026-09-01:
    построений было три; своей копии здесь нет и быть не должно — дубль-гейт прав)."""
    import clinical_kb as _ckb
    return _ckb.patient_med_text(conn, profile)


def rule_fires(payload: dict, med_text: str) -> bool | None:
    """Срабатывает ли сгенерированное правило на тексте тенанта. None — regex не компилируется
    (это дефект правила, не «не сработало»). Единственный предикат для критика и применителя."""
    import re as _re
    cond = (payload or {}).get("condition") or {}
    rx = (cond.get("detect") or {}).get("regex") or ""
    label = (cond.get("label") or "").lower()
    if rx:
        try:
            if _re.search(rx, med_text):
                return True
        except _re.error:
            return None
    return bool(label and label in med_text)


def _apply_active_generated(frame: dict, conn, med_text: str) -> dict:
    """Наложить АКТИВНЫЕ сгенерированные правила (тир 2) поверх детерминированной рамки.
    Fail-closed по полу: правило, чьё наложение пробивает пол безопасности, ПРОПУСКАЕТСЯ
    (база уже полу удовлетворяет). Пустой склад → рамка не меняется (поведение прода сейчас)."""
    try:
        import re as _re
        import generated_food_rules as _gfr
        import food_floor as _ff
        for rule in _gfr.get_active_rules(conn=conn):
            if not rule_fires(rule.get("payload") or {}, med_text):
                continue
            cand = _merge_frame(frame, (rule.get("payload") or {}).get("frame") or {})
            if _ff.assert_floor(cand, conn=conn, med_text=med_text):
                logging.getLogger(__name__).warning(
                    "food gen-rule %s пробивает пол — пропущено (fail-closed)", rule.get("rule_key"))
                continue
            frame = cand
    except Exception as e:
        logging.getLogger(__name__).warning("apply generated rules skipped: %s", e)
    return frame


def medical_frame(conn=None, profile: dict | None = None, *, lang: str | None = None) -> dict:
    """Детерминированная РАМКА питания тенанта из медкарты+гипотез+генома (per-tenant).

    Возвращает {energy, protein, micro:set, constraints:set, reasons:[...]}.
    energy: 'gain'|'maintain'|'standard'. Медкарта доминирует над геномом."""
    lang = lang or clinical_kb_language(conn, profile)
    import health_db as _db
    if conn is None:
        conn = _db.get_conn()
    if profile is None:
        try:
            profile = _db.get_profile_context()
        except Exception:  # silent-ok: профиль недоступен → рамка по медкарте/геному, дефолт {}
            profile = {}

    frame = dict(energy="standard", protein="standard",
                 micro=set(), constraints=set(), reasons=[])

    def _apply(mut: dict, src: str):
        if mut.get("energy") == "gain" or (mut.get("energy") == "maintain" and frame["energy"] == "standard"):
            frame["energy"] = mut["energy"]
        if mut.get("protein") == "high":
            frame["protein"] = "high"
        frame["micro"] |= set(mut.get("micro", ()))
        frame["constraints"] |= set(mut.get("constraints", ()))
        if mut.get("why"):
            frame["reasons"].append(f"{src}: {mut['why']}")

    # med_text (проблем-лист + profile.medical) — для матча состояний И для генерируемых правил
    # (шаг 5). Дом — patient_med_text: тот же текст видит критик генератора.
    low = _ckb_med_text(conn, profile)
    bmi = _bmi(profile)

    # 1-3) КЛИНИЧЕСКОЕ ЗНАНИЕ ИЗ clinical_kb (brief-neutralization Фаза 2 B2.2): условие→рамка
    # (медкарта/ИМТ/геном) читается из версионируемого источника methodology/clinical_kb/food.yaml,
    # а НЕ из хардкод-CONDITION_RULES/_genome_flags в коде (§9: имя болезни/ген — данные, не код).
    # Гейт по ДАННЫМ тенанта (тот же med_text и bmi, что у medical_frame — паритет доказан 13/13,
    # включая suppress-when-gain). Провенанс reasons (медкарта/геном/профиль) — по типу триггера.
    try:
        import clinical_kb as _ckb
        _ttypes = _ckb.cond_trigger_type(conn, lang=lang)
        for _e in _ckb.active_entries(conn, "food", med_text=low, bmi=bmi):
            if _e.get("kind") == "frame_rule":
                payload = dict(_e["payload"])
                why_key = _FRAME_REASON_KEYS.get(_e["id"])
                # Only translate the known message. A changed tenant rule is not the old text.
                if why_key and payload.get("why") == i18n.t(why_key, "ru"):
                    payload["why"] = i18n.t(why_key, lang)
                else:
                    payload["why"] = i18n.pick(payload.get("why"), lang)
                _apply(payload, _ttypes.get(_e["condition_key"], i18n.t('food.source.data', lang)))
    except Exception as _fe:
        logging.getLogger(__name__).warning("clinical_kb frame_rules skipped: %s", _fe)

    # 4) ДОМИНАНТА: при gain/maintain снимаем генетические ограничения жира/порций (калории важнее).
    if frame["energy"] in ("gain", "maintain"):
        dropped = frame["constraints"] & _SUPPRESSED_WHEN_GAIN
        frame["constraints"] -= _SUPPRESSED_WHEN_GAIN
        if dropped:
            frame["reasons"].append(
                i18n.t('food.frame.dominance', lang, dropped=sorted(dropped)))

    # 5) data-in-code-9 #3: активные СГЕНЕРИРОВАННЫЕ правила поверх детерминированной рамки,
    # fail-closed по полу. Пустой склад (прод сейчас) → рамка не меняется.
    frame = _apply_active_generated(frame, conn, low)

    frame["bmi"] = bmi
    return frame


# ── P0/P1: каталог продуктов по категориям, аннотированный рамкой ──
_KIND_CAT = {0: "fruits", 1: "vegetables", 2: "seafood"}
_CAT_ORDER = ['fruits', 'vegetables', 'seafood', 'meat', 'eggs', 'dairy', 'pantry', 'fats']


def _annotate(item_tags: set, frame: dict, lang: str | None = None) -> str:
    """form_note по рамке (лактоза/калории/жир/пурины). Пустая строка если нечего сказать."""
    notes = []
    c = frame["constraints"]
    if "lactose" in item_tags and "lactose_free" in c:
        notes.append(i18n.t('food.note.lactose_free', lang))
    if "lactose_low" in item_tags and "lactose_free" in c:
        notes.append(i18n.t('food.note.lactose_low', lang))
    if frame["energy"] == "gain" and item_tags & {"calorie_dense", "unsat"}:
        notes.append(i18n.t('food.note.calories', lang))
    if "sat_fat" in item_tags and "sat_fat_limit" in c:
        notes.append(i18n.t('food.note.lean', lang))
    if "purine" in item_tags and "purine_limit" in c:
        notes.append(i18n.t('food.note.purines', lang))
    return "; ".join(notes)


def build_food_profile(conn=None, month: int = 1, profile: dict | None = None, *, lang: str | None = None) -> dict:
    """Детерминированный пищевой профиль тенанта: рамка + продукты по категориям + фокус +
    ограничить. Сезонное (фрукты/овощи/рыба) из seasonal_produce×геном, несезонное — staples.
    Materialized-view-on-read: всегда считается из текущих входов (refresh даром)."""
    lang = lang or clinical_kb_language(conn, profile)
    import health_db as _db
    import food_genome as _fg
    import food_staples as _fs
    if conn is None:
        conn = _db.get_conn()
    frame = medical_frame(conn, profile, lang=lang)

    try:
        genes = conn.execute(
            "SELECT gene, significance FROM genetic_variants WHERE gene LIKE 'MTHFR%'").fetchall()
    except Exception:  # silent-ok: нет генома → фолат-буст не применяется, профиль строится
        genes = []

    cats: dict[str, list] = {i18n.t(f"food.category.{c}", lang): [] for c in _CAT_ORDER}

    # Сезонное (фрукты/овощи/морепродукты). conn → онко-эмфаза гейтится по проблем-листу (clinical_kb).
    for it in _fg.beneficial_this_month(month, genes, conn=conn, lang=lang):
        cat_id = _KIND_CAT.get(it.get("kind", 1), "vegetables")
        cat = i18n.t(f"food.category.{cat_id}", lang)
        tags = {"protein", "omega3"} if cat_id == "seafood" else set()
        cats[cat].append({"item": it["food"], "why": it["why"], "form_note": _annotate(tags, frame, lang)})

    # Несезонные staples (мясо/яйца/молочное/бакалея/жиры/рыба-консервы)
    for s in _fs.staples_for_person(lang):
        cat = s["cat"][0].upper() + s["cat"][1:]
        cats.setdefault(cat, [])
        cats[cat].append({"item": s["item"], "why": s["why"], "form_note": _annotate(s["tags"], frame, lang)})

    # Клиническую оговорку добавляем только при соответствующем ограничении
    # в рамке текущего тенанта. Сам по себе микронутриент-фокус такого основания
    # не даёт: общий шаблон не должен приписывать человеку отсутствующий контекст.
    _resection = "dumping_aware" in frame["constraints"]
    micro = []
    micro_foods = _fs.micro_foods_for_person(lang)
    for m in ("B12", "iron", "folate", "D", "Ca"):
        if m in frame["micro"] and m in micro_foods:
            desc = micro_foods[m]
            if m == "B12" and _resection:
                desc += i18n.t('food.micro.resection_suffix', lang)
            micro.append((m, desc))

    # Ограничить (детерминированно из constraints + energy)
    limit: list[str] = []
    c = frame["constraints"]
    if "lactose_free" in c:
        limit.append(i18n.t('food.limit.lactose', lang))
    if "sat_fat_limit" in c:
        limit.append(i18n.t('food.limit.saturated_fat', lang))
    if "low_GI" in c:
        limit.append(i18n.t('food.limit.sugar', lang))
    if "purine_limit" in c:
        limit.append(i18n.t('food.limit.purines', lang))
    if "dumping_aware" in c:
        limit.append(i18n.t('food.limit.dumping', lang))
    if frame["energy"] in ("gain", "maintain"):
        limit.append(i18n.t('food.limit.volume', lang))
    limit.append(i18n.t('food.limit.alcohol', lang))

    # Пол безопасности (нить data-in-code-9, инкремент 1): независимый гвардиан на рамку.
    # НЕ фатально — детерминированный выход и так проходит; лог, если пол пробит. Станет
    # enforcing, когда появится генерация (тир 2). Гвардиан не должен ронять профиль.
    try:
        import food_floor as _ff
        import logging as _lg
        _viol = _ff.assert_floor(frame, conn=conn, profile=profile)
        if _viol:
            _lg.getLogger(__name__).warning(
                "food_floor: рамка нарушает пол безопасности: %s",
                "; ".join(f"{v['id']}({v['detail']})" for v in _viol))
    except Exception:  # silent-ok: гвардиан пола не должен ломать построение профиля
        pass

    # data-in-code-9 #репертуар (тир 4): «приготовь из своего» — блюдо из канала владельца, вкус-совместимое.
    from_rep = None
    try:
        import repertoire as _rep
        from_rep = _rep.suggest_set(seed=month, n=6, lang=lang)  # разнесённая подборка, ротация по месяцу
    except Exception as e:
        logging.getLogger(__name__).warning("repertoire suggest skipped: %s", e)

    return {"frame": frame, "month": month, "language": lang,
            "categories": {k: v for k, v in cats.items() if v},
            "micro": micro, "limit": limit, "from_repertoire": from_rep}


_ENERGY_LABEL_KEYS = {
    "gain": "food.energy.gain",
    "maintain": "food.energy.maintain",
    "standard": "food.energy.standard",
}


def render_food_document(name: str, model: dict) -> str:
    """ДЕТЕРМИНИРОВАННЫЙ рендер профиля в markdown (LLM тут НЕ решает — только данные).
    Каркас «не медназначение» обязателен; при gain — акцент на вес/диетолога."""
    lang = model.get("language", i18n.DEFAULT)
    f = model["frame"]
    energy_key = _ENERGY_LABEL_KEYS.get(f["energy"])
    L: list[str] = [i18n.t('food.document.title', lang, name=name), ""]
    L.append(i18n.t('food.document.intro', lang))
    L.append("")
    L.append(i18n.t('food.document.frame', lang,
                    energy=i18n.t(energy_key, lang) if energy_key else f["energy"])
             + (i18n.t('food.document.protein', lang) if f["protein"] == "high" else "")
             + (i18n.t('food.document.bmi', lang, bmi=f["bmi"]) if f.get("bmi") else ""))
    if f["reasons"]:
        L.append("")
        L.append(i18n.t('food.document.reasons', lang, reasons="; ".join(f["reasons"])))
    if model["micro"]:
        L.append("")
        L.append(i18n.t('food.document.micro', lang,
                        sources="; ".join(f"{m} — {src}" for m, src in model["micro"])))
    for cat, items in model["categories"].items():
        L.append("")
        L.append(f"## {cat}")
        for it in items:
            note = f" — _{it['form_note']}_" if it.get("form_note") else ""
            L.append(f"- **{it['item']}** — {it['why']}{note}")
    L.append("")
    L.append(i18n.t('food.document.limit_heading', lang))
    for x in model["limit"]:
        L.append(f"- {x}")
    if f["energy"] in ("gain", "maintain"):
        L.append("")
        L.append(i18n.t('food.document.weight_priority', lang))
    fr = model.get("from_repertoire")
    if fr:
        items = fr if isinstance(fr, list) else [fr]  # список подборки (или одиночное — совместимость)
        L.append("")
        L.append(i18n.t('food.document.repertoire_heading', lang))
        for it in items:
            L.append(f"- **{it['title']}** — {it['why']}")
    return "\n".join(L)


# ── P3: продукт дня для брифа — сезонное + несезонные staples, рамка-осознанно ──
_BRIEF_PATTERN = ("fruit", "veg", "staple", "fruit", "sea", "fruit")  # фрукты 3/6, овощ/рыба/staple по 1


def pick_food_of_day(conn=None, month: int = 1, day_index: int = 0, profile: dict | None = None, *, lang: str | None = None) -> dict | None:
    """Продукт дня для брифа: сезонное (фрукты/овощи/рыба) + несезонные staples (мясо/яйца/
    бакалея) для вариативности, с form_note по рамке. Ротация по дате."""
    lang = lang or clinical_kb_language(conn, profile)
    import health_db as _db
    import food_genome as _fg
    import food_staples as _fs
    if conn is None:
        conn = _db.get_conn()
    frame = medical_frame(conn, profile, lang=lang)
    try:
        genes = conn.execute(
            "SELECT gene, significance FROM genetic_variants WHERE gene LIKE 'MTHFR%'").fetchall()
    except Exception:  # silent-ok: нет генома → без фолат-буста
        genes = []
    seasonal = _fg.beneficial_this_month(month, genes, conn=conn, lang=lang)
    fruits = [i for i in seasonal if i["kind"] == 0]
    veg = [i for i in seasonal if i["kind"] == 1]
    sea = [i for i in seasonal if i["kind"] == 2]
    staples = [{"food": s["item"], "food_alias": raw["item"], "tag": raw["cat"],
                "why": s["why"], "_tags": s["tags"]}
               for s, raw in zip(_fs.staples_for_person(lang), _fs.STAPLES)
               if raw["cat"] in ("мясо", "яйца", "бакалея", "молочное")]
    cat = _BRIEF_PATTERN[day_index % len(_BRIEF_PATTERN)]
    pool = {"fruit": fruits, "veg": veg, "sea": sea, "staple": staples}[cat] or seasonal or staples
    if not pool:
        return None
    # data-in-code-9 #вкус (тир 3): убрать невкусное (hard_avoid/disliked), любимое — вперёд.
    # Всё отфильтровалось → неотфильтрованный пул (не отдаём None). Сбой → лог, не silent.
    try:
        import taste as _taste
        _filt = sorted([p for p in pool if _taste.acceptable(p.get("food_alias", p.get("food", "")))],
                       key=lambda p: _taste.rank_key(p.get("food_alias", p.get("food", ""))))
        pool = _filt or pool
    except Exception as e:
        logging.getLogger(__name__).warning("taste filter (pick) skipped: %s", e)
    it = dict(pool[day_index % len(pool)])
    note = _annotate(it.get("_tags", set()), frame, lang)
    tnote = ""
    try:
        import taste as _taste
        tnote = i18n.pick(_taste.taste_note(it.get("food_alias", it.get("food", ""))), lang)
    except Exception as e:
        logging.getLogger(__name__).warning("taste note (pick) skipped: %s", e)
    combined = "; ".join(x for x in (note, tnote) if x)
    if combined:
        it["why"] = f"{it['why']} ({combined})"
    it.pop("_tags", None)
    it.setdefault("tag", "food")
    return it
