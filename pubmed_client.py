#!/usr/bin/env python3.11
"""
PubMed client через NCBI E-utilities REST API.
Бесплатный, без ключа. Ищет релевантные исследования по обнаруженным паттернам.
"""

from _time_inject import get_today  # seam
import logging
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import re
import requests

log = logging.getLogger(__name__)

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
TOOL        = "health_copilot"
EMAIL       = "health-os@example.invalid"   # контакт для NCBI; прежний тоже был недоставляемым, но нёс ник владельца (pii-scrub 2026-09-23)


def search_pubmed(query: str, max_results: int = 5, years_back: int = 5,
                  strict: bool = False) -> list[dict]:
    """
    Ищет статьи в PubMed. Возвращает список {pmid, title, abstract, year, journal}.

    strict=True: сбой источника бросается, а не превращается в []. Без этого «NCBI лежит»
    и «новых статей нет» неотличимы — 27.09 первый прогон крана партнёра сохранил отчёт
    «0 новых» при ответах 500 на все темы (pubmed_searcher зовёт strict).
    """
    from datetime import date
    min_year = get_today().year - years_back

    try:
        # Шаг 1: получить PMID
        search_resp = requests.get(ESEARCH_URL, params={
            "db":       "pubmed",
            "term":     f"{query} AND {min_year}:{get_today().year}[pdat]",
            "retmax":   max_results,
            "retmode":  "json",
            "sort":     "relevance",
            "tool":     TOOL,
            "email":    EMAIL,
        }, timeout=10)
        search_resp.raise_for_status()
        pmids = search_resp.json().get("esearchresult", {}).get("idlist", [])

        if not pmids:
            return []

        time.sleep(0.35)  # NCBI rate limit: ~3 req/s

        # Шаг 2: получить абстракты
        fetch_resp = requests.get(EFETCH_URL, params={
            "db":      "pubmed",
            "id":      ",".join(pmids),
            "rettype": "abstract",
            "retmode": "xml",
            "tool":    TOOL,
            "email":   EMAIL,
        }, timeout=15)
        fetch_resp.raise_for_status()

        return _parse_pubmed_xml(fetch_resp.text)

    except Exception as e:
        log.warning(f"PubMed search '{query}': {e}")
        if strict:
            raise
        return []


def _parse_pubmed_xml(xml_text: str) -> list[dict]:
    """Парсит XML-ответ PubMed в список статей."""
    results = []
    try:
        root = ET.fromstring(xml_text)
        for article in root.findall(".//PubmedArticle"):
            pmid_el    = article.find(".//PMID")
            title_el   = article.find(".//ArticleTitle")
            journal_el = article.find(".//Journal/Title")
            year_el    = article.find(".//PubDate/Year")
            abstract_el = article.find(".//AbstractText")

            pmid    = pmid_el.text    if pmid_el    is not None else ""
            title   = title_el.text   if title_el   is not None else ""
            journal = journal_el.text if journal_el is not None else ""
            year    = year_el.text    if year_el    is not None else ""
            abstract = ""
            if abstract_el is not None:
                abstract = "".join(abstract_el.itertext())

            if title:
                results.append({
                    "pmid":     pmid,
                    "title":    title,
                    "abstract": abstract[:600] if abstract else "",
                    "year":     year,
                    "journal":  journal,
                    "url":      f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                })
    except Exception as e:
        log.warning(f"PubMed XML parse error: {e}")
    return results


# ── Нейтрализованные запросы: класс состояния из данных, не зашитый диагноз ──
# Нить diagnosis-hardcode этап B (B1-B3). Раньше PATTERN_QUERIES зашивал диагноз+лечение
# владельца (препарат, гистология, маркер с годом) и слал его строкой в NCBI (PubMed) —
# PHI-egress третьей стороне.
# Теперь: физиологический паттерн → НЕЙТРАЛЬНЫЙ базовый запрос; класс состояния тенанта
# (clinical_kb.active_conditions по ЕГО problem_list) добавляет нейтральный контекст;
# egress-guard режет сырую специфику (номера/даты/имена/конкретный препарат/гистологию).

# Физиологический паттерн → нейтральный базовый PubMed-запрос (0 онко-литералов).
PATTERN_TEMPLATES = {
    "low_hrv":                    "heart rate variability autonomic recovery",
    "poor_deep_sleep":            "sleep quality deep sleep NREM",
    "low_deep_sleep_after_chemo": "sleep architecture slow wave sleep recovery",
    "hrv_decline":                "autonomic nervous system long-term effects",
    "ferritin_low":               "iron deficiency ferritin repletion",
    "weight_loss":                "nutritional status weight surveillance",
    "albumin_borderline":         "albumin nutritional support outcomes",
    "vagal_stimulation_sleep":    "vagus nerve stimulation sleep quality deep sleep insomnia",
    "cipn":                       "peripheral neuropathy long-term recovery treatment",
    "readiness_low":              "recovery wearable HRV readiness score",
    "glucose_high_normal":        "glucose insulin resistance metabolic effects",
    "cea_trend":                  "tumor marker surveillance remission",
}

# Класс состояния (clinical_kb condition_id) → НЕЙТРАЛЬНЫЙ литературный термин.
# Класс, НЕ сырой диагноз владельца. LLM-ген (не ручная MeSH) → планка приёмки ниже (R6).
CONDITION_LIT_TERMS = {
    "oncology":              "cancer survivorship",
    "iron_deficiency":       "iron deficiency",
    "b12_folate_deficiency": "cobalamin folate deficiency",  # 'cobalamin' не 'B12': цифра режется egress_safe(\d)
    "dysglycemia":           "glucose metabolism insulin resistance",
    "nutritional_risk":      "nutritional status weight loss",
    "hyperuricemia":         "uric acid gout",
}
# Классы, заведённые под тенанта, приносят свой термин в своде (clinical_kb condition_attr("lit_term")).
try:
    import clinical_kb as _ckb
    CONDITION_LIT_TERMS.update(_ckb.condition_attr("lit_term"))
except Exception as _e:
    log.warning(f"clinical_kb lit_term недоступны: {_e}")

# CONDITION_FLOOR_TERMS — расширенный маппинг ТОЛЬКО для еженедельного floor-sweep
# (pubmed_searcher). Клинические классы ∪ геномные/образ-жизни маркеры. Почему ОТДЕЛЬНО от
# CONDITION_LIT_TERMS: floor даёт по ОДНОЙ автономной теме на состояние (не AND), поэтому геном
# здесь безопасен; а CONDITION_LIT_TERMS клеится _compose_query'ем через AND к КАЖДОМУ паттерну —
# добавь туда геном, и реальный-тайм запросы сузятся до нуля. Все термины digit-free (egress_safe \d).
CONDITION_FLOOR_TERMS = {
    **CONDITION_LIT_TERMS,
    "low_bmi":               "underweight nutritional status",
}
# Геномные/образ-жизни маркеры приносят свой floor-термин из свода (clinical_kb condition_attr
# "floor_term"), где они и заведены. До 27.09 их список стоял здесь литералом — набор маркеров
# и был чьим-то геномным профилем (BL-PUB-16 в). Термины обязаны быть digit-free (egress_safe).
try:
    CONDITION_FLOOR_TERMS.update(_ckb.condition_attr("floor_term"))
except Exception as _e:  # noqa: BLE001 — свода нет → только клинические классы
    log.warning(f"clinical_kb floor_term недоступны: {_e}")

# Паттерны, осмысленные ТОЛЬКО при онко-классе (иначе не детектим — не навязываем всем).
_ONCOLOGY_GATED_PATTERNS = {"cipn", "cea_trend", "low_deep_sleep_after_chemo"}

# egress-guard: сырые специфики, которые НИКОГДА не должны уйти в NCBI (диагноз/лечение/PII).
# Класс-термины (cancer survivorship, tumor marker) — разрешены; конкретика — режется.
# Общие правила — здесь; ЛИЧНЫЕ слова (фамилия, врачи, диагноз и режим владельца, город) —
# в словаре pii_census (приватная зона), читаются в egress_safe. До 2026-09-23 личные слова
# стояли в этом списке литералами — то есть сам сторож был утечкой в открытый репозиторий.
_EGRESS_BLOCKLIST = [
    r"\d",                                    # любые цифры: даты, значения, ID
    # конкретный противоопухолевый препарат — по основам МНН ВОЗ (не по списку чьего-то лечения)
    r"platin\b|tabine\b|taxel\b|rubicin\b|mab\b|nib\b|uracil\b|tecan\b|5-?fu\b|mustine\b|poside\b",
    r"carcinoma|sarcoma|lymphoma|leuka?emia|melanoma|glioma|ectom(?:y|ies)\b",  # гистология/операция
]


def _personal_pattern():
    """Личные слова тенанта из словаря pii_census (все классы). Нет словаря → None:
    остаются общие правила, WARNING пишет сам pii_census."""
    import pii_census
    return pii_census.pattern()


def egress_safe(query: str) -> bool:
    """True если запрос НЕ содержит сырой специфики (можно слать в NCBI).

    Разрешает класс-уровень (cancer survivorship), режет конкретику (препарат/гистология/
    номера/имена). Страхует от регрессии: если сырой термин просочился в запрос — блок."""
    low = query.lower()
    for pat in _EGRESS_BLOCKLIST:
        if re.search(pat, low):
            return False
    personal = _personal_pattern()
    if personal is not None and personal.search(query):
        return False
    return True


def _compose_query(pattern: str, active_conds: set) -> Optional[str]:
    """Строит запрос: нейтральный шаблон + класс-контекст тенанта, затем egress-guard.

    Q4 (реш. владельца): блок → лог → fallback на чистый шаблон → если и он не прошёл, None.
    active_conds — из clinical_kb.active_conditions(conn) СМОТРЯЩЕГО тенанта."""
    base = PATTERN_TEMPLATES.get(pattern)
    if not base:
        return None
    ctx = " ".join(CONDITION_LIT_TERMS[c] for c in sorted(active_conds)
                   if c in CONDITION_LIT_TERMS)
    composed = f"{base} {ctx}".strip() if ctx else base
    if egress_safe(composed):
        return composed
    log.warning("egress-guard заблокировал запрос '%s' — fallback на чистый шаблон", pattern)
    if egress_safe(base):
        return base
    log.warning("egress-guard заблокировал даже базовый шаблон '%s' — пропуск", pattern)
    return None



def get_evidence_for_patterns(detected_patterns: list[str], active_conds: set | None = None) -> dict:
    """{паттерн: [статьи]}. Запрос строится из нейтрального шаблона + класса тенанта.

    active_conds — множество condition_id из clinical_kb.active_conditions(conn). None →
    пустое (только нейтральный шаблон, без класс-контекста — безопасный дефолт)."""
    active_conds = active_conds or set()
    evidence = {}
    for pattern in detected_patterns:
        query = _compose_query(pattern, active_conds)
        if not query:
            continue
        log.info("PubMed: поиск по паттерну '%s'", pattern)
        articles = search_pubmed(query, max_results=3, years_back=8)
        if articles:
            evidence[pattern] = articles
        time.sleep(0.5)
    return evidence


def format_evidence_block(evidence: dict, max_per_pattern: int = 2) -> str:
    """Форматирует найденные статьи для подстановки в промпт."""
    if not evidence:
        return ""
    lines = ["\n=== ДОКАЗАТЕЛЬНАЯ БАЗА (PubMed) ==="]
    for pattern, articles in evidence.items():
        lines.append(f"\nПо паттерну [{pattern}]:")
        for art in articles[:max_per_pattern]:
            lines.append(f"  \u2022 {art['title']} ({art['year']}, {art['journal']})")
            if art["abstract"]:
                lines.append(f"    {art['abstract'][:250]}...")
            lines.append(f"    PMID: {art['pmid']} \u2014 {art['url']}")
    return "\n".join(lines)


# ── Автоопределение паттернов из данных ───────────────────────────────────

def _tenant_floors() -> dict:
    """{metric: личный пол} для hrv / sleep_deep (часы) / readiness из absolute_thresholds тенанта."""
    out = {}
    try:
        import rules_db
        for m in ("hrv", "sleep_deep", "readiness"):
            try:
                out[m] = float(rules_db.get_threshold(m, "floor"))
            except KeyError:
                continue
    except Exception as e:  # noqa: BLE001 — нет БД → паттерны по полам не судим, остальное живёт
        log.warning(f"pubmed_client: личные пороги недоступны: {e}")
    return out


def detect_patterns_from_stats(stats7: dict, stats30: dict, stats90: dict,
                                recent_labs: list, active_conds: set | None = None) -> list[str]:
    """Детерминированно определяет паттерны из статистики.

    active_conds (из clinical_kb.active_conditions) гейтит онко-осмысленные паттерны:
    cipn/cea_trend/… детектятся ТОЛЬКО если у тенанта активен онко-класс. Раньше 'cipn'
    добавлялся ВСЕМ безусловно (зашитое онко-допущение)."""
    active_conds = active_conds or set()
    patterns = []

    hrv7  = stats7.get("avg_hrv")
    hrv90 = stats90.get("avg_hrv")
    deep7 = stats7.get("avg_deep")

    # Нижние границы — пороги текущего тенанта (absolute_thresholds, p10 его ряда).
    # Нет порога → паттерн не судим; общий литерал не заменяет личный ряд.
    floor = _tenant_floors()
    if hrv7 and floor.get("hrv") and hrv7 < floor["hrv"]:
        patterns.append("low_hrv")
    if hrv7 and hrv90 and hrv7 < hrv90 * 0.85:
        patterns.append("hrv_decline")
    if deep7 and floor.get("sleep_deep") and deep7 < floor["sleep_deep"]:
        patterns.append("poor_deep_sleep")
        if "oncology" in active_conds:
            patterns.append("low_deep_sleep_after_chemo")

    readiness7 = stats7.get("avg_readiness")
    if readiness7 and floor.get("readiness") and readiness7 < floor["readiness"]:
        patterns.append("readiness_low")

    lab_dict = {l["test_name"]: l["value"] for l in recent_labs if l.get("value")}
    if lab_dict.get("Ferritin") and lab_dict["Ferritin"] < 50:
        patterns.append("ferritin_low")
    if lab_dict.get("Albumin") and lab_dict["Albumin"] < 4.0:
        patterns.append("albumin_borderline")
    if lab_dict.get("Glucose") and lab_dict["Glucose"] > 95:
        patterns.append("glucose_high_normal")
    if lab_dict.get("CEA") and "oncology" in active_conds:
        patterns.append("cea_trend")

    # Онко-специфичные контекстные паттерны — только при активном онко-классе тенанта.
    if "oncology" in active_conds:
        patterns.append("cipn")

    return list(dict.fromkeys(patterns))  # dedup, preserve order
