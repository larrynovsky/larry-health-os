"""
brief_cards.py — Ф2 анти-повтора утреннего брифа.

Детерминированные адаптеры существующих провайдеров GP-контекста (drift, safety_net,
genome, hypotheses) → унифицированные Card с semantic_key на уровне НАХОДКИ. LLM здесь
НЕ участвует (N12): ключ присваивается кодом до модели.

ПОДКЛЮЧЁН: карточки идут в gate (brief_gate/brief_state) за флагом MORNING_BRIEF_GATE
(вкл. в проде оба тенанта, 2026-07). Адаптеры работают с СЫРЫМИ структурами провайдеров
(дикты/строки БД), а не с уже отформатированным текстом — так надёжнее (не парсим свой
же вывод).

Границы claim-scope:
- Структурные провайдеры (drift/safety/genome) → точные allowed_claims (числа/факты).
- Гипотезы (свободный клинический текст) → claim_scope='passthrough', allowed_claims=None:
  их утверждения НЕ декомпозируются валидатором. Это медицинский контент (врачи/CBCR),
  а не арифметико-провенансный пол.
"""
from __future__ import annotations

from dataclasses import dataclass, field

_LANES = ("safety", "routine")
_SCOPES = ("structured", "passthrough")

_SEVERITY_NUM = {
    "mild": 0.3, "moderate": 0.6, "severe": 0.9,
    "info": 0.1, "warn": 0.5, "urgent": 0.8, "critical": 1.0,
}


@dataclass
class Card:
    """То, что ЭМИТИТ провайдер. FSM/статусные поля (status, recurrence_state,
    gate_reason, cooldown_until) заполняет gate (Ф3), не провайдер."""
    provider: str
    semantic_key: str
    lane: str
    origin: str = "internal"
    delivery: str = "computed"
    relevance: float | None = None
    importance: float | None = None
    severity: float | None = None
    evidence_summary: str = ""
    allowed_claims: list[str] | None = None
    forbidden_claims: list[str] = field(default_factory=list)
    last_value: str | None = None
    claim_scope: str = "structured"
    # Гены (латиница, verbatim), которые НЕСЁТ карточка — для leak-детекта валидатора.
    # Заполняют провайдеры, чей контент содержит имя гена: from_genome (сам ген) и
    # from_sleep_deep (склеенный sleep-геном). Подавлена карточка → ни один
    # из этих генов не должен всплыть в тексте (риск Ф0: LLM конфабулирует из промпта).
    genome_terms: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.lane not in _LANES:
            raise ValueError(f"lane={self.lane!r} не в {_LANES}")
        if self.claim_scope not in _SCOPES:
            raise ValueError(f"claim_scope={self.claim_scope!r} не в {_SCOPES}")


def _sev(level: str | None) -> float | None:
    return _SEVERITY_NUM.get(str(level).lower()) if level else None


# ── Адаптеры (сырая структура провайдера → Card) ────────────────────────────

def from_drift(d: dict) -> Card:
    """hai.detect_metric_drift() → Card. Ключ = drift:<metric>:<direction>."""
    metric = str(d.get("metric", "?")).lower()
    direction = str(d.get("direction", "?")).lower()
    # BL-DATA-PARITY-1: новые колонки дрейфа — под человеческой подписью; пять исходных
    # (deep_min, hrv_ms…) в словаре колонок не значатся и остаются как были.
    import hai_analysis
    import metrics_db
    name = (metric if metric in hai_analysis.DRIFT_LEGACY_METRICS
            else metrics_db.METRIC_LABELS.get(metric, (metric,))[0])
    claim = (f"{name} {direction} {d.get('delta_pct')}% за {d.get('streak_days')}д "
             f"(7д={d.get('current_7d')} vs 30д={d.get('baseline_30d')})")
    return Card(
        provider="drift",
        semantic_key=f"drift:{metric}:{direction}",
        lane="routine",
        severity=_sev(d.get("severity")),
        last_value=str(d.get("current_7d")),
        evidence_summary=claim,
        allowed_claims=[claim],
    )


def from_safety(a: dict) -> Card:
    """safety_net.run_safety_net()['alerts'][i] → Card (lane=safety)."""
    metric = str(a.get("metric", "?"))
    direction = str(a.get("direction", "?")).lower()
    unit = a.get("unit") or ""
    note = a.get("note") or ""
    claim = f"{metric} {a.get('value')}{unit} {direction}"
    if note:
        claim += f" ({note})"
    # origin по источнику алерта: лаборатория — внешний документ,
    # носимые — внутренний ежедневный замер. Исчезновение лабораторной карточки
    # без нового результата не доказывает возвращения показателя к норме.
    # Fail-closed: внутренним считается ТОЛЬКО явный носимый источник; неизвестный или
    # пустой source — внешний (молчание про «прошло» дешевле ложного отбоя).
    origin = "internal" if a.get("source") == "lifestyle" else "personal_external"
    return Card(
        provider="safety_net",
        semantic_key=f"safety:{metric.lower()}:{direction}",
        lane="safety",
        origin=origin,
        severity=_sev(a.get("level")),
        last_value=str(a.get("value")),
        evidence_summary=claim,
        allowed_claims=[claim],
    )


def from_genome(v: dict, domain: str) -> Card:
    """Строка genetic_variants → Card (modifier, lane=routine, origin=personal_external).
    effect_allele не верифицирован (palindromic/пусто) → forbidden: не утверждать направление."""
    gene = str(v.get("gene", "?"))
    genotype = v.get("genotype") or "?"
    sig = v.get("significance") or ""
    summary = v.get("clinical_summary") or ""
    claim = f"{gene} {genotype} {sig}: {summary}".strip()
    forbidden = []
    if str(v.get("effect_allele_status") or "").lower() in ("palindromic", "", "none"):
        forbidden.append("не утверждать направление эффекта как верифицированное "
                         "(effect_allele не подтверждён)")
    strong = "Pathogenic" in sig and "Likely" not in sig
    return Card(
        provider="genome",
        semantic_key=f"genome:{domain}:{gene.lower()}",
        lane="routine",
        origin="personal_external",
        severity=_sev("severe" if strong else "moderate"),
        last_value=str(genotype),
        evidence_summary=claim,
        allowed_claims=[claim],
        forbidden_claims=forbidden,
        genome_terms=[gene] if gene and gene != "?" else [],
    )


def from_calendar(e: dict) -> Card:
    """Ближайшая поездка из Google Calendar → карточка «за день до» (второй этап).
    e = {title, location, date_raw, is_travel} из calendar_client.get_travel_events.
    date_raw начинается с today/tomorrow/day after → человекочитаемое «когда»."""
    dr = (e.get("date_raw") or "")
    when = ("сегодня" if dr.startswith("today")
            else "завтра" if dr.startswith("tomorrow")
            else "послезавтра" if dr.startswith("day after")
            else dr)
    where = (e.get("location") or e.get("title") or "поездка").strip()
    slug = "".join(ch if ch.isalnum() else "_" for ch in where.lower())[:24].strip("_")
    claim = f"{when}: поездка — {where}"
    return Card(
        provider="calendar",
        semantic_key=f"calendar:travel:{slug}",
        lane="routine",
        origin="personal_external",
        importance=0.7,
        severity=0.5,           # выше θ гейта — поездка попадает в FSM (кулдаун на место)
        evidence_summary=claim,
        allowed_claims=[claim],
    )


def from_trail(trail: dict) -> Card:
    """Тропа (S4, семейная активность) → карта на выходной день дома. Кулдаун per-тропа
    (gate) не даёт повтора → ротация по списку."""
    name = trail.get("name", "тропа")
    km = trail.get("km")
    note = (trail.get("note") or "").strip()
    extra = f" (~{km} км)" if km else (f" — {note}" if note else "")
    claim = f"выходной — тропа: {name}{extra}"
    slug = "".join(ch if ch.isalnum() else "_" for ch in name.lower())[:28].strip("_")
    return Card(provider="trail", semantic_key=f"movement:trail:{slug}",
        lane="routine", origin="curated", importance=0.5, severity=0.4,
        evidence_summary=claim, allowed_claims=[claim])


def from_season_food(item: dict) -> Card:
    """Сезон×геном (S3): продукт в сезоне И полезный тенанту → карта. item из
    food_genome.beneficial_this_month. Кулдаун per-продукт (tag) даёт ротацию ~месяц."""
    food = item.get("food", "продукт")
    tag = item.get("tag", "food")
    why = item.get("why", "")
    claim = f"в сезоне и полезно: {food} — {why}"
    return Card(
        provider="food",
        semantic_key=f"food:seasonal:{tag}:{''.join(ch if ch.isalnum() else '_' for ch in food.lower())[:16]}",
        lane="routine", origin="curated",
        importance=0.6 if item.get("boosted") else 0.5,
        severity=0.45 if item.get("boosted") else 0.4,   # boosted (MTHFR-фолат) чуть важнее
        evidence_summary=claim, allowed_claims=[claim])


def from_hypothesis(h: dict) -> Card:
    """hai.get_open_hypotheses()[i] → pass-through Card. НЕ декомпозируем утверждения
    (медицинский текст, домен врачей/CBCR)."""
    return Card(
        provider="hypotheses",
        semantic_key=f"hypothesis:{h.get('memory_id')}",
        lane="routine",
        last_value=h.get("status"),
        evidence_summary=(h.get("observation") or "")[:160],
        allowed_claims=None,
        claim_scope="passthrough",
    )


# ── Cross-provider дедуп (риск #6) ──────────────────────────────────────────
# Разные провайдеры про ОДНУ находку → один канонический ключ. Синонимы явные и
# детерминированные (N12), не эвристика. Расширять по мере подключения провайдеров.
_SYNONYM = {
    "drift:deep:down": "sleep:deep:below_band",
    "drift:deep_sleep:down": "sleep:deep:below_band",
}


def canonical_key(semantic_key: str) -> str:
    return _SYNONYM.get(semantic_key, semantic_key)


def dedup(cards: list[Card]) -> list[Card]:
    """Схлопывает карточки одного канонического смысла; первая выигрывает
    (порядок = приоритет вызывающего). Гасит «глубокий мало» дважды.

    genome_terms СЛИВАЮТСЯ через все схлопнутые карты: иначе dedup мог выбросить
    карту-носитель гена (напр. drift:deep:down канонизируется в sleep:deep:below_band
    и идёт раньше карты sleep_agent с геном сна → ген терялся, leak-сторож слеп)."""
    seen: dict[str, Card] = {}
    for c in cards:
        k = canonical_key(c.semantic_key)
        if k in seen:
            merged = list(dict.fromkeys(
                (seen[k].genome_terms or []) + (c.genome_terms or [])))
            seen[k].genome_terms = merged
            continue
        seen[k] = c
    return list(seen.values())


def from_sleep_deep(deep_today_min: int, band: dict, genome_mods=None) -> Card:
    """Хронический глубокий сон ниже нормы. Находка sleep-агента в тот же FSM/кулдаун
    (месячный) — гасит ежедневную «устойчивую недельную картину». band извне
    (brief_gate.band_position). genome_mods — sleep-геном СКЛЕЕН сюда как
    пояснение (один кулдаун): подавлена находка → подавлен и геном-довесок."""
    z = band.get("z")
    sev = 0.7 if (isinstance(z, (int, float)) and z <= -1.5) else 0.5
    claim = f"глубокий сон {deep_today_min}м — ниже личной полосы (p10≈{band.get('p_low')})"
    allowed = [claim]
    ev = claim
    terms: list[str] = []
    if genome_mods:
        joined = ", ".join(genome_mods)
        ev = f"{claim}; вероятно генетически обусловлено ({joined})"
        allowed.append(f"генетический модификатор глубокого сна: {joined}")
        # Имя гена = первый токен каждого мода ("GENE AA Pathogenic" → "GENE").
        # dict.fromkeys дедуплицирует (два варианта одного гена → одно имя; например
        # ['GENE_A','GENE_A','GENE_B'] → ['GENE_A','GENE_B']). Подавлена карточка sleep:deep →
        # эти гены не должны просочиться в текст.
        terms = list(dict.fromkeys(m.split()[0] for m in genome_mods if m and m.split()))
    return Card(provider="sleep_agent", semantic_key="sleep:deep:below_band", lane="routine",
                severity=sev, last_value=str(deep_today_min), evidence_summary=ev,
                allowed_claims=allowed, genome_terms=terms)


def from_recovery(composite: float, band: dict) -> "Card | None":
    """Индекс восстановления ниже ЛИЧНОЙ trailing-полосы композита → карточка.
    Гейт по личной полосе (band извне, brief_gate.band_position), а не по абсолюту:
    устойчивое смещение базового уровня не должно давать ежедневную тревогу.
    band != 'below' → None (не находка).
    severity дискретна (0.5, глубже полосы 0.7 при z≤−1.5) — как у sleep-агента.
    Категория 'pulse' (см. PROVIDER_CATEGORY): общий слот с drift/sleep обеспечивает
    дедупликацию. Если drift молчит, композит может показать диффузное проседание."""
    if band.get("band") != "below":
        return None
    z = band.get("z")
    sev = 0.7 if (isinstance(z, (int, float)) and z <= -1.5) else 0.5
    claim = (f"индекс восстановления {composite:.0f}% от доболезненного baseline — "
             f"ниже личной полосы (p10≈{band.get('p_low')})")
    return Card(provider="recovery", semantic_key="recovery:composite:below_band",
                lane="routine", severity=sev, last_value=f"{composite:.0f}%",
                evidence_summary=claim, allowed_claims=[claim])
