#!/usr/bin/env python3.11
"""
backfill_effect_alleles.py — v4 (strand-aware + het-palindromic systemic fix).

Дозаполняет effect_allele / ref_allele / assembly / effect_allele_status в
genetic_variants. Конвейер на каждый вариант:
  hits(myvariant) → extract_ref_alts (ClinVar-first) → resolve_effect_allele
  (strand-резолюция) → het-palindromic upgrade → точечный UPDATE.

Принципы:
  - НЕ через upsert_genetic_variant — точечный UPDATE, чтобы не трогать
    significance / prev_significance (иначе ложные genome-нарративы).
  - При любой неоднозначности effect_allele=NULL, но status пишется всегда —
    покрытие 'resolved' мониторится, NULL объяснён. Никогда не угадываем.
  - Идемпотентно: повторный прогон даёт тот же результат.
  - Пишет только на primary (Studio). На прочих узлах get_conn вернёт RO и
    UPDATE упадёт; для тестов — ALLOW_WRITE_NONPRIMARY=1.
  - Скипает варианты с effect_allele_status='palindromic_het_resolved' —
    они уже разрешены и не требуют перезаписи.

Het-palindromic resolution (встроено в compute_for_variant, v4):
  Для гетерозиготных генотипов AT/TA/CG/GC strand-неоднозначность фактически
  отсутствует — оба аллеля присутствуют в строке. Резолюция:
    Path 1: извлечь alt из HGVS в clinical_summary (паттерн N>M)
    Path 2: complement(ref_allele) — из БД или из myvariant hits
  fix_palindromic_het.py был одноразовым бэкфиллом для существующих данных;
  этот модуль теперь покрывает все новые варианты системно.

Запуск на Studio:  /opt/homebrew/bin/python3.11 backfill_effect_alleles.py
"""
from __future__ import annotations

import logging
import re as _re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import health_db as db
from effect_allele import resolve_effect_allele
from genome_annotator import extract_ref_alts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BATCH = 400
# 23andMe v5 = GRCh37; myvariant vcf/dbsnp по умолчанию hg19 = GRCh37.
ASSEMBLY = "GRCh37"
FIELDS = "clinvar.ref,clinvar.alt,dbsnp.ref,dbsnp.alt,vcf.ref,vcf.alt"

# ── het-palindromic helpers ────────────────────────────────────────────────

_HGVS_RE = _re.compile(r'[ACGT]>([ACGT])', _re.IGNORECASE)
_COMP_MAP = {'A': 'T', 'T': 'A', 'C': 'G', 'G': 'C'}
_HET_PAL_GTS = frozenset({'AT', 'TA', 'CG', 'GC'})


def _het_palindromic_alt(
    genotype: str,
    clinical_summary: str | None,
    ref_allele: str | None,
) -> str | None:
    """Alt для het-palindromic варианта.

    Path 1: HGVS-нотация в clinical_summary (N>M).
    Path 2: complement(ref_allele).
    Возвращает None, если оба пути не дали результата.
    """
    gt = genotype.upper()
    # Path 1: HGVS parse
    m = _HGVS_RE.search(clinical_summary or "")
    if m:
        alt = m.group(1).upper()
        if alt in gt:
            return alt
        comp = _COMP_MAP.get(alt)
        if comp and comp in gt:
            return comp
    # Path 2: complement(ref_allele)
    if ref_allele:
        comp = _COMP_MAP.get(ref_allele.upper())
        if comp and comp in gt:
            return comp
    return None


# ── network ────────────────────────────────────────────────────────────────

def fetch_hits(rsids: list[str]) -> dict[str, list[dict]]:
    """POST myvariant, группирует хиты по rsid (поле query). {rsid: [item,...]}."""
    try:
        resp = requests.post(
            "https://myvariant.info/v1/variant",
            json={"ids": rsids, "fields": FIELDS}, timeout=45,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001 — сеть, любая ошибка → пустой батч
        log.warning(f"myvariant fetch error: {e}")
        return {}
    out: dict[str, list[dict]] = {}
    for it in (data if isinstance(data, list) else []):
        q = it.get("query")
        if q:
            out.setdefault(q, []).append(it)
    return out


# ── core ────────────────────────────────────────────────────────────────────

def compute_for_variant(
    genotype,
    hits,
    *,
    clinical_summary: str | None = None,
    ref_allele: str | None = None,
) -> tuple[str | None, str | None, str | None, str]:
    """Чистая. (effect_allele|None, ref_allele|None, assembly|None, status).

    status ∈ resolve_effect_allele-статусы ∪ {source_conflict, palindromic_het_resolved}.
    ref_allele пишем для прозрачности даже когда не resolved (если пара была).
    assembly — только для resolved (иначе нечего фиксировать).

    Het-palindromic upgrade:
      Если resolve_effect_allele вернул 'palindromic' И генотип — известная
      гетерозиготная пара (AT/TA/CG/GC), пробуем _het_palindromic_alt.
      ref_allele fallback: используем DB-значение или ref из myvariant (hits).
    """
    ref, alts, _source, agg = extract_ref_alts(hits)
    if agg == "source_conflict":
        return None, ref, None, "source_conflict"
    if agg == "no_source":
        return None, None, None, "no_data"
    allele, status = resolve_effect_allele(genotype, ref, alts)
    if status == "palindromic" and genotype.upper() in _HET_PAL_GTS:
        alt = _het_palindromic_alt(genotype, clinical_summary, ref_allele or ref)
        if alt:
            return alt, ref, None, "palindromic_het_resolved"
    assembly = ASSEMBLY if status == "resolved" else None
    return allele, ref, assembly, status


def run(fetch=fetch_hits, *, limit: int | None = None) -> dict[str, int]:
    """Прогон по всем вариантам. fetch инъектируется для тестов (мок без сети)."""
    if not db._is_primary():
        raise RuntimeError(
            "backfill_effect_alleles: запись разрешена только на primary (Studio) "
            "или с ALLOW_WRITE_NONPRIMARY=1. Прерываю (single-primary guard)."
        )
    # Миграция колонок (ref_allele/assembly/effect_allele_status) — забота init_db
    # при старте сервисов (Ф0, уже задеплоено). Backfill их не создаёт, только пишет.
    # Скипаем palindromic_het_resolved — они уже разрешены.
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, rsid, genotype, clinical_summary, ref_allele "
            "FROM genetic_variants "
            "WHERE (effect_allele_status IS NULL "
            "       OR effect_allele_status != 'palindromic_het_resolved') "
            "ORDER BY id"
        )]
    if limit:
        rows = rows[:limit]
    total = len(rows)
    log.info(f"backfill v4: вариантов {total}")
    stats: dict[str, int] = {}
    done = 0
    for i in range(0, total, BATCH):
        batch = rows[i:i + BATCH]
        hitsmap = fetch([r["rsid"] for r in batch])
        with db.get_conn() as conn:
            for r in batch:
                allele, ref, assembly, status = compute_for_variant(
                    r["genotype"], hitsmap.get(r["rsid"], []),
                    clinical_summary=r.get("clinical_summary"),
                    ref_allele=r.get("ref_allele"),
                )
                conn.execute(
                    "UPDATE genetic_variants SET effect_allele=?, ref_allele=?, "
                    "assembly=?, effect_allele_status=? WHERE id=?",
                    (allele, ref, assembly, status, r["id"]),
                )
                stats[status] = stats.get(status, 0) + 1
            conn.commit()
        done += len(batch)
        if done % (BATCH * 5) == 0 or done == total:
            log.info(f"  {done}/{total} | {stats}")
        time.sleep(0.2)
    log.info(f"backfill v4 готово: {stats}")
    return stats


if __name__ == "__main__":
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
    print(run(limit=lim))
