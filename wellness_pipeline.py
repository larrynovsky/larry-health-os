"""wellness_pipeline.py — Health-modifying SNP calling for wellness phenotypes.

Public interface (one module = one function rule):
    run(conn, genome_import_id) -> dict

Schema (created by Phase G DDL):
    wellness_phenotypes(
        id               INTEGER PK,
        gene             TEXT,
        trait            TEXT,
        phenotype        TEXT,            -- human-readable call
        confidence       TEXT,            -- 'high' | 'medium' | 'indeterminate'
        supporting_rsids TEXT,            -- JSON ["rs1801133", "rs1801131"]
        genotypes_json   TEXT,            -- JSON {"rs1801133": "GA"}
        notes            TEXT,
        genome_import_id INTEGER,
        computed_at      TEXT
    )
    PRIMARY KEY (gene, trait)

Staleness / WFR guard (Таненбаум §7.3.5):
    genome_imports.phase_g_wellness_at stamped after run().
    wellness_context._check_staleness() compares max(genome_imports.id)
    with genome_import_id in wellness_phenotypes.

Wellness traits implemented (Phase G):
    MTHFR   / folate_metabolism    — rs1801133 (C677T) + rs1801131 (A1298C) compound
    COMT    / dopamine_metabolism  — rs4680 (Val158Met)
    FTO     / obesity_risk         — rs9939609 (intronic; fat mass associated)
    HFE     / iron_overload_risk   — rs1799945 (H63D; resolution depends on input context)

APOE is in traits_pipeline (disease_risk category) rather than here;
ACTN3 calls with multiallelic_ambiguous status remain indeterminate and are not included.

Compound rule design (MTHFR):
    C677T het + A1298C wt  → mildly reduced (~65% activity)
    C677T wt + A1298C het  → mildly reduced (~70–80% activity)
    C677T hom + A1298C wt  → moderately reduced (~30% activity)
    C677T het + A1298C het → compound het → moderately reduced (~50% activity)
    C677T hom + A1298C het → significantly reduced (rare, <5% activity)
    C677T hom + A1298C hom → severely reduced (very rare)

Strand-flip safety:
    _fetch_db_effect_alleles() overrides hardcoded fallbacks with
    genetic_variants.effect_allele WHERE status='resolved'.
"""

from __future__ import annotations

import json
import logging

import i18n
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class _WellnessCall:
    gene: str
    trait: str
    phenotype: str
    confidence: str                    # 'high' | 'medium' | 'indeterminate'
    supporting_rsids: list[str] = field(default_factory=list)
    genotypes: dict[str, str | None] = field(default_factory=dict)
    notes: str = ""


# ---------------------------------------------------------------------------
# Hardcoded effect alleles (fallbacks; DB-resolved values override)
# ---------------------------------------------------------------------------

_EFFECT_ALLELES: dict[str, str] = {
    "rs1801133": "A",   # MTHFR C677T: A (minor) = 677T variant
    "rs1801131": "G",   # MTHFR A1298C: G (minor) = 1298C variant
    "rs4680":    "A",   # COMT Val158Met: A = Met (low activity)
    "rs9939609": "A",   # FTO: A = obesity-associated allele
    "rs1799945": "G",   # HFE H63D: G = His63Asp; resolve strand per input
}

_ALL_RSIDS = list(_EFFECT_ALLELES.keys())

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _fetch_genotypes(conn, rsids: list[str]) -> dict[str, str | None]:
    if not rsids:
        return {}
    placeholders = ",".join("?" * len(rsids))
    rows = conn.execute(
        f"SELECT rsid, genotype FROM raw_snps WHERE rsid IN ({placeholders})",
        rsids,
    ).fetchall()
    result: dict[str, str | None] = {r: None for r in rsids}
    for row in rows:
        result[row[0]] = row[1]
    return result


def _fetch_db_effect_alleles(conn, rsids: list[str]) -> dict[str, str]:
    """Fetch strand-corrected effect alleles from genetic_variants (status='resolved')."""
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='genetic_variants'"
    ).fetchone():
        return {}
    placeholders = ",".join("?" * len(rsids))
    rows = conn.execute(
        f"SELECT rsid, effect_allele FROM genetic_variants "
        f"WHERE rsid IN ({placeholders}) AND effect_allele_status='resolved' "
        f"AND effect_allele IS NOT NULL",
        rsids,
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def _count_allele(genotype: str | None, allele: str) -> int:
    """Count occurrences of allele in a 2-char diploid genotype. Returns -1 if indeterminate."""
    if not genotype or not allele or len(genotype) != 2:
        return -1
    return sum(1 for a in genotype if a == allele)


# ---------------------------------------------------------------------------
# Individual wellness callers
# ---------------------------------------------------------------------------

def _call_mthfr(
    gts: dict[str, str | None],
    db_ea: dict[str, str],
) -> _WellnessCall:
    """MTHFR compound rule: rs1801133 (C677T) + rs1801131 (A1298C).

    Both SNPs independently reduce MTHFR enzyme activity.
    Compound heterozygosity (one of each) has greater impact than single het.
    Effect allele for rs1801133: A (= 677T variant; creates thermolabile enzyme)
    Effect allele for rs1801131: G (= 1298C variant; reduces activity, less than C677T)
    """
    rsid_677 = "rs1801133"
    rsid_1298 = "rs1801131"

    ea_677 = db_ea.get(rsid_677, _EFFECT_ALLELES[rsid_677])
    ea_1298 = db_ea.get(rsid_1298, _EFFECT_ALLELES[rsid_1298])

    gt_677 = gts.get(rsid_677)
    gt_1298 = gts.get(rsid_1298)

    n_677 = _count_allele(gt_677, ea_677)    # copies of 677T
    n_1298 = _count_allele(gt_1298, ea_1298)  # copies of 1298C

    all_gts = {rsid_677: gt_677, rsid_1298: gt_1298}
    rsids = [rsid_677, rsid_1298]

    if n_677 < 0 and n_1298 < 0:
        return _WellnessCall(
            gene="MTHFR", trait="folate_metabolism",
            phenotype="indeterminate", confidence="indeterminate",
            supporting_rsids=rsids, genotypes=all_gts,
            notes=_WELLNESS_LEGACY_NOTES["wellness.notes.mthfr_missing"],
        )

    # Map counts to phenotype
    if n_677 == 0 and n_1298 == 0:
        phenotype = "Normal (wildtype)"
        notes = i18n.t("wellness.notes.mthfr_normal", "ru")
    elif n_677 == 1 and n_1298 == 0:
        phenotype = "Mildly reduced (C677T heterozygous)"
        notes = i18n.t("wellness.notes.mthfr_677_het", "ru")
    elif n_677 == 0 and n_1298 == 1:
        phenotype = "Mildly reduced (A1298C heterozygous)"
        notes = i18n.t("wellness.notes.mthfr_1298_het", "ru")
    elif n_677 == 2 and n_1298 == 0:
        phenotype = "Moderately reduced (C677T homozygous)"
        notes = i18n.t("wellness.notes.mthfr_677_hom", "ru")
    elif n_677 == 1 and n_1298 == 1:
        phenotype = "Moderately reduced (compound heterozygous C677T + A1298C)"
        notes = i18n.t("wellness.notes.mthfr_compound", "ru")
    elif n_677 == 0 and n_1298 == 2:
        phenotype = "Mildly reduced (A1298C homozygous)"
        notes = i18n.t("wellness.notes.mthfr_1298_hom", "ru")
    elif n_677 == 2 and n_1298 == 1:
        phenotype = "Severely reduced (C677T hom + A1298C het)"
        notes = i18n.t("wellness.notes.mthfr_severe", "ru")
    else:
        phenotype = f"Reduced ({n_677}×C677T, {n_1298}×A1298C)"
        notes = i18n.t("wellness.notes.mthfr_rare", "ru")

    confidence = "indeterminate" if (n_677 < 0 or n_1298 < 0) else "high"

    return _WellnessCall(
        gene="MTHFR", trait="folate_metabolism",
        phenotype=phenotype, confidence=confidence,
        supporting_rsids=rsids, genotypes=all_gts,
        notes=notes,
    )


def _call_comt(
    gts: dict[str, str | None],
    db_ea: dict[str, str],
) -> _WellnessCall:
    """COMT rs4680 (Val158Met): A allele = Met = low COMT activity.
    High COMT (Val/Val, GG): fast dopamine breakdown → lower pain sensitivity, stress resilience↑
    Low COMT (Met/Met, AA): slow breakdown → higher dopamine tone, better working memory,
                            but higher stress sensitivity
    Intermediate (Val/Met, GA): balanced
    """
    rsid = "rs4680"
    ea = db_ea.get(rsid, _EFFECT_ALLELES[rsid])
    gt = gts.get(rsid)
    count = _count_allele(gt, ea)

    if count < 0:
        return _WellnessCall(
            gene="COMT", trait="dopamine_metabolism",
            phenotype="indeterminate", confidence="indeterminate",
            supporting_rsids=[rsid], genotypes={rsid: gt},
            notes=_WELLNESS_LEGACY_NOTES["wellness.notes.comt_missing"],
        )

    if count == 2:
        phenotype = "Low COMT activity (Met/Met)"
        notes = i18n.t("wellness.notes.comt_low", "ru")
    elif count == 1:
        phenotype = "Intermediate COMT activity (Val/Met)"
        notes = i18n.t("wellness.notes.comt_intermediate", "ru")
    else:
        phenotype = "High COMT activity (Val/Val)"
        notes = i18n.t("wellness.notes.comt_high", "ru")

    return _WellnessCall(
        gene="COMT", trait="dopamine_metabolism",
        phenotype=phenotype, confidence="high",
        supporting_rsids=[rsid], genotypes={rsid: gt},
        notes=notes,
    )


def _call_fto(
    gts: dict[str, str | None],
    db_ea: dict[str, str],
) -> _WellnessCall:
    """FTO rs9939609: A allele = increased obesity/fat mass risk.
    Effect size: ~0.4 kg/m² BMI per A allele in European populations.
    Mechanism: intronic variant affecting IRX3/IRX5 expression in adipocytes.
    """
    rsid = "rs9939609"
    ea = db_ea.get(rsid, _EFFECT_ALLELES[rsid])
    gt = gts.get(rsid)
    count = _count_allele(gt, ea)

    if count < 0:
        return _WellnessCall(
            gene="FTO", trait="obesity_risk",
            phenotype="indeterminate", confidence="indeterminate",
            supporting_rsids=[rsid], genotypes={rsid: gt},
            notes=_WELLNESS_LEGACY_NOTES["wellness.notes.fto_missing"],
        )

    if count == 2:
        phenotype = "Elevated obesity risk (AA homozygous)"
        notes = i18n.t("wellness.notes.fto_elevated", "ru")
    elif count == 1:
        phenotype = "Modest obesity risk (AT heterozygous)"
        notes = i18n.t("wellness.notes.fto_modest", "ru")
    else:
        phenotype = "No FTO obesity risk (TT wildtype)"
        notes = i18n.t("wellness.notes.fto_neutral", "ru")

    return _WellnessCall(
        gene="FTO", trait="obesity_risk",
        phenotype=phenotype, confidence="medium",
        supporting_rsids=[rsid], genotypes={rsid: gt},
        notes=notes,
    )


def _call_hfe(
    gts: dict[str, str | None],
    db_ea: dict[str, str],
) -> _WellnessCall:
    """HFE rs1799945 (H63D): G allele = Asp63 (minor/pathogenic variant).
    H63D het is extremely common (~20% carrier rate in Europeans).
    Het carrier: mildly increased iron absorption, rarely causes clinical haemochromatosis.
    Hom H63D/H63D: increased iron accumulation risk, but usually mild.
    A palindromic_het_resolved status is valid only when the input context confirms the strand.
    """
    rsid = "rs1799945"
    ea = db_ea.get(rsid, _EFFECT_ALLELES[rsid])
    gt = gts.get(rsid)
    count = _count_allele(gt, ea)

    if count < 0:
        return _WellnessCall(
            gene="HFE", trait="iron_overload_risk",
            phenotype="indeterminate", confidence="indeterminate",
            supporting_rsids=[rsid], genotypes={rsid: gt},
            notes=_WELLNESS_LEGACY_NOTES["wellness.notes.hfe_missing"],
        )

    if count == 2:
        phenotype = "H63D homozygous (elevated iron risk)"
        notes = i18n.t("wellness.notes.hfe_homozygous", "ru")
    elif count == 1:
        phenotype = "H63D heterozygous carrier"
        notes = i18n.t("wellness.notes.hfe_heterozygous", "ru")
    else:
        phenotype = "No H63D variant (wildtype)"
        notes = i18n.t("wellness.notes.hfe_absent", "ru")

    return _WellnessCall(
        gene="HFE", trait="iron_overload_risk",
        phenotype=phenotype, confidence="high",
        supporting_rsids=[rsid], genotypes={rsid: gt},
        notes=notes,
    )


# Storage compatibility only; display text lives in i18n.
_WELLNESS_LEGACY_NOTES = {
    'wellness.notes.mthfr_missing': 'Оба SNP (C677T, A1298C) отсутствуют в геноме.',
    'wellness.notes.comt_missing': 'SNP rs4680 отсутствует в геноме.',
    'wellness.notes.fto_missing': 'SNP rs9939609 отсутствует в геноме.',
    'wellness.notes.hfe_missing': 'SNP rs1799945 отсутствует в геноме.',
}
_WELLNESS_NOTE_KEYS = (
    "wellness.notes.mthfr_missing",
    "wellness.notes.mthfr_normal",
    "wellness.notes.mthfr_677_het",
    "wellness.notes.mthfr_1298_het",
    "wellness.notes.mthfr_677_hom",
    "wellness.notes.mthfr_compound",
    "wellness.notes.mthfr_1298_hom",
    "wellness.notes.mthfr_severe",
    "wellness.notes.mthfr_rare",
    "wellness.notes.comt_missing",
    "wellness.notes.comt_low",
    "wellness.notes.comt_intermediate",
    "wellness.notes.comt_high",
    "wellness.notes.fto_missing",
    "wellness.notes.fto_elevated",
    "wellness.notes.fto_modest",
    "wellness.notes.fto_neutral",
    "wellness.notes.hfe_missing",
    "wellness.notes.hfe_homozygous",
    "wellness.notes.hfe_heterozygous",
    "wellness.notes.hfe_absent",
)


def wellness_notes_for_person(rows: list[dict], lang: str | None = None) -> list[dict]:
    """Person view of stored wellness rows, including notes written before i18n."""
    from traits_pipeline import _genetic_notes_for_person
    return _genetic_notes_for_person(rows, _WELLNESS_NOTE_KEYS, _WELLNESS_LEGACY_NOTES, lang)


# ---------------------------------------------------------------------------
# DB upsert
# ---------------------------------------------------------------------------

def _upsert(conn, call: _WellnessCall, genome_import_id: int) -> None:
    conn.execute(
        """
        INSERT INTO wellness_phenotypes
            (gene, trait, phenotype, confidence,
             supporting_rsids, genotypes_json, notes,
             genome_import_id, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(gene, trait) DO UPDATE SET
            phenotype        = excluded.phenotype,
            confidence       = excluded.confidence,
            supporting_rsids = excluded.supporting_rsids,
            genotypes_json   = excluded.genotypes_json,
            notes            = excluded.notes,
            genome_import_id = excluded.genome_import_id,
            computed_at      = excluded.computed_at
        """,
        (
            call.gene,
            call.trait,
            call.phenotype,
            call.confidence,
            json.dumps(call.supporting_rsids),
            json.dumps(call.genotypes),
            call.notes,
            genome_import_id,
        ),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(conn, genome_import_id: int) -> dict:
    """Compute all wellness phenotypes and upsert into wellness_phenotypes.

    Args:
        conn: sqlite3.Connection to the canonical health.db
        genome_import_id: ID from genome_imports for this pipeline run

    Returns:
        dict with keys:
            'status'                : 'ok' | 'partial'
            'results'               : {f"{gene}/{trait}": phenotype}
            'indeterminate_traits'  : ["GENE/trait", ...]
    """
    log.info("wellness_pipeline.run(): genome_import_id=%d", genome_import_id)

    # Ensure table exists (idempotent DDL)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS wellness_phenotypes (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            gene             TEXT    NOT NULL,
            trait            TEXT    NOT NULL,
            phenotype        TEXT    NOT NULL,
            confidence       TEXT    NOT NULL DEFAULT 'indeterminate',
            supporting_rsids TEXT,
            genotypes_json   TEXT,
            notes            TEXT,
            genome_import_id INTEGER REFERENCES genome_imports(id),
            computed_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_wellness_gene_trait
            ON wellness_phenotypes(gene, trait);
    """)
    conn.commit()

    gts = _fetch_genotypes(conn, _ALL_RSIDS)
    db_ea = _fetch_db_effect_alleles(conn, _ALL_RSIDS)
    log.debug("wellness genotypes: %s", gts)
    log.debug("wellness db effect alleles: %s", db_ea)

    calls: list[_WellnessCall] = [
        _call_mthfr(gts, db_ea),
        _call_comt(gts, db_ea),
        _call_fto(gts, db_ea),
        _call_hfe(gts, db_ea),
    ]

    results: dict[str, str] = {}
    indeterminate: list[str] = []

    for call in calls:
        _upsert(conn, call, genome_import_id)
        key = f"{call.gene}/{call.trait}"
        results[key] = call.phenotype
        if call.confidence == "indeterminate":
            indeterminate.append(key)
        log.info(
            "wellness %-30s → %-55s [%s]",
            key, call.phenotype, call.confidence,
        )

    # Stamp timestamp on genome_imports (repurposing phase_g_prs_at column)
    # TODO: rename column to phase_g_wellness_at in a future migration
    conn.execute(
        "UPDATE genome_imports SET phase_g_prs_at = datetime('now') WHERE id = ?",
        (genome_import_id,),
    )
    conn.commit()

    status = "partial" if indeterminate else "ok"
    log.info("wellness_pipeline done: %s (indeterminate=%s)", status, indeterminate)
    return {
        "status": status,
        "results": results,
        "indeterminate_traits": indeterminate,
    }
