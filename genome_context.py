import llm_client
#!/usr/bin/env python3.11
"""
genome_context.py
Формирует генетический контекст для GP и специалистов.
Также обрабатывает ad-hoc trait запросы через GWAS Catalog.
"""
import sys
import hai_core
import json
import time
import logging
import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import health_db as db


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

GWAS_CATALOG_URL = "https://www.ebi.ac.uk/gwas/rest/api/efoTraits/search"
GWAS_ASSOCIATIONS_URL = "https://www.ebi.ac.uk/gwas/rest/api/associations/search/findByEfoTrait"


def _zygosity(genotype: str, effect_allele: str) -> tuple[str, str]:
    """
    Возвращает (status, icon):
      "homo_risk"    ⚠️  — гомозигота по патогенному аллелю (effect_allele известен)
      "hetero"       ⚡  — гетерозигота (один патогенный аллель, effect_allele известен)
      "wildtype"     ✓   — оба аллеля нормальные (нет патогенного, effect_allele известен)
      "homo_unknown" ?h  — гомозигота, но effect_allele NULL → нельзя сказать несёт или нет
      "hetero_unknown" ?± — гетерозигота, но effect_allele NULL → нельзя верифицировать
      "unknown"      ?   — генотип непарсируемый или effect_allele слишком длинный
    """
    gt = genotype.upper().replace("/", "").replace(" ", "") if genotype else ""
    bad_gt = not gt or gt in ("--", "II", "DI", "DD")

    if not effect_allele or len(effect_allele) > 2:
        # effect_allele неизвестен — применяем гетеро/гомо-эвристику
        if bad_gt or len(gt) != 2:
            return "unknown", "?"
        if gt[0] == gt[1]:
            return "homo_unknown", "?h"   # гомозигота, статус носительства не верифицирован
        else:
            return "hetero_unknown", "?±"  # гетерозигота, статус носительства не верифицирован

    if bad_gt:
        return "unknown", "?"

    ea = effect_allele.upper()
    count = gt.count(ea)
    if count == 0:
        return "wildtype", "✓"
    elif count == 1:
        return "hetero", "⚡"
    else:
        return "homo_risk", "⚠️"


GENOME_QA_SYSTEM = """Ты — GP (главный врач) персональной health системы.
Пользователь задаёт вопрос о своих генетических предрасположенностях.
Отвечай на основе предоставленных данных генома.

Правила:
- Различай гомозиготу (AA/GG/CC/TT) и гетерозиготу (AG/CT/...) — это критически важно
- GWAS-данные = вероятностные риски, не диагнозы. Всегда указывай OR (odds ratio) если есть
- Если вариант не найден в геноме — скажи прямо
- Если данных недостаточно для ответа — скажи прямо
- Не паникуй и не преуменьшай
- 23andMe v5 чип: покрывает ~643k частых вариантов. Редкие (<1% MAF) — не покрыты
- 4-6 предложений, ясно и без жаргона"""


def build_genetic_context_block(domain: str = None, max_variants: int = 50) -> str:
    """
    Возвращает текстовый блок с клинически значимыми вариантами
    для инъекции в системный промпт GP/специалистов.

    domain: hint для фильтрации (oncology, cardiology, etc.)
            None = возвращает все значимые варианты (до max_variants)
    """
    # Носители — первыми (Ф5): иначе немногие реальные носители тонут среди тысяч
    # непроверяемых меток ClinVar. pool — значимые по убыванию патогенности
    # (источник непроверяемого хвоста). counts — точные сводки по всей таблице.
    carriers = db.get_carrier_variants(limit=200)
    pool = db.get_significant_variants(domain=domain, limit=max(max_variants, 60))
    counts = db.genome_summary_counts()
    _seen = set()
    variants = []
    for v in carriers + pool:
        rid = v.get("rsid")
        if rid in _seen:
            continue
        _seen.add(rid)
        variants.append(v)

    if not variants:
        return ""

    lines = ["=== ГЕНОМНЫЙ КОНТЕКСТ (23andMe v5) ==="]
    lines.append("Ниже — клинически значимые варианты генома пользователя.")
    lines.append("Интерпретируй как модификаторы риска, не как диагнозы.\n")

    # Группируем по (значимость × верифицированность носительства)
    # Только верифицированные hetero/homo_risk попадают в Pathogenic-секцию
    # unknown/homo_unknown/hetero_unknown → отдельная секция "не верифицировано"
    by_sig = {
        "Pathogenic":        [],   # verified hetero или homo_risk
        "Likely pathogenic": [],   # verified hetero или homo_risk
        "unverified":        [],   # любая значимость, но effect_allele=NULL
        "other":             [],   # прочие верифицированные
    }

    # Анти-тихий-отказ (genome strand-fix Ф3): верифицированный wildtype для
    # Pathogenic/Likely НЕ выбрасываем молча — иначе strand-баг (носитель →
    # ложный wildtype) стал бы невидим. Считаем и показываем сводной строкой
    # (а не сотнями строк «не носитель»).
    verified_noncarrier = {"Pathogenic": 0, "Likely pathogenic": 0}

    for v in variants:
        sig = v.get("significance", "")
        effect_allele = v.get("effect_allele") or ""
        genotype = v.get("genotype", "?")
        zyg_status, zyg_icon = _zygosity(genotype, effect_allele)

        # Wildtype — носительство верифицированно отсутствует.
        if zyg_status == "wildtype":
            if "Pathogenic" in sig and "Likely" not in sig:
                verified_noncarrier["Pathogenic"] += 1
            elif "Likely pathogenic" in sig:
                verified_noncarrier["Likely pathogenic"] += 1
            continue

        is_unverified = zyg_status in ("unknown", "homo_unknown", "hetero_unknown")

        if is_unverified:
            by_sig["unverified"].append((v, zyg_status, zyg_icon))
        elif "Pathogenic" in sig and "Likely" not in sig:
            by_sig["Pathogenic"].append((v, zyg_status, zyg_icon))
        elif "Likely pathogenic" in sig:
            by_sig["Likely pathogenic"].append((v, zyg_status, zyg_icon))
        else:
            by_sig["other"].append((v, zyg_status, zyg_icon))

    section_labels = {
        "Pathogenic":        "⚠️ ПАТОГЕННЫЕ (верифицированы)",
        "Likely pathogenic": "⚡ ВЕРОЯТНО ПАТОГЕННЫЕ (верифицированы)",
        "other":             "ℹ️ НЕОПРЕДЕЛЁННОЙ/ИНОЙ ЗНАЧИМОСТИ",
        "unverified":        "? НЕ ВЕРИФИЦИРОВАНЫ (effect_allele неизвестен — статус носительства неясен)",
    }

    for category in ("Pathogenic", "Likely pathogenic", "other", "unverified"):
        vlist = by_sig[category]
        if not vlist:
            continue
        lines.append(f"{section_labels[category]}:")
        cap = {"unverified": 8, "other": 12}.get(category)
        if category == "unverified":
            vlist = sorted(
                vlist,
                key=lambda t: 0 if "oncology" in json.loads(t[0].get("domain_tags") or "[]") else 1,
            )
        shown = vlist[:cap] if cap else vlist
        for v, zyg_status, zyg_icon in shown:
            gene = v.get("gene") or "?"
            rsid = v.get("rsid", "")
            genotype = v.get("genotype", "?")
            sig = v.get("significance", "")
            conditions = json.loads(v.get("conditions") or "[]")
            cond_str = "; ".join(conditions[:3]) if conditions else "не указано"
            summary = v.get("clinical_summary", "")
            domains = json.loads(v.get("domain_tags") or "[]")
            effect_allele = v.get("effect_allele") or ""

            zyg_note = {
                "homo_risk":      f"гомозигота ×2 ({effect_allele}{effect_allele})",
                "hetero":         f"гетерозигота ×1 ({effect_allele}/-)",
                "homo_unknown":   f"гомозигота ({genotype}), effect_allele неизвестен",
                "hetero_unknown": f"гетерозигота ({genotype}), effect_allele неизвестен",
                "unknown":        "генотип непарсируемый",
            }.get(zyg_status, "")

            lines.append(
                f"  • {gene} ({rsid}) | {genotype} | {zyg_icon} {zyg_note} | {sig}"
                f"\n    Условия: {cond_str}"
                + (f"\n    Домены: {', '.join(domains)}" if domains else "")
                + (f"\n    {summary[:200]}" if summary else "")
            )
        if cap and len(vlist) > cap:
            if category == "unverified":
                extra = max(counts["patho_unverified"] - cap, len(vlist) - cap)
                lines.append(
                    f"  … и ещё ~{extra} непроверяемых «патогенных» меток ClinVar "
                    "(не подтверждают носительство; ортогональное тестирование только при показаниях)"
                )
            else:
                lines.append(f"  … и ещё {len(vlist) - cap} верифицированных носителей иной значимости")
        lines.append("")

    if counts["patho_wildtype"]:
        lines.append("✅ ПРОВЕРЕНО — НОСИТЕЛЬСТВО НЕ ПОДТВЕРЖДЕНО (verified wildtype):")
        lines.append(
            f"  • {counts['patho_wildtype']} «патогенных/вероятно патогенных» по ClinVar — "
            "пациент НЕ несёт риск-аллель (проверено по effect_allele, strand-aware)"
        )
        lines.append("")

    lines.append("ОГРАНИЧЕНИЕ: чип 23andMe v5 не покрывает редкие варианты (<1% MAF).")
    lines.append("Высокопенетрантные мутации (BRCA1/2) покрыты частично.")
    lines.append("=== КОНЕЦ ГЕНОМНОГО КОНТЕКСТА ===")

    return "\n".join(lines)


def _search_gwas_catalog(trait_query: str) -> list[str]:
    """
    Ищет rsID в GWAS Catalog по текстовому описанию трейта.
    Возвращает список rsID ассоциированных вариантов.
    """
    try:
        # Ищем трейт
        resp = requests.get(
            "https://www.ebi.ac.uk/gwas/rest/api/efoTraits/search",
            params={"query": trait_query, "page": 0, "size": 5},
            timeout=15
        )
        resp.raise_for_status()
        traits = resp.json().get("_embedded", {}).get("efoTraits", [])
        if not traits:
            return []

        rsids = []
        for trait in traits[:2]:  # берём первые 2 трейта
            efo_id = trait.get("shortForm", "")
            if not efo_id:
                continue
            # Получаем ассоциации
            assoc_resp = requests.get(
                f"https://www.ebi.ac.uk/gwas/rest/api/associations/search/findByEfoTrait",
                params={"efoTrait": efo_id, "page": 0, "size": 20},
                timeout=15
            )
            assoc_resp.raise_for_status()
            associations = assoc_resp.json().get("_embedded", {}).get("associations", [])

            for assoc in associations:
                loci = assoc.get("loci", [])
                for locus in loci:
                    for snp in locus.get("strongestRiskAlleles", []):
                        rsid = snp.get("snpId", "")
                        if rsid and rsid.startswith("rs"):
                            rsids.append(rsid)

        return list(set(rsids))[:30]  # не больше 30 вариантов

    except Exception as e:
        log.warning(f"GWAS Catalog ошибка: {e}")
        return []


def answer_trait_question(question: str) -> str:
    """
    Отвечает на вопрос о предрасположенности.
    1. GWAS Catalog → rsIDs для трейта
    2. Ищет генотипы в raw_snps
    3. GP генерирует ответ
    """
    import anthropic
    db.init_db()

    log.info(f"Trait query: {question}")

    # 1. Ищем rsIDs в GWAS Catalog
    rsids = _search_gwas_catalog(question)
    log.info(f"GWAS Catalog нашёл {len(rsids)} rsID: {rsids[:10]}")

    # 2. Получаем генотипы из базы
    genotype_map = {}
    if rsids:
        genotype_map = db.get_snps_batch(rsids)

    # 3. Также проверяем из genetic_variants (уже аннотированные)
    annotated_context = ""
    if rsids:
        conn = db.get_conn()
        placeholders = ",".join("?" * len(rsids))
        rows = conn.execute(
            f"SELECT * FROM genetic_variants WHERE rsid IN ({placeholders})",
            rsids
        ).fetchall()
        conn.close()
        if rows:
            annotated_context = "\nИз ClinVar аннотации:\n" + "\n".join([
                f"  {r['rsid']} ({r['gene']}): {r['significance']} | {r['clinical_summary'][:100]}"
                for r in rows
            ])

    # 4. Формируем контекст для GP
    if genotype_map:
        genotypes_text = "\n".join([
            f"  {rsid}: генотип {gt}"
            for rsid, gt in genotype_map.items()
        ])
    else:
        genotypes_text = "Варианты не найдены в геноме пользователя (23andMe v5)"

    user_prompt = f"""Вопрос пользователя: {question}

Найденные варианты из GWAS Catalog для этого трейта:
rsIDs проверены: {', '.join(rsids) if rsids else 'не найдено'}

Генотипы пользователя:
{genotypes_text}
{annotated_context}

Ответь на вопрос на основе этих данных."""

    client = llm_client.guarded_client()
    msg = client.messages.create(
        model=hai_core.get_model("sonnet"),
        max_tokens=600,
        system=GENOME_QA_SYSTEM + hai_core.answer_language(),
        messages=[{"role": "user", "content": user_prompt}]
    )
    return msg.content[0].text.strip()



# ── Lifestyle-ориентированный блок (компактный, для briefов агентов) ───────────

# Гены, релевантные для каждого lifestyle-домена
LIFESTYLE_GENE_MAP = {
    "sleep": [
        "CLOCK", "PER1", "PER2", "PER3", "CRY1", "CRY2",
        "BMAL1", "MTNR1B", "NR1D1", "RORA", "VIP",
    ],
    "movement": [
        "ACTN3", "ACE", "PPARGC1A", "PPARA", "ADRB2",
        "NOS3", "MSTN", "IGF1", "MCT1", "IL6",
    ],
    "stress": [
        "COMT", "MAOA", "MAOB", "SLC6A4", "HTR2A",
        "BDNF", "NR3C1", "FKBP5", "TPH2", "CACNA1C",
    ],
    "energy": [
        "FTO", "MTHFR", "VDR", "PPARG", "LEP", "LEPR",
        "MC4R", "ADIPOQ", "GCK", "TCF7L2", "MTNR1B",
    ],
}


def build_lifestyle_genome_block(domain: str, max_variants: int = 12,
                                 only_genes: set | None = None) -> str:
    """
    Компактный геномный блок для lifestyle-агентов.
    domain: "sleep" | "movement" | "stress" | "energy"
    only_genes: если задан — ТОЛЬКО эти гены (upper-case имена). Нужен утреннему
      брифу: гейт одобряет ген персонально (≤1/мес), а не весь домен — иначе
      подавленные гены домена (в т.ч. PINNED) протекают в текст.
    Возвращает пустую строку если нет значимых вариантов.
    """
    gene_filter = LIFESTYLE_GENE_MAP.get(domain, [])
    if not gene_filter:
        return ""

    # Запрашиваем варианты напрямую по генам — не через глобальный лимит
    relevant = db.get_variants_by_genes(gene_filter, limit=max_variants * 3)

    # Клинически важные rsid — всегда включаются, резервируют последние слоты
    PINNED_BY_DOMAIN = {
        "energy":   ["rs1801133", "rs1801131"],   # MTHFR C677T, A1298C
        "sleep":    ["rs57875989", "rs228697"],    # PER3 VNTR, CRY1
        "stress":   ["rs4680"],                    # COMT Val158Met
        "movement": ["rs1815739"],                 # ACTN3 R577X
    }
    pinned_rsids = PINNED_BY_DOMAIN.get(domain, [])
    forced = []
    if pinned_rsids:
        benign = {"Benign", "Likely benign", "not provided", ""}
        conn = db.get_conn()
        ph = ",".join("?" * len(pinned_rsids))
        rows = conn.execute(
            f"SELECT * FROM genetic_variants WHERE rsid IN ({ph})", pinned_rsids
        ).fetchall()
        conn.close()
        for row in rows:
            r = dict(row)
            if r.get("genotype") and r.get("significance") not in benign:
                forced.append(r)

    # Убираем pinned из основного списка (если попали туда), чтобы не дублировать
    forced_rsids = {v["rsid"] for v in forced}
    relevant = [v for v in relevant if v["rsid"] not in forced_rsids]

    # Pinned занимают последние слоты; основной список — первые (max_variants - len(forced))
    main_slots = max_variants - len(forced)
    relevant = relevant[:main_slots] + forced

    # Персональный фильтр гена (утренний бриф): только одобренные гейтом гены.
    # Перебивает и PINNED — анти-повтор сильнее «клинически важных всегда».
    if only_genes is not None:
        want = {g.upper() for g in only_genes}
        relevant = [v for v in relevant if (v.get("gene") or "").upper() in want]

    if not relevant:
        return ""

    lines = [f"ГЕНОМНЫЙ КОНТЕКСТ [{domain.upper()}]:"]
    for v in relevant:
        gene     = v.get("gene", "?")
        genotype = v.get("genotype", "?")
        sig      = v.get("significance") or ""
        conditions = json.loads(v.get("conditions") or "[]")
        summary  = v.get("clinical_summary") or ""

        # Компактный сигнал
        if "Pathogenic" in sig and "Likely" not in sig:
            sig_icon = "⚠️ ПАТОГЕН"
        elif "Likely pathogenic" in sig:
            sig_icon = "⚡ вер.патоген"
        elif "Risk" in sig:
            sig_icon = "▲ риск"
        elif "Drug" in sig:
            sig_icon = "💊 фармакоген"
        elif "Uncertain" in sig:
            sig_icon = "? неопр."
        else:
            sig_icon = "~"

        cond_str = (conditions[0] if conditions else "") or ""
        note = (summary[:120] if summary else cond_str[:120])

        effect_allele = v.get("effect_allele") or ""
        zyg_status, _ = _zygosity(genotype, effect_allele)

        # Пропускаем только верифицированный wildtype
        if zyg_status == "wildtype":
            continue

        # Формируем ярлык носительства
        if zyg_status == "homo_risk":
            zyg_label = f"hom:{effect_allele}{effect_allele}"
        elif zyg_status == "hetero":
            zyg_label = f"het:{effect_allele}/-"
        elif zyg_status == "homo_unknown":
            zyg_label = f"hom:{genotype}?"   # гомозигота, статус не верифицирован
        elif zyg_status == "hetero_unknown":
            zyg_label = f"het:{genotype}?"   # гетерозигота, статус не верифицирован
        else:
            zyg_label = f"{genotype}?"

        # Для неверифицированных вариантов добавляем предупреждение к ноте
        if zyg_status in ("homo_unknown", "hetero_unknown"):
            note = f"[effect_allele не верифицирован] {note}"

        lines.append(f"  {gene} [{zyg_label}] | {sig_icon} | {note}")

    lines.append(f"  (23andMe v5 · только значимые варианты, не диагноз)")
    return "\n".join(lines)


if __name__ == "__main__":
    # Тест
    db.init_db()
    block = build_genetic_context_block()
    print("=== Контекстный блок ===")
    print(block or "(пусто — запустите аннотацию сначала)")
    print()
    if len(sys.argv) > 1:
        q = " ".join(sys.argv[1:])
        print(f"=== Ответ на: {q} ===")
        print(answer_trait_question(q))
