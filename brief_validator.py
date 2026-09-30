"""
brief_validator.py — Ф4: пост-рендер claim-validator.

Ловит КОНКРЕТНОЕ и проверяемое в финальном тексте LLM против решений гейта:

  1. leak: подавленный геном-ген (Latin, verbatim — PER3/MTHFR/CLOCK/COMT…) не должен
     появиться в тексте. Это ГЛАВНЫЙ риск из Ф0: промпт велит «глубокий низкий →
     упомяни генетический модификатор», и LLM может втащить ген обратно, даже когда
     карточка геном подавлена гейтом. Гены латиницей → проверяемы подстрокой.

     ВАЖНО (2026-07-14): ген берём из card.genome_terms, НЕ из provider=="genome".
     Для sleep-домена ген сна НЕ отдельная genome-карточка — он склеен в карточку
     sleep_agent (sleep:deep:below_band) через genome_mods. Фильтр по провайдеру
     пропускал ровно этот путь (ген сна внутри sleep-карты). genome_terms несёт имена генов от
     ЛЮБОГО провайдера (from_genome + from_sleep_deep) → сторож видит реальный путь.
  2. forbidden: если показанная карточка несёт forbidden-фразу verbatim — её быть не должно.

Гипотезы (claim_scope='passthrough') на содержимое НЕ проверяются — это медицинский
текст (домен врачей/CBCR), не арифметико-провенансный пол.

ЧЕСТНАЯ ГРАНИЦА: это НЕ полная проверка «LLM не ввёл никаких новых утверждений». Та
требует structured-output от самой модели (список заявленных claim'ов) — отдельный
кусок при флипе. Здесь — проверяемое ядро: утечка подавленного гена + verbatim-forbidden.
"""
from __future__ import annotations

import re


def validate(rendered_text: str, shown_cards: list, suppressed_cards: list) -> dict:
    """Возвращает {ok, leaks, forbidden_hits}. ok=False → рендер отвергнуть/regen."""
    rlow = (rendered_text or "").lower()

    leaks = []
    for c in suppressed_cards:
        # Гены, которые несёт карточка (from_genome + склеенный sleep-геном).
        # Fallback на старую эвристику по ключу — на случай карточки без genome_terms,
        # но с provider=="genome" (обратная совместимость).
        terms = list(getattr(c, "genome_terms", None) or [])
        if not terms and getattr(c, "provider", "") == "genome":
            terms = [c.semantic_key.split(":")[-1]]
        for gene in terms:
            g = (gene or "").strip().lower()
            # Граница слова, не голая подстрока: снижает FP на коротких токенах
            # (напр. ген CLOCK vs англ. «clock» внутри слова). Полное устранение FP
            # для генов-словарных-слов — при эскалации до скраба (список известных генов).
            if g and re.search(rf"\b{re.escape(g)}\b", rlow):
                leaks.append({"key": c.semantic_key, "gene": gene})

    forbidden_hits = []
    for c in shown_cards:
        for f in (getattr(c, "forbidden_claims", None) or []):
            frag = f.strip().lower()
            if frag and frag in rlow:
                forbidden_hits.append({"key": c.semantic_key, "forbidden": f})

    return {"ok": not leaks and not forbidden_hits,
            "leaks": leaks, "forbidden_hits": forbidden_hits}
