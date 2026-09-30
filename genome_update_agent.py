import llm_client
#!/usr/bin/env python3.11
"""
genome_update_agent.py
Ежемесячный синк: перепроверяет клинически значимые варианты через ClinVar API,
автоматически обновляет статусы, генерирует нарратив GP если были значимые изменения.
"""
import sys
import hai_core
import i18n
from _fmt_helpers import fmt_label
import json
import time
import logging
import requests
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _time_inject import get_today
import health_db as db


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CLINVAR_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
CLINVAR_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
CLINVAR_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
MYVARIANT_POST = "https://myvariant.info/v1/variant"
RATE_SLEEP = 0.34  # NCBI: max 3 req/s без ключа

# Движение статуса вверх по шкале риска — триггер нарратива
SEVERITY_ORDER = {
    "Benign": 0,
    "Likely benign": 1,
    "not provided": 1,
    "Uncertain significance": 2,
    "Conflicting interpretations of pathogenicity": 2,
    "risk factor": 3,
    "association": 3,
    "drug response": 3,
    "Likely pathogenic": 4,
    "Pathogenic": 5,
}

GENOME_NARRATIVE_PROMPT = """Ты — GP (главный врач) персональной health системы. 
Тебе нужно объяснить пользователю изменения в интерпретации его геномных данных.

Говори понятно, без жаргона. Объясни:
1. Что это за ген и вариант
2. Что означает генотип пользователя (гомозигота/гетерозигота — это важно)
3. Что изменилось в научном понимании
4. Что это конкретно значит для здоровья и lifestyle

Ограничение: 5-7 предложений на каждый изменённый вариант. Не паникуй, не преуменьшай.

Изменённые варианты:
{changes}

Генотипы пользователя:
{genotypes}"""


def _severity(sig: str) -> int:
    if not sig:
        return 0
    for key, val in SEVERITY_ORDER.items():
        if key.lower() in sig.lower():
            return val
    return 2  # unknown → средний


def fetch_clinvar_updates(variants: list[dict]) -> list[dict]:
    """
    Перепроверяет список вариантов через myvariant.info.
    Возвращает список изменившихся: {rsid, old_sig, new_sig, gene, conditions}.
    """
    if not variants:
        return []

    rsids = [v["rsid"] for v in variants]
    sig_map = {v["rsid"]: v["significance"] for v in variants}

    changed = []
    batch_size = 200

    for i in range(0, len(rsids), batch_size):
        batch = rsids[i:i + batch_size]
        try:
            resp = requests.post(
                MYVARIANT_POST,
                json={"ids": batch, "fields": "clinvar,dbsnp"},
                timeout=30
            )
            resp.raise_for_status()
            items = resp.json()
        except Exception as e:
            log.warning(f"ClinVar fetch error: {e}")
            time.sleep(2)
            continue

        for item in items:
            if not isinstance(item, dict):
                continue

            # Найти rsID
            rsid = None
            dbsnp_rsid = item.get("dbsnp", {}).get("rsid")
            if dbsnp_rsid:
                rsid = f"rs{dbsnp_rsid}"
            if not rsid or rsid not in sig_map:
                continue

            clinvar = item.get("clinvar", {})
            if not clinvar:
                continue
            if isinstance(clinvar, list):
                clinvar = clinvar[0]

            rcv = clinvar.get("rcv", {})
            if isinstance(rcv, list):
                rcv = rcv[0] if rcv else {}

            new_sig = (
                rcv.get("clinical_significance", "")
                or clinvar.get("clinical_significance", {}).get("description", "")
            ).strip()

            old_sig = sig_map[rsid]

            if new_sig and new_sig != old_sig:
                # Условия
                cond_raw = rcv.get("conditions", {})
                if isinstance(cond_raw, dict):
                    conditions = [cond_raw.get("name", "")]
                elif isinstance(cond_raw, list):
                    conditions = [c.get("name", "") for c in cond_raw if isinstance(c, dict)]
                else:
                    conditions = []

                gene_info = clinvar.get("gene", {})
                if isinstance(gene_info, list):
                    gene_info = gene_info[0] if gene_info else {}
                gene = gene_info.get("symbol", "")

                changed.append({
                    "rsid": rsid,
                    "gene": gene,
                    "old_sig": old_sig,
                    "new_sig": new_sig,
                    "conditions": [c for c in conditions if c],
                })

        time.sleep(RATE_SLEEP)

    return changed


def _is_significant_change(old_sig: str, new_sig: str) -> bool:
    """Значимое изменение = статус движется вверх по шкале риска."""
    return _severity(new_sig) > _severity(old_sig)


def generate_genome_narrative(changes: list[dict], genotype_map: dict[str, str]) -> str:
    """GP генерирует нарратив по значимым изменениям."""
    import anthropic
    client = llm_client.guarded_client()

    changes_text = "\n".join([
        f"- {c['rsid']} ({c['gene']}): {c['old_sig']} → {c['new_sig']}"
        f"\n  Связанные условия: {', '.join(c['conditions']) or 'не указаны'}"
        for c in changes
    ])

    genotypes_text = "\n".join([
        f"- {rsid}: генотип {gt}"
        for rsid, gt in genotype_map.items()
    ])

    prompt = GENOME_NARRATIVE_PROMPT.format(
        changes=changes_text,
        genotypes=genotypes_text
    ) + hai_core.answer_language()

    msg = client.messages.create(
        model=hai_core.get_model("sonnet"),
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text.strip()


def run_monthly_update() -> dict:
    """
    Главная функция ежемесячного обновления.
    Возвращает результат: {changed, narrative, log_id}
    """
    db.init_db()

    variants = db.get_significant_variants(limit=500)
    log.info(f"Проверяем {len(variants)} клинически значимых вариантов...")

    if not variants:
        log.info("Нет вариантов для проверки. Запустите аннотацию сначала.")
        return {"changed": 0, "narrative": None, "log_id": None}

    changed = fetch_clinvar_updates(variants)
    log.info(f"Изменилось статусов: {len(changed)}")

    # Обновляем базу для всех изменившихся
    for ch in changed:
        # Читаем текущий вариант
        conn = db.get_conn()
        row = conn.execute(
            "SELECT * FROM genetic_variants WHERE rsid=?", (ch["rsid"],)
        ).fetchone()
        conn.close()
        if not row:
            continue

        import json as _j
        existing = dict(row)
        db.upsert_genetic_variant(ch["rsid"], {
            "gene": ch["gene"] or existing.get("gene", ""),
            "genotype": existing.get("genotype", ""),
            "significance": ch["new_sig"],
            "conditions": ch["conditions"] or _j.loads(existing.get("conditions") or "[]"),
            "domain_tags": _j.loads(existing.get("domain_tags") or "[]"),
            "clinical_summary": existing.get("clinical_summary", ""),
            "annotation_source": "clinvar_monthly_update",
        })

    # Генерируем нарратив только если есть значимые изменения вверх
    significant_changes = [c for c in changed if _is_significant_change(c["old_sig"], c["new_sig"])]
    narrative = None

    if significant_changes:
        log.info(f"Значимых изменений (риск вверх): {len(significant_changes)}. Генерируем нарратив...")
        genotype_map = db.get_snps_batch([c["rsid"] for c in significant_changes])
        try:
            narrative = generate_genome_narrative(significant_changes, genotype_map)
        except Exception as e:
            log.error(f"Ошибка генерации нарратива: {e}")
            lang = i18n.lang_of()
            narrative = i18n.t("genome.reply.update_fallback", lang, count=len(significant_changes),
                               details=", ".join(i18n.t("genome.reply.changed_variant", lang,
                                   variant=c["rsid"],
                                   before=fmt_label(c["old_sig"], "genome.significance", lang, unknown_expected=True),
                                   after=fmt_label(c["new_sig"], "genome.significance", lang, unknown_expected=True))
                                   for c in significant_changes))

    log_id = db.save_genome_update_log({
        "run_date": get_today().isoformat(),
        "variants_checked": len(variants),
        "variants_changed": len(changed),
        "changes": changed,
        "narrative": narrative,
    })

    return {
        "changed": len(changed),
        "significant": len(significant_changes),
        "narrative": narrative,
        "log_id": log_id,
    }


if __name__ == "__main__":
    result = run_monthly_update()
    print(f"\nИзменилось: {result['changed']} вариантов")
    print(f"Значимых (риск вверх): {result['significant']}")
    if result["narrative"]:
        print(f"\nНарратив GP:\n{result['narrative']}")
