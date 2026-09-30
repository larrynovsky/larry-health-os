"""pharmaco_context.py — Pharmacogenomic text block for consilium injection.

Public interface (one module = one function rule):
    build_pharmacogenomic_block(conn=None, relevant_drugs=None) -> str

Args:
    conn: sqlite3.Connection (optional; opens own connection if None)
    relevant_drugs: list of drug names (RU or EN) to filter output;
                    None = include all actionable findings

Returns:
    Formatted multi-line string ready for LLM prompt injection.
    Special return values (sentinel strings):
        '[PHARMACO MISSING: run pharmaco_pipeline.run() first]'
            — no rows in pharmaco_phenotypes
        '[PHARMACO STALE: recompute needed]'
            — latest genome_imports.id ≠ genome_import_id in pharmaco_phenotypes

Staleness check (WFR consistency guard, Таненбаум §7.3.5):
    If a new VCF was imported after pharmaco was computed, the block is stale.
    Caller (monthly_consilium) must decide whether to block or warn.

Drug alias table (RU → EN canonical):
    Common Cyrillic brand/generic names are mapped to EN counterparts
    so `relevant_drugs` filtering works regardless of language.
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel strings (checked by callers)
# ---------------------------------------------------------------------------

SENTINEL_MISSING = "[PHARMACO MISSING: run pharmaco_pipeline.run() first]"
SENTINEL_STALE   = "[PHARMACO STALE: recompute needed]"
SENTINEL_UNSEEDED = "[CPIC REFERENCE UNSEEDED: run cpic_reference_db.seed()]"


# ---------------------------------------------------------------------------
# Drug alias table: RU brand/generic → EN canonical
# ---------------------------------------------------------------------------

_DRUG_ALIASES: dict[str, str] = {
    # Anticoagulants / antiplatelets
    "варфарин": "warfarin",
    "клопидогрел": "clopidogrel",
    "клопидогрель": "clopidogrel",
    "плавикс": "clopidogrel",
    # NSAIDs
    "ибупрофен": "ibuprofen",
    "нурофен": "ibuprofen",
    "диклофенак": "diclofenac",
    "целекоксиб": "celecoxib",
    "найз": "nimesulide",
    # PPIs
    "омепразол": "omeprazole",
    "лансопразол": "lansoprazole",
    "пантопразол": "pantoprazole",
    "рабепразол": "rabeprazole",
    # SSRIs / antidepressants
    "флуоксетин": "fluoxetine",
    "сертралин": "sertraline",
    "пароксетин": "paroxetine",
    "циталопрам": "citalopram",
    "эсциталопрам": "escitalopram",
    "амитриптилин": "amitriptyline",
    "нортриптилин": "nortriptyline",
    "венлафаксин": "venlafaxine",
    # Antipsychotics
    "галоперидол": "haloperidol",
    "рисперидон": "risperidone",
    "арипипразол": "aripiprazole",
    # Opioids / analgesics
    "кодеин": "codeine",
    "трамадол": "tramadol",
    # Oncology
    "тамоксифен": "tamoxifen",
    "иринотекан": "irinotecan",
    "фторурацил": "fluorouracil",
    "5-фторурацил": "fluorouracil",
    "капецитабин": "capecitabine",
    "азатиоприн": "azathioprine",
    "меркаптопурин": "mercaptopurine",
    "6-меркаптопурин": "mercaptopurine",
    "тиогуанин": "thioguanine",
    # HIV / antivirals
    "абакавир": "abacavir",
    # Antifungals
    "вориконазол": "voriconazole",
    # Statins
    "симвастатин": "simvastatin",
    "аторвастатин": "atorvastatin",
    "розувастатин": "rosuvastatin",
    "ловастатин": "lovastatin",
    "правастатин": "pravastatin",
    "питавастатин": "pitavastatin",
    # Thiopurines / immunosuppressants
    "циклоспорин": "cyclosporine",
    # Antiepileptics
    "фенитоин": "phenytoin",
    "карбамазепин": "carbamazepine",
    "вальпроат": "valproate",
    "ламотриджин": "lamotrigine",
}


def _normalize_drug(name: str) -> str:
    """Lowercase + strip, then map RU → EN if known, else return original."""
    n = name.strip().lower()
    return _DRUG_ALIASES.get(n, n)


# ---------------------------------------------------------------------------
# Clinical implication templates (shown in output block)
# ---------------------------------------------------------------------------

# A2-full (2026-07-17, нить diagnosis-hardcode): ген-уровневые импликации ПЕРЕНЕСЕНЫ
# в БД (cpic_gene_implication, засев cpic_reference_db.seed). Раньше здесь был литерал
# _IMPLICATIONS — теперь единый канон CPIC читают И консилиум, И дашборд (нет split-brain).
def _get_implications(gene: str, phenotype: str, conn) -> list[str]:
    """Ген-уровневые импликации из cpic_gene_implication (замена литерала _IMPLICATIONS).

    Та же substring-семантика и порядок строк — паритет со снятой константой проверяется
    golden-тестом (test_pharmaco_context_parity)."""
    import cpic_reference_db as _cpic
    return _cpic.get_gene_implications(gene, phenotype, conn)


# ---------------------------------------------------------------------------
# Drug → gene mapping (for relevant_drugs filter)
# ---------------------------------------------------------------------------

_DRUG_GENES: dict[str, list[str]] = {
    "warfarin":        ["CYP2C9"],
    "phenytoin":       ["CYP2C9"],
    "diclofenac":      ["CYP2C9"],
    "ibuprofen":       ["CYP2C9"],
    "celecoxib":       ["CYP2C9"],
    "losartan":        ["CYP2C9"],
    "clopidogrel":     ["CYP2C19"],
    "omeprazole":      ["CYP2C19"],
    "lansoprazole":    ["CYP2C19"],
    "pantoprazole":    ["CYP2C19"],
    "voriconazole":    ["CYP2C19"],
    "citalopram":      ["CYP2C19", "CYP2D6"],
    "escitalopram":    ["CYP2C19", "CYP2D6"],
    "fluoxetine":      ["CYP2D6"],
    "sertraline":      ["CYP2D6"],
    "paroxetine":      ["CYP2D6"],
    "amitriptyline":   ["CYP2D6", "CYP2C19"],
    "nortriptyline":   ["CYP2D6"],
    "haloperidol":     ["CYP2D6"],
    "risperidone":     ["CYP2D6"],
    "aripiprazole":    ["CYP2D6"],
    "codeine":         ["CYP2D6"],
    "tramadol":        ["CYP2D6"],
    "tamoxifen":       ["CYP2D6"],
    "simvastatin":     ["SLCO1B1"],
    "atorvastatin":    ["SLCO1B1"],
    "rosuvastatin":    ["SLCO1B1"],
    "pravastatin":     ["SLCO1B1"],
    "lovastatin":      ["SLCO1B1"],
    "pitavastatin":    ["SLCO1B1"],
    "fluorouracil":    ["DPYD"],
    "capecitabine":    ["DPYD"],
    "irinotecan":      ["UGT1A1"],
    "azathioprine":    ["TPMT"],
    "mercaptopurine":  ["TPMT"],
    "thioguanine":     ["TPMT"],
    "abacavir":        ["HLA-B"],
}


def _genes_for_drugs(drugs: list[str]) -> set[str]:
    """Return set of gene names relevant to the given drug list."""
    genes: set[str] = set()
    for drug in drugs:
        en = _normalize_drug(drug)
        genes.update(_DRUG_GENES.get(en, []))
    return genes


# ---------------------------------------------------------------------------
# Staleness check
# ---------------------------------------------------------------------------

def _cpic_seeded(conn) -> bool:
    """A2-full: справочник импликаций (cpic_gene_implication) засеян? Пустой ≠ «нет генома»."""
    try:
        n = conn.execute("SELECT COUNT(*) FROM cpic_gene_implication").fetchone()[0]
        return int(n) > 0
    except Exception:
        return False


def _check_staleness(conn) -> bool:
    """Return True if pharmaco data is stale (newer genome import exists).

    Compares max(genome_imports.id) with genome_import_id stored in pharmaco_phenotypes.
    """
    try:
        latest_import_id = conn.execute(
            "SELECT MAX(id) FROM genome_imports"
        ).fetchone()[0]
        if latest_import_id is None:
            return False   # no imports at all → not stale (just missing)

        pharmaco_import_id = conn.execute(
            "SELECT MIN(genome_import_id) FROM pharmaco_phenotypes"
        ).fetchone()[0]
        if pharmaco_import_id is None:
            return False   # no pharmaco rows → missing, not stale

        return int(latest_import_id) != int(pharmaco_import_id)
    except Exception as exc:
        log.warning("pharmaco_context: staleness check failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_pharmacogenomic_block(
    conn=None,
    relevant_drugs: Optional[list[str]] = None,
) -> str:
    """Build a formatted pharmacogenomics section for consilium prompt injection.

    Args:
        conn: sqlite3.Connection; if None, opens own connection via health_db.get_conn()
        relevant_drugs: optional list of drug names (RU or EN) to scope output

    Returns:
        Multi-line string with pharmacogenomic findings, or a SENTINEL_* string.
    """
    _own_conn = conn is None
    if _own_conn:
        from health_db import get_conn
        conn = get_conn()

    try:
        # Check for missing data
        row_count = conn.execute(
            "SELECT COUNT(*) FROM pharmaco_phenotypes"
        ).fetchone()[0]
        if row_count == 0:
            return SENTINEL_MISSING

        # A2-full: справочник импликаций теперь в БД. Пустой справочник —
        # отдельный отказ (не «нет генома»): честный отдельный sentinel.
        if not _cpic_seeded(conn):
            log.warning("pharmaco_context: cpic_gene_implication not seeded")
            return SENTINEL_UNSEEDED

        # Staleness check (WFR guard)
        if _check_staleness(conn):
            log.warning("pharmaco_context: stale data detected")
            return SENTINEL_STALE

        # Fetch all pharmaco rows
        rows = conn.execute(
            """
            SELECT gene, star_allele_1, star_allele_2, phenotype, confidence,
                   coverage_snp_count, computed_at
            FROM pharmaco_phenotypes
            ORDER BY gene
            """
        ).fetchall()

        # Apply drug filter if requested
        if relevant_drugs:
            filter_genes = _genes_for_drugs(relevant_drugs)
            rows = [r for r in rows if r[0] in filter_genes]
            if not rows:
                return (
                    f"[PHARMACO: нет данных для препаратов: "
                    f"{', '.join(relevant_drugs)}]"
                )

        # Build output sections
        lines: list[str] = ["=== ФАРМАКОГЕНОМИКА (CPIC) ==="]

        # Severity grouping: actionable first
        critical_lines: list[str] = []
        warning_lines:  list[str] = []
        info_lines:     list[str] = []

        for row in rows:
            gene, sa1, sa2, phenotype, confidence, coverage, computed_at = row
            diplotype = f"{sa1}/{sa2}"
            conf_tag = f"[conf={confidence}]"

            if confidence == "indeterminate":
                info_lines.append(
                    f"  {gene}: неопределённо {conf_tag} "
                    f"(нехватка SNP: coverage={coverage})"
                )
                continue

            implications = _get_implications(gene, phenotype, conn)
            gene_line = f"  {gene}: {diplotype} → {phenotype} {conf_tag}"

            if any("🚨" in i for i in implications):
                critical_lines.append(gene_line)
                critical_lines.extend(f"    {i}" for i in implications)
            elif any("⚠️" in i for i in implications):
                warning_lines.append(gene_line)
                warning_lines.extend(f"    {i}" for i in implications)
            else:
                info_lines.append(gene_line)
                if implications:
                    info_lines.extend(f"    {i}" for i in implications)

        if critical_lines:
            lines.append("\n🚨 КРИТИЧЕСКИЕ ПРЕДУПРЕЖДЕНИЯ:")
            lines.extend(critical_lines)

        if warning_lines:
            lines.append("\n⚠️ ПРЕДУПРЕЖДЕНИЯ:")
            lines.extend(warning_lines)

        if info_lines:
            lines.append("\nℹ️ ПРОЧИЕ НАХОДКИ:")
            lines.extend(info_lines)

        # Footer with timestamp
        if rows:
            computed_at = rows[0][6]
            lines.append(f"\nДанные рассчитаны: {computed_at} UTC")

        return "\n".join(lines)

    finally:
        if _own_conn:
            conn.close()
