"""traits_pipeline.py — Deterministic trait calling from raw SNP data.

Public interface (one module = one function rule):
    run(conn, genome_import_id) -> dict

Schema (created by health_db._migrate_traits_tables / Phase F DDL):
    trait_phenotypes(
        id               INTEGER PK,
        trait            TEXT UNIQUE,     -- 'lactase_persistence' | 'eye_color_tendency' | 'apoe_genotype'
        category         TEXT,            -- 'nutrition' | 'appearance' | 'disease_risk'
        phenotype        TEXT,            -- human-readable call
        confidence       TEXT,            -- 'high' | 'medium' | 'indeterminate'
        supporting_rsids TEXT,            -- JSON array  ["rs4988235"]
        genotypes_json   TEXT,            -- JSON object {"rs4988235": "GG"}
        notes            TEXT,
        genome_import_id INTEGER,
        computed_at      TEXT
    )

Staleness / WFR guard (Таненбаум §7.3.5):
    Caller stamps genome_imports.phase_f_monogenic_at after run().
    traits_context._check_staleness() compares max(genome_imports.id) with
    genome_import_id stored in trait_phenotypes — same pattern as pharmaco.

Traits implemented (Phase F):
    lactase_persistence  — rs4988235 (MCM6 enhancer; A=persistence allele)
    eye_color_tendency   — rs12913832 (HERC2/OCA2; G=blue tendency)
    apoe_genotype        — rs7412 + rs429358 (APOE ε isoform diplotype)

SNPs missing from Tellmegen chip → indeterminate:
    rs17822931 (ABCC11 earwax), rs762551 (CYP1A2 caffeine),
    rs1229984 (ADH1B alcohol), rs713598/rs1726866 (TAS2R38 bitter taste)

Strand-flip safety:
    _fetch_db_effect_alleles() pulls effect_allele from genetic_variants
    WHERE effect_allele_status='resolved', overriding hardcoded fallbacks
    (same pattern as pharmaco_pipeline, fixes DPYD*13-class bugs).
"""

from __future__ import annotations

import json
import logging

import i18n
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class _TraitCall:
    trait: str
    category: str
    phenotype: str
    confidence: str                    # 'high' | 'medium' | 'indeterminate'
    supporting_rsids: list[str] = field(default_factory=list)
    genotypes: dict[str, str | None] = field(default_factory=dict)
    notes: str = ""


# ---------------------------------------------------------------------------
# Hardcoded effect alleles (fallbacks; DB-resolved values override these)
# ---------------------------------------------------------------------------

_EFFECT_ALLELES: dict[str, str] = {
    # Trait SNPs
    "rs4988235":  "A",   # MCM6: A = lactase persistence
    "rs12913832": "G",   # HERC2: G = blue eye tendency
    "rs7412":     "T",   # APOE: T = ε2 marker (Cys158)
    "rs429358":   "C",   # APOE: C = ε4 marker (Arg112)
}

_ALL_RSIDS = list(_EFFECT_ALLELES.keys())

# ---------------------------------------------------------------------------
# Shared helpers (replicate pattern from pharmaco_pipeline)
# ---------------------------------------------------------------------------

def _fetch_genotypes(conn, rsids: list[str]) -> dict[str, str | None]:
    """Fetch genotype strings from raw_snps."""
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
    """Fetch strand-corrected effect alleles from genetic_variants (status='resolved').
    These override _EFFECT_ALLELES hardcoded fallbacks.
    """
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
    """Count occurrences of allele in a 2-char diploid genotype string."""
    if not genotype or not allele or len(genotype) != 2:
        return -1   # -1 = indeterminate
    return sum(1 for a in genotype if a == allele)


# ---------------------------------------------------------------------------
# Individual trait callers
# ---------------------------------------------------------------------------

def _call_lactase(
    gts: dict[str, str | None],
    db_ea: dict[str, str],
) -> _TraitCall:
    """rs4988235 (MCM6 enhancer): A allele = lactase persistence.
    GG → non-persistent (intolerant)
    AG → partially persistent (tolerant in most)
    AA → persistent (fully tolerant)
    """
    rsid = "rs4988235"
    ea = db_ea.get(rsid, _EFFECT_ALLELES[rsid])
    gt = gts.get(rsid)
    count = _count_allele(gt, ea)

    if count < 0:
        return _TraitCall(
            trait="lactase_persistence", category="nutrition",
            phenotype="indeterminate", confidence="indeterminate",
            supporting_rsids=[rsid], genotypes={rsid: gt},
            notes=_TRAIT_LEGACY_NOTES["traits.notes.lactase_missing"],
        )
    if count == 2:
        phenotype = "Persistent (lactose tolerant)"
        notes = i18n.t("traits.notes.lactase_persistent", "ru")
    elif count == 1:
        phenotype = "Partially persistent (likely tolerant)"
        notes = i18n.t("traits.notes.lactase_partial", "ru")
    else:
        phenotype = "Non-persistent (lactose intolerant)"
        notes = i18n.t("traits.notes.lactase_nonpersistent", "ru")

    return _TraitCall(
        trait="lactase_persistence", category="nutrition",
        phenotype=phenotype, confidence="high",
        supporting_rsids=[rsid], genotypes={rsid: gt},
        notes=notes,
    )


def _call_eye_color(
    gts: dict[str, str | None],
    db_ea: dict[str, str],
) -> _TraitCall:
    """rs12913832 (HERC2/OCA2): G allele drives blue/light eyes.
    GG → strong blue/grey tendency
    AG → mixed/hazel/green (probabilistic)
    AA → brown/dark eyes
    Confidence: GG and AA = high deterministic anchors;
                AG = medium (environmental and other loci modulate).
    """
    rsid = "rs12913832"
    ea = db_ea.get(rsid, _EFFECT_ALLELES[rsid])
    gt = gts.get(rsid)
    count = _count_allele(gt, ea)

    if count < 0:
        return _TraitCall(
            trait="eye_color_tendency", category="appearance",
            phenotype="indeterminate", confidence="indeterminate",
            supporting_rsids=[rsid], genotypes={rsid: gt},
            notes=_TRAIT_LEGACY_NOTES["traits.notes.eye_missing"],
        )
    if count == 2:
        phenotype = "Light eyes (blue/grey likely)"
        confidence = "high"
        notes = i18n.t("traits.notes.eye_light", "ru")
    elif count == 1:
        phenotype = "Mixed/hazel/green eyes (probabilistic)"
        confidence = "medium"
        notes = i18n.t("traits.notes.eye_mixed", "ru")
    else:
        phenotype = "Dark/brown eyes likely"
        confidence = "high"
        notes = i18n.t("traits.notes.eye_dark", "ru")

    return _TraitCall(
        trait="eye_color_tendency", category="appearance",
        phenotype=phenotype, confidence=confidence,
        supporting_rsids=[rsid], genotypes={rsid: gt},
        notes=notes,
    )


def _call_apoe(
    gts: dict[str, str | None],
    db_ea: dict[str, str],
) -> _TraitCall:
    """APOE diplotype from rs7412 (T=ε2 marker) + rs429358 (C=ε4 marker).

    Haplotype logic:
        rs7412=T  → Cys at 158 → ε2 marker present
        rs429358=C → Arg at 112 → ε4 marker present
        Both absent  → ε3 haplotype

    Diplotype from allele counts (assuming no phase ambiguity for common cases):
        0 T, 0 C → ε3/ε3  (most common, baseline AD risk)
        1 T, 0 C → ε2/ε3  (slightly reduced LDL, modest AD risk reduction)
        2 T, 0 C → ε2/ε2  (very rare, risk of type III hyperlipoproteinaemia)
        0 T, 1 C → ε3/ε4  (elevated AD risk ~3×)
        0 T, 2 C → ε4/ε4  (greatly elevated AD risk ~12×)
        1 T, 1 C → ε2/ε4  (phase ambiguous; or ε2/ε3 + ε3/ε4 — flag note)

    A 'resolved' effect_allele_status confirms strand orientation only for
    the input being processed; it is not a property of every genome.
    """
    rsid_e2 = "rs7412"    # T = ε2
    rsid_e4 = "rs429358"  # C = ε4

    ea_e2 = db_ea.get(rsid_e2, _EFFECT_ALLELES[rsid_e2])
    ea_e4 = db_ea.get(rsid_e4, _EFFECT_ALLELES[rsid_e4])

    gt_e2 = gts.get(rsid_e2)
    gt_e4 = gts.get(rsid_e4)

    n_e2 = _count_allele(gt_e2, ea_e2)
    n_e4 = _count_allele(gt_e4, ea_e4)

    if n_e2 < 0 or n_e4 < 0:
        missing = [r for r, g in [(rsid_e2, gt_e2), (rsid_e4, gt_e4)] if g is None]
        return _TraitCall(
            trait="apoe_genotype", category="disease_risk",
            phenotype="indeterminate", confidence="indeterminate",
            supporting_rsids=[rsid_e2, rsid_e4],
            genotypes={rsid_e2: gt_e2, rsid_e4: gt_e4},
            notes=_TRAIT_LEGACY_NOTES["traits.notes.apoe_missing"].format(missing=missing),
        )

    # Determine diplotype
    if n_e2 == 0 and n_e4 == 0:
        diplotype = "ε3/ε3"
        notes = i18n.t("traits.notes.apoe_e3_e3", "ru")
    elif n_e2 == 1 and n_e4 == 0:
        diplotype = "ε2/ε3"
        notes = i18n.t("traits.notes.apoe_e2_e3", "ru")
    elif n_e2 == 2 and n_e4 == 0:
        diplotype = "ε2/ε2"
        notes = i18n.t("traits.notes.apoe_e2_e2", "ru")
    elif n_e2 == 0 and n_e4 == 1:
        diplotype = "ε3/ε4"
        notes = i18n.t("traits.notes.apoe_e3_e4", "ru")
    elif n_e2 == 0 and n_e4 == 2:
        diplotype = "ε4/ε4"
        notes = i18n.t("traits.notes.apoe_e4_e4", "ru")
    elif n_e2 == 1 and n_e4 == 1:
        diplotype = "ε2/ε4 (phase uncertain)"
        notes = _TRAIT_LEGACY_NOTES["traits.notes.apoe_phase_uncertain"]
    else:
        diplotype = f"unusual ({n_e2}×ε2-marker, {n_e4}×ε4-marker)"
        notes = i18n.t("traits.notes.apoe_unusual", "ru")

    confidence = "medium" if "phase uncertain" in diplotype or "unusual" in diplotype else "high"

    return _TraitCall(
        trait="apoe_genotype", category="disease_risk",
        phenotype=diplotype,
        confidence=confidence,
        supporting_rsids=[rsid_e2, rsid_e4],
        genotypes={rsid_e2: gt_e2, rsid_e4: gt_e4},
        notes=notes,
    )


# Legacy storage values differ from the person copy only where vocabulary rules require it.
_TRAIT_LEGACY_NOTES = {
    'traits.notes.lactase_missing': 'SNP отсутствует в геноме (не покрыт чипом).',
    'traits.notes.eye_missing': 'SNP отсутствует в геноме.',
    'traits.notes.apoe_missing': 'SNP отсутствует в геноме: {missing}.',
    'traits.notes.apoe_phase_uncertain': 'По одному ε2- и ε4-маркеру. Наиболее вероятный генотип ε2/ε4, но фаза не определена по SNP-данным — альтернативно ε2/ε3 + ε3/ε4 (без ε2/ε4). При клиническом значении рекомендуется секвенирование полного гена APOE.',
}
_TRAIT_NOTE_KEYS = (
    "traits.notes.lactase_missing",
    "traits.notes.lactase_persistent",
    "traits.notes.lactase_partial",
    "traits.notes.lactase_nonpersistent",
    "traits.notes.eye_missing",
    "traits.notes.eye_light",
    "traits.notes.eye_mixed",
    "traits.notes.eye_dark",
    "traits.notes.apoe_missing",
    "traits.notes.apoe_e3_e3",
    "traits.notes.apoe_e2_e3",
    "traits.notes.apoe_e2_e2",
    "traits.notes.apoe_e3_e4",
    "traits.notes.apoe_e4_e4",
    "traits.notes.apoe_phase_uncertain",
    "traits.notes.apoe_unusual",
)


def _genetic_notes_for_person(rows: list[dict], keys: tuple[str, ...],
                              legacy: dict[str, str], lang: str | None = None) -> list[dict]:
    """Translate exact known notes on read, including old rows; never rewrite custom notes.

    The one parameterised seed note contains a printable list of missing markers.
    Capture its text without interpreting it or re-running genetic calls.
    """
    lang = lang or i18n.lang_of()
    templates = [(key, legacy[key] if key in legacy else i18n.t(key, "ru")) for key in keys]
    result = []
    for row in rows:
        note = row.get("notes")
        display = note
        if isinstance(note, str):
            for key, template in templates:
                before, field, after = template.partition("{missing}")
                if field:
                    if note.startswith(before) and note.endswith(after):
                        missing = note[len(before):len(note) - len(after) if after else None]
                        display = i18n.t(key, lang, missing=missing)
                        break
                elif note == template or note == i18n.t(key, "ru"):
                    display = i18n.t(key, lang)
                    break
        result.append({**row, "notes": display})
    return result


def trait_notes_for_person(rows: list[dict], lang: str | None = None) -> list[dict]:
    """Person view of stored trait rows; the caller supplies rows from the current tenant."""
    return _genetic_notes_for_person(rows, _TRAIT_NOTE_KEYS, _TRAIT_LEGACY_NOTES, lang)


# ---------------------------------------------------------------------------
# DB upsert
# ---------------------------------------------------------------------------

def _upsert(conn, call: _TraitCall, genome_import_id: int) -> None:
    conn.execute(
        """
        INSERT INTO trait_phenotypes
            (trait, category, phenotype, confidence,
             supporting_rsids, genotypes_json, notes,
             genome_import_id, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(trait) DO UPDATE SET
            category         = excluded.category,
            phenotype        = excluded.phenotype,
            confidence       = excluded.confidence,
            supporting_rsids = excluded.supporting_rsids,
            genotypes_json   = excluded.genotypes_json,
            notes            = excluded.notes,
            genome_import_id = excluded.genome_import_id,
            computed_at      = excluded.computed_at
        """,
        (
            call.trait,
            call.category,
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
    """Compute all trait phenotypes and upsert into trait_phenotypes.

    Args:
        conn: sqlite3.Connection to the canonical health.db
        genome_import_id: ID from genome_imports for this pipeline run

    Returns:
        dict with keys:
            'status'   : 'ok' | 'partial'
            'results'  : {trait: phenotype}
            'indeterminate_traits': [trait, ...]
    """
    log.info("traits_pipeline.run(): genome_import_id=%d", genome_import_id)

    # Ensure table exists (idempotent DDL)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trait_phenotypes (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            trait            TEXT    NOT NULL,
            category         TEXT    NOT NULL DEFAULT '',
            phenotype        TEXT    NOT NULL,
            confidence       TEXT    NOT NULL DEFAULT 'indeterminate',
            supporting_rsids TEXT,
            genotypes_json   TEXT,
            notes            TEXT,
            genome_import_id INTEGER REFERENCES genome_imports(id),
            computed_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_trait
            ON trait_phenotypes(trait);
    """)
    conn.commit()

    gts = _fetch_genotypes(conn, _ALL_RSIDS)
    db_ea = _fetch_db_effect_alleles(conn, _ALL_RSIDS)
    log.debug("genotypes: %s", gts)
    log.debug("db effect alleles: %s", db_ea)

    calls: list[_TraitCall] = [
        _call_lactase(gts, db_ea),
        _call_eye_color(gts, db_ea),
        _call_apoe(gts, db_ea),
    ]

    results: dict[str, str] = {}
    indeterminate: list[str] = []

    for call in calls:
        _upsert(conn, call, genome_import_id)
        results[call.trait] = call.phenotype
        if call.confidence == "indeterminate":
            indeterminate.append(call.trait)
        log.info(
            "trait %-25s → %-45s [%s]",
            call.trait, call.phenotype, call.confidence,
        )

    # Stamp timestamp on genome_imports
    conn.execute(
        "UPDATE genome_imports SET phase_f_monogenic_at = datetime('now') WHERE id = ?",
        (genome_import_id,),
    )
    conn.commit()

    status = "partial" if indeterminate else "ok"
    log.info("traits_pipeline done: %s (indeterminate=%s)", status, indeterminate)
    return {
        "status": status,
        "results": results,
        "indeterminate_traits": indeterminate,
    }
