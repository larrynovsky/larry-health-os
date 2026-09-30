#!/usr/bin/env python3.11
"""
genome_annotator.py
Аннотирует клинически значимые SNPs из raw_snps через myvariant.info,
тегирует домены через LLM (Haiku), сохраняет в genetic_variants.

Запуск первичной аннотации: python3.11 genome_annotator.py
"""
import sys
import json
import time
import logging
import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import health_db as db


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

MYVARIANT_URL = "https://myvariant.info/v1/variant"
MYVARIANT_QUERY_URL = "https://myvariant.info/v1/query"
BATCH_SIZE = 1000         # myvariant.info принимает до 1000
RATE_LIMIT_SLEEP = 0.2    # секунд между батчами
MAX_WORKERS = 5           # параллельных запросов

# Значимость, которую считаем клинически важной (не Benign)
KEEP_SIGNIFICANCE = {
    "Pathogenic",
    "Likely pathogenic",
    "Uncertain significance",
    "Conflicting interpretations of pathogenicity",
    "risk factor",
    "association",
    "drug response",
    "protective",
}

# Public API: SEVERITY_PRIORITY — порядок сортировки SNP по клинической значимости.
# Меньшее число = выше severity. Используется для сортировки variant'ов в narratives,
# `genome_context.get_for_domain()` и в тестах UC-C-02.
# Порядок: Pathogenic(1) > Likely(3) > risk factor(6) > functional_variant(9) — он же ниже,
# в самом словаре. (До 2026-08-02 здесь стояла ссылка на раздел BLUEPRINT по номеру,
# и указывала она на генерацию конституций — номер разъехался при перенумерации.)
SEVERITY_PRIORITY: dict[str, int] = {
    "Pathogenic": 1,
    "Likely pathogenic": 3,
    "Conflicting interpretations of pathogenicity": 4,
    "Uncertain significance": 5,
    "risk factor": 6,
    "association": 7,
    "drug response": 8,
    "functional_variant": 9,
    "protective": 10,
    "Likely benign": 99,
    "Benign": 99,
}

DOMAINS = [
    "oncology", "cardiology", "neurology", "metabolism",
    "pharmacogenomics", "sleep_circadian", "inflammation",
    "endocrinology", "musculoskeletal", "immunology", "other"
]

# Детерминированный keyword → domain маппинг (без LLM, без rate limits)
DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "oncology": [
        "cancer", "carcinoma", "tumor", "tumour", "neoplasm", "lymphoma",
        "leukemia", "leukaemia", "melanoma", "sarcoma", "glioma", "adenoma",
        "brca", "lynch", "tp53", "apc", "ret", "nf1", "nf2", "vhl",
        "hereditary breast", "hereditary ovarian", "colorectal", "polyposis",
        "medullary thyroid", "pheochromocytoma", "von hippel",
    ],
    "cardiology": [
        "cardiac", "cardio", "heart", "coronary", "arrhythmia", "cardiomyopathy",
        "atrial fibrillation", "long qt", "brugada", "hypertrophic", "dilated",
        "aortic", "marfan", "familial hypercholesterol", "atherosclerosis",
        "thrombosis", "factor v leiden", "apoe", "pcsk9", "myocardial",
    ],
    "neurology": [
        "neurolog", "alzheimer", "parkinson", "epilepsy", "seizure", "dementia",
        "huntington", "spinocerebellar", "ataxia", "neuropathy", "migraine",
        "multiple sclerosis", "als ", "amyotrophic", "cerebral", "stroke",
    ],
    "metabolism": [
        "metabol", "diabetes", "insulin", "glucose", "obesity", "lipid",
        "cholesterol", "triglyceride", "fatty acid", "mthfr", "homocysteine",
        "phenylketonuria", "galactosemia", "glycogen", "lysosomal", "gaucher",
        "fabry", "wilson", "hemochromatosis", "porphyria",
    ],
    "pharmacogenomics": [
        "cyp2", "cyp3", "cyp1", "dpyd", "tpmt", "ugt1", "slco", "abcb1",
        "drug response", "drug metabolism", "warfarin", "clopidogrel",
        "tamoxifen", "codeine", "pharmacogen",
    ],
    "sleep_circadian": [
        "sleep", "circadian", "clock", "per1", "per2", "per3", "cry1", "cry2",
        "bmal", "insomnia", "narcolepsy", "restless leg",
    ],
    "inflammation": [
        "inflamm", "autoimmun", "rheumatoid", "lupus", "crohn", "colitis",
        "psoriasis", "ankylosing", "spondylitis", "il-", "interleukin",
        "tnf", "nfkb", "crp", "celiac",
    ],
    "endocrinology": [
        "thyroid", "adrenal", "pituitary", "cortisol", "testosterone",
        "estrogen", "androgen", "hormone", "hypothyroid", "hyperthyroid",
        "congenital adrenal", "hypogonadism", "vitamin d", "vdr",
    ],
    "musculoskeletal": [
        "muscle", "myopathy", "dystrophy", "actn3", "ace ", "bone",
        "osteoporosis", "arthritis", "ehlers", "connective tissue",
        "collagen", "fibromyalgia",
    ],
    "immunology": [
        "immune", "immunodeficien", "hla-", "mhc", "antibody", "immunoglobulin",
        "agammaglobulinemia", "scid", "complement",
    ],
}


# Функциональные варианты: ClinVar маркирует как Benign/no_sig, но они
# имеют доказанное функциональное значение в поведении, нейрохимии, спорте.
# Формат: rsid → {gene, description, domains, significance}
FUNCTIONAL_WHITELIST = {
    # ── Нейромедиаторы / психонейробиология ──────────────────────────────
    "rs4680":    {"gene": "COMT", "significance": "functional_variant",
                  "domains": ["neurology", "metabolism"],
                  "conditions": ["Dopamine catabolism speed; Val158Met; affects PFC dopamine tone, stress response, cognitive flexibility"]},
    "rs4633":    {"gene": "COMT", "significance": "functional_variant",
                  "domains": ["neurology"],
                  "conditions": ["COMT haplotype marker; linked to dopamine metabolism variation"]},
    "rs4818":    {"gene": "COMT", "significance": "functional_variant",
                  "domains": ["neurology"],
                  "conditions": ["COMT haplotype marker; modulates dopamine degradation rate"]},
    "rs6265":    {"gene": "BDNF", "significance": "functional_variant",
                  "domains": ["neurology", "sleep_circadian"],
                  "conditions": ["BDNF Val66Met; reduces activity-dependent BDNF secretion; affects neuroplasticity, memory, mood"]},
    "rs53576":   {"gene": "OXTR", "significance": "functional_variant",
                  "domains": ["neurology", "inflammation"],
                  "conditions": ["Oxytocin receptor; affects social bonding, empathy, stress reactivity, HPA axis regulation"]},
    "rs1800497": {"gene": "ANKK1", "significance": "functional_variant",
                  "domains": ["neurology", "metabolism"],
                  "conditions": ["DRD2 Taq1A (ANKK1 rs1800497); reduces dopamine D2 receptor density; linked to reward, addiction, anhedonia risk"]},
    # ── Циркадный ритм / сон ─────────────────────────────────────────────
    "rs1801260": {"gene": "CLOCK", "significance": "functional_variant",
                  "domains": ["sleep_circadian"],
                  "conditions": ["CLOCK gene 3111T/C; evening chronotype, delayed sleep phase tendency"]},
    "rs2304672": {"gene": "CLOCK", "significance": "functional_variant",
                  "domains": ["sleep_circadian"],
                  "conditions": ["CLOCK variant; associated with sleep duration and circadian preference"]},
    "rs228697":  {"gene": "CLOCK", "significance": "functional_variant",
                  "domains": ["sleep_circadian"],
                  "conditions": ["CLOCK haplotype; modulates circadian period length"]},
    # ── Движение / физическая подготовка ─────────────────────────────────
    "rs1815739": {"gene": "ACTN3", "significance": "functional_variant",
                  "domains": ["musculoskeletal"],
                  "conditions": ["ACTN3 R577X; R allele = alpha-actinin-3 in fast-twitch fibers (power); XX = endurance adaptation"]},
    "rs1799752": {"gene": "ACE",  "significance": "functional_variant",
                  "domains": ["cardiology", "musculoskeletal"],
                  "conditions": ["ACE I/D polymorphism; D = higher ACE activity, power/strength; I = lower ACE, endurance advantage"]},
    "rs4994":    {"gene": "ADRB3", "significance": "functional_variant",
                  "domains": ["metabolism", "musculoskeletal"],
                  "conditions": ["Beta-3 adrenergic receptor Trp64Arg; affects lipolysis in adipose tissue, metabolic rate, exercise response"]},
    # ── Метаболизм (дополнительные) ──────────────────────────────────────
    "rs9939609": {"gene": "FTO",  "significance": "functional_variant",
                  "domains": ["metabolism"],
                  "conditions": ["FTO intron variant; associated with obesity risk, appetite regulation, energy expenditure"]},
    "rs662":     {"gene": "PON1", "significance": "functional_variant",
                  "domains": ["cardiology", "metabolism"],
                  "conditions": ["PON1 Q192R; paraoxonase activity; affects HDL antioxidant capacity, organophosphate metabolism"]},
    # ── Сердечно-сосудистый риск / коагуляция ────────────────────────────
    "rs1799963": {"gene": "F2",   "significance": "risk factor",
                  "domains": ["cardiology"],
                  "conditions": ["Prothrombin G20210A; increased prothrombin levels; risk of venous thromboembolism"]},
    "rs6025":    {"gene": "F5",   "significance": "risk factor",
                  "domains": ["cardiology"],
                  "conditions": ["Factor V Leiden R506Q; resistance to activated protein C; thrombophilia risk"]},
    # ── Фармакогеномика (ключевые) ────────────────────────────────────────
    "rs1045642": {"gene": "ABCB1", "significance": "drug response",
                  "domains": ["pharmacogenomics"],
                  "conditions": ["MDR1/ABCB1 C3435T; P-glycoprotein drug efflux pump; affects drug bioavailability (digoxin, chemo, statins)"]},
    "rs4244285": {"gene": "CYP2C19", "significance": "drug response",
                  "domains": ["pharmacogenomics"],
                  "conditions": ["CYP2C19*2 loss-of-function; poor metabolizer; affects clopidogrel, PPIs, antidepressants, antifungals"]},
    "rs4986893": {"gene": "CYP2C19", "significance": "drug response",
                  "domains": ["pharmacogenomics"],
                  "conditions": ["CYP2C19*3 loss-of-function; Asian-prevalent poor metabolizer allele"]},
    "rs1799853": {"gene": "CYP2C9", "significance": "drug response",
                  "domains": ["pharmacogenomics"],
                  "conditions": ["CYP2C9*2; reduced warfarin, NSAID, and phenytoin metabolism"]},
    "rs1057910": {"gene": "CYP2C9", "significance": "drug response",
                  "domains": ["pharmacogenomics"],
                  "conditions": ["CYP2C9*3; severely reduced enzyme activity; warfarin sensitivity, NSAID toxicity risk"]},
    # ── Воспаление (дополнительные) ──────────────────────────────────────
    "rs1800896": {"gene": "IL10",  "significance": "functional_variant",
                  "domains": ["inflammation", "immunology"],
                  "conditions": ["IL-10 promoter -1082A/G; affects anti-inflammatory cytokine production; influences autoimmune and infection response"]},
    "rs361525":  {"gene": "TNF",   "significance": "functional_variant",
                  "domains": ["inflammation"],
                  "conditions": ["TNF-alpha -238G/A; promoter variant; modulates TNF expression in inflammatory states"]},
}


def tag_domains_keywords(gene: str, conditions: list, significance: str) -> list[str]:
    """Детерминированный keyword-маппинг доменов. Быстро, без API."""
    text = " ".join([
        (gene or "").lower(),
        " ".join(conditions).lower(),
        significance.lower(),
    ])

    tags = []
    for domain, keywords in DOMAIN_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            tags.append(domain)

    return tags if tags else ["other"]


def fetch_myvariant_batch(rsids: list[str]) -> dict:
    """Запрашивает аннотации для батча rsID через myvariant.info POST."""
    # myvariant.info принимает rsID через query с полем 'ids'
    payload = {
        "ids": ",".join(rsids),
        "fields": "clinvar,dbsnp,cadd",
        "assembly": "hg19",
    }
    try:
        resp = requests.post(
            "https://myvariant.info/v1/variant",
            json={"ids": rsids, "fields": "clinvar,dbsnp"},
            timeout=30
        )
        resp.raise_for_status()
        return {item.get("_id", ""): item for item in resp.json() if isinstance(item, dict)}
    except Exception as e:
        log.warning(f"myvariant.info ошибка: {e}")
        return {}


def parse_clinvar(item: dict) -> dict | None:
    """Извлекает клинически значимые поля из ответа myvariant."""
    clinvar = item.get("clinvar", {})
    if not clinvar:
        return None

    # ClinVar может вернуть список или словарь
    if isinstance(clinvar, list):
        clinvar = clinvar[0]

    rcv = clinvar.get("rcv", {})
    if isinstance(rcv, list):
        rcv = rcv[0] if rcv else {}

    significance = (
        rcv.get("clinical_significance", "")
        or clinvar.get("clinical_significance", {}).get("description", "")
    )
    if not significance:
        return None

    # Нормализуем
    significance = significance.strip()
    # Фильтруем незначимые
    SKIP = {"Benign", "Likely benign", "Benign/Likely benign", "not provided", ""}
    if significance in SKIP:
        return None
    if not any(sig.lower() in significance.lower() for sig in KEEP_SIGNIFICANCE):
        return None

    # Условия (болезни)
    conditions_raw = rcv.get("conditions", {})
    if isinstance(conditions_raw, dict):
        conditions = [conditions_raw.get("name", "")]
    elif isinstance(conditions_raw, list):
        conditions = [c.get("name", "") for c in conditions_raw if isinstance(c, dict)]
    else:
        conditions = []
    conditions = [c for c in conditions if c]

    # Ген
    gene_info = clinvar.get("gene", {})
    if isinstance(gene_info, list):
        gene_info = gene_info[0] if gene_info else {}
    gene = gene_info.get("symbol", "") or ""

    # dbSNP как fallback для гена
    if not gene:
        dbsnp = item.get("dbsnp", {})
        gene_info2 = dbsnp.get("gene", {})
        if isinstance(gene_info2, list):
            gene_info2 = gene_info2[0] if gene_info2 else {}
        gene = gene_info2.get("symbol", "") or ""

    summary = rcv.get("preferred_name", "") or clinvar.get("variant_id", "")

    return {
        "significance": significance,
        "conditions": conditions,
        "gene": gene,
        "clinical_summary": str(summary)[:500],
    }





def extract_ref_alts(hits: list[dict]) -> tuple[str | None, list[str], str | None, str]:
    """ClinVar-first извлечение (ref, alts) для ОДНОГО rsid из хитов myvariant.

    myvariant отдаёт мультиаллельные позиции несколькими хитами (разные _id,
    общий query=rsid). Клинически значим тот alt, у которого есть запись ClinVar,
    поэтому ref/alt берём из clinvar.* в приоритете, а dbsnp/vcf — fallback.

    Возвращает (ref|None, alts, source, agg_status):
      agg_status='ok'              — есть консистентная пара(ы), один ref;
      agg_status='source_conflict' — >1 различных ref (strand/представление) → не доверяем;
      agg_status='no_source'       — нет ни одной ref/alt пары.

    Мультиаллельность (один ref, >1 alt) НЕ разрешается здесь — список alts
    передаётся в resolve_effect_allele, который вернёт multiallelic_ambiguous.
    """
    cv: set[tuple[str, str]] = set()
    other: set[tuple[str, str]] = set()
    for it in hits or []:
        cvr = it.get("clinvar")
        for c in (cvr if isinstance(cvr, list) else [cvr]):
            if isinstance(c, dict) and c.get("ref") and c.get("alt"):
                cv.add((str(c["ref"]).upper(), str(c["alt"]).upper()))
        for key in ("dbsnp", "vcf"):
            d = it.get(key)
            if isinstance(d, dict) and d.get("ref") and d.get("alt"):
                other.add((str(d["ref"]).upper(), str(d["alt"]).upper()))
    pairs = cv or other
    source = "clinvar" if cv else ("dbsnp_vcf" if other else None)
    if not pairs:
        return None, [], None, "no_source"
    refs = {r for r, _ in pairs}
    if len(refs) > 1:
        return None, [], source, "source_conflict"
    ref = refs.pop()
    alts = sorted({a for _, a in pairs})
    return ref, alts, source, "ok"


def _process_batch(batch: list[tuple]) -> list[dict]:
    """Обрабатывает один батч rsID → список вариантов для сохранения."""
    rsid_list = [r[0] for r in batch]
    genotype_map = {r[0]: r[1] for r in batch}
    results = fetch_myvariant_batch(rsid_list)
    to_save = []

    for rsid, item in results.items():
        original_rsid = rsid  # keep original key for genotype_map lookup
        actual_rsid = item.get("dbsnp", {}).get("rsid")
        if actual_rsid:
            # Normalize: strip any existing 'rs'/'RS' prefix then re-add
            actual_rsid_str = str(actual_rsid).strip()
            if actual_rsid_str.lower().startswith("rs"):
                actual_rsid_str = actual_rsid_str[2:]
            rsid = f"rs{actual_rsid_str}"
        elif not rsid.startswith("rs"):
            continue

        parsed = parse_clinvar(item)
        annotation_source = "myvariant+clinvar"
        whitelist_domains = None
        if not parsed:
            wl_entry = FUNCTIONAL_WHITELIST.get(rsid) or FUNCTIONAL_WHITELIST.get(original_rsid)
            if wl_entry:
                parsed = {
                    "significance": wl_entry["significance"],
                    "conditions": wl_entry["conditions"],
                    "gene": wl_entry["gene"],
                    "clinical_summary": (wl_entry["conditions"][0][:500]
                                          if wl_entry["conditions"] else ""),
                }
                annotation_source = "functional_whitelist"
                whitelist_domains = wl_entry["domains"]
            else:
                continue

        domain_tags = (whitelist_domains if whitelist_domains is not None
                       else tag_domains_keywords(
                           parsed["gene"], parsed["conditions"], parsed["significance"]))
        # Genotype: try normalized rsid first, then original key
        genotype = genotype_map.get(rsid) or genotype_map.get(original_rsid, "")
        to_save.append({
            "rsid": rsid,
            "genotype": genotype,
            "domain_tags": domain_tags,
            "annotation_source": annotation_source,
            **parsed,
        })

    return to_save


def annotate_all(limit_rsids: int = None):
    """
    Главная функция: берёт все rsID из raw_snps,
    запрашивает myvariant.info параллельными батчами, сохраняет значимые варианты.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    db.init_db()

    log.info("Загружаем rsID из raw_snps...")
    conn = db.get_conn()
    rows = conn.execute("SELECT rsid, genotype FROM raw_snps WHERE rsid LIKE 'rs%'").fetchall()
    conn.close()

    all_rsids = [(r["rsid"], r["genotype"]) for r in rows]
    if limit_rsids:
        all_rsids = all_rsids[:limit_rsids]

    log.info(f"Всего rsID для аннотации: {len(all_rsids):,}, батчей: {len(all_rsids)//BATCH_SIZE + 1}")

    batches = [all_rsids[i:i + BATCH_SIZE] for i in range(0, len(all_rsids), BATCH_SIZE)]
    total_saved = 0
    completed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_process_batch, b): i for i, b in enumerate(batches)}
        for future in as_completed(futures):
            completed += 1
            try:
                variants = future.result()
                for v in variants:
                    rsid = v.pop("rsid")
                    db.upsert_genetic_variant(rsid, v)
                    total_saved += 1
            except Exception as e:
                log.warning(f"Батч ошибка: {e}")

            if completed % 10 == 0:
                log.info(f"  Прогресс: {completed}/{len(batches)} батчей, сохранено {total_saved}")

            time.sleep(RATE_LIMIT_SLEEP)

    log.info(f"Аннотация завершена. Сохранено клинически значимых вариантов: {total_saved}")
    return total_saved


def annotate_rsids(rsids: list[str]) -> list[dict]:
    """
    Аннотирует конкретный список rsID (для ad-hoc запросов из /genome).
    Возвращает список аннотированных вариантов с генотипом из raw_snps.
    """
    db.init_db()
    genotype_map = db.get_snps_batch(rsids)

    results = fetch_myvariant_batch(rsids)
    annotated = []

    for rsid in rsids:
        item = results.get(rsid) or results.get(rsid.replace("rs", ""))
        if not item:
            continue
        parsed = parse_clinvar(item)
        if parsed:
            annotated.append({
                "rsid": rsid,
                "genotype": genotype_map.get(rsid, "not_found"),
                **parsed,
            })
        else:
            # Возвращаем просто генотип даже без ClinVar
            genotype = genotype_map.get(rsid)
            if genotype:
                annotated.append({
                    "rsid": rsid,
                    "genotype": genotype,
                    "significance": "not in ClinVar",
                    "conditions": [],
                    "gene": "",
                    "clinical_summary": "",
                })

    return annotated


if __name__ == "__main__":
    import sys
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    saved = annotate_all(limit_rsids=limit)
    print(f"\nКлинически значимых вариантов сохранено: {saved}")
