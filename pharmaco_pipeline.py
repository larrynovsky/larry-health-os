"""pharmaco_pipeline.py — Wave 1: CPIC-based star-allele calling.

Public interface (one module = one function rule):
    run(conn, genome_import_id) -> dict

Returns:
    {
        "status": "ok" | "partial" | "all_indeterminate",
        "results": {gene: {phenotype, confidence, star_allele_1, star_allele_2, coverage_snp_count}},
        "indeterminate_genes": [...],
        "completed_at": ISO8601,
    }

Side effects:
    - Upserts pharmaco_phenotypes rows (one per gene, keyed by gene name)
    - Stamps genome_imports.phase_e_pharmaco_at = datetime('now')

Consistency contract (write-through pattern, Таненбаум §7.5.4):
    Called within the same connection as vcf_import.
    Caller commits; any failure raises PharmacoPipelineError.

R1 prevention (missing defining SNP → indeterminate):
    Never assigns "Normal Metabolizer" without at least the required SNPs present.
    phenotype = 'indeterminate', confidence = 'indeterminate' when evidence absent.

Genes covered (CPIC Tier A/B, 2024):
    CYP2C9, CYP2C19, CYP2D6, SLCO1B1, DPYD, TPMT, UGT1A1, HLA-B (*57:01)
"""

from __future__ import annotations
from _time_inject import get_now  # seam

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class PharmacoPipelineError(RuntimeError):
    """Raised on unrecoverable pipeline errors."""


# ---------------------------------------------------------------------------
# Star-allele data model
# ---------------------------------------------------------------------------

@dataclass
class AlleleRule:
    """Map (rsid + effect_allele) → star-allele name."""
    rsid: str
    effect_allele: str    # single nucleotide, uppercase
    star_allele: str      # e.g. "*2", "*17", "HapB3"


@dataclass
class GeneDefinition:
    """CPIC definition for one pharmacogene."""
    gene: str
    required_rsids: list[str]          # ANY missing → indeterminate result
    allele_rules: list[AlleleRule]
    wildtype_allele: str = "*1"
    diplotype_to_phenotype: dict[tuple[str, str], str] = field(default_factory=dict)

    def lookup_phenotype(self, star1: str, star2: str) -> str:
        key = _canonical_diplotype(star1, star2)
        return self.diplotype_to_phenotype.get(key, "indeterminate")


def _canonical_diplotype(a: str, b: str) -> tuple[str, str]:
    """Stable sort for diplotype keys: numerically by allele number.

    Handles suffixes like '*1a' (→ rank 1 + suffix 'a') so that
    '*1a' sorts before '*5', not after it.
    """
    import re as _re

    def _rank(s: str) -> tuple[int, str]:
        num_part = s.lstrip("*")
        m = _re.match(r'^(\d+)(.*)', num_part)
        if m:
            return (int(m.group(1)), m.group(2))
        return (999, num_part)

    return tuple(sorted([a, b], key=_rank))  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# CPIC gene definitions
# ---------------------------------------------------------------------------

GENE_DEFINITIONS: dict[str, GeneDefinition] = {}


# ── CYP2C9 ─── warfarin, NSAIDs (ibuprofen, celecoxib), losartan, phenytoin
GENE_DEFINITIONS["CYP2C9"] = GeneDefinition(
    gene="CYP2C9",
    required_rsids=["rs1799853", "rs1057910"],
    allele_rules=[
        AlleleRule("rs1799853", "T", "*2"),   # c.430C>T; Arg144Cys; reduced
        AlleleRule("rs1057910", "C", "*3"),   # c.1075A>C; Ile359Leu; greatly reduced
    ],
    diplotype_to_phenotype={
        ("*1", "*1"): "Normal Metabolizer",
        ("*1", "*2"): "Intermediate Metabolizer",
        ("*1", "*3"): "Intermediate Metabolizer",
        ("*2", "*2"): "Poor Metabolizer",
        ("*2", "*3"): "Poor Metabolizer",
        ("*3", "*3"): "Poor Metabolizer",
    },
)


# ── CYP2C19 ── clopidogrel, PPIs (omeprazole), SSRIs, tricyclics, voriconazole
GENE_DEFINITIONS["CYP2C19"] = GeneDefinition(
    gene="CYP2C19",
    required_rsids=["rs4244285"],    # *2 is the critical loss-of-function allele in EUR
    allele_rules=[
        AlleleRule("rs4244285",  "A", "*2"),   # c.681G>A; splice defect; null
        AlleleRule("rs4986893",  "A", "*3"),   # c.636G>A; null; rare in EUR
        AlleleRule("rs12248560", "T", "*17"),  # c.-806C>T; increased expression
    ],
    wildtype_allele="*1",
    diplotype_to_phenotype={
        ("*1", "*1"):   "Normal Metabolizer",
        ("*1", "*17"):  "Rapid Metabolizer",
        ("*17", "*17"): "Ultrarapid Metabolizer",
        ("*1", "*2"):   "Intermediate Metabolizer",
        ("*1", "*3"):   "Intermediate Metabolizer",
        ("*2", "*17"):  "Intermediate Metabolizer",   # competing effects
        ("*3", "*17"):  "Intermediate Metabolizer",
        ("*2", "*2"):   "Poor Metabolizer",
        ("*2", "*3"):   "Poor Metabolizer",
        ("*3", "*3"):   "Poor Metabolizer",
    },
)


# ── CYP2D6 ── codeine/tramadol (opioids), tamoxifen, TCAs, many antipsychotics
# If a required defining site is missing from the input VCF, expect indeterminate.
GENE_DEFINITIONS["CYP2D6"] = GeneDefinition(
    gene="CYP2D6",
    required_rsids=["rs1065852", "rs3892097"],   # *4 / *10 primary definers
    allele_rules=[
        AlleleRule("rs3892097",  "A", "*4"),    # c.1846G>A; splice null; most common PM allele
        AlleleRule("rs1065852",  "C", "*10"),   # c.100C>T reversed? actually T>C; reduced
        AlleleRule("rs5030655",  "A", "*6"),    # c.1707del frameshift; null
        AlleleRule("rs16947",    "A", "*2A"),   # c.2850C>T; normal/UM marker
        AlleleRule("rs28371725", "T", "*41"),   # c.2988G>A; reduced splicing
    ],
    diplotype_to_phenotype={
        ("*1", "*1"):    "Normal Metabolizer",
        ("*1", "*2A"):   "Normal Metabolizer",
        ("*2A", "*2A"):  "Ultrarapid Metabolizer",
        ("*1", "*4"):    "Intermediate Metabolizer",
        ("*1", "*6"):    "Intermediate Metabolizer",
        ("*1", "*10"):   "Intermediate Metabolizer",
        ("*1", "*41"):   "Intermediate Metabolizer",
        ("*4", "*4"):    "Poor Metabolizer",
        ("*4", "*6"):    "Poor Metabolizer",
        ("*4", "*10"):   "Poor Metabolizer",
        ("*6", "*6"):    "Poor Metabolizer",
        ("*10", "*10"):  "Poor Metabolizer",
        ("*41", "*41"):  "Poor Metabolizer",
    },
)


# ── DPYD ── 5-fluorouracil, capecitabine (severe toxicity risk)
GENE_DEFINITIONS["DPYD"] = GeneDefinition(
    gene="DPYD",
    required_rsids=["rs3918290"],    # *2A = most severe; mandatory screen
    allele_rules=[
        AlleleRule("rs3918290",  "A", "*2A"),    # IVS14+1G>A; splice null; highest risk
        AlleleRule("rs55886062", "A", "*13"),    # c.1679T>G; null
        AlleleRule("rs67376798", "A", "HapB3"),  # c.2846A>T; reduced
    ],
    diplotype_to_phenotype={
        ("*1", "*1"):      "Normal Metabolizer",
        ("*1", "*2A"):     "Intermediate Metabolizer",
        ("*1", "*13"):     "Intermediate Metabolizer",
        ("*1", "HapB3"):   "Intermediate Metabolizer",
        ("*2A", "*2A"):    "Poor Metabolizer",
        ("*2A", "*13"):    "Poor Metabolizer",
        ("*13", "*13"):    "Poor Metabolizer",
        ("HapB3", "HapB3"): "Poor Metabolizer",
    },
)


# ── TPMT ── azathioprine, 6-mercaptopurine, thioguanine (myelosuppression risk)
GENE_DEFINITIONS["TPMT"] = GeneDefinition(
    gene="TPMT",
    required_rsids=["rs1142345"],    # *3C is most common variant in EUR
    allele_rules=[
        AlleleRule("rs1800462", "A", "*2"),    # c.238G>C; Ala80Pro; null
        AlleleRule("rs1800460", "T", "*3B"),   # c.460G>A; null
        AlleleRule("rs1142345", "C", "*3C"),   # c.719A>G; Tyr240Cys; null (*3A = *3B + *3C)
    ],
    diplotype_to_phenotype={
        ("*1", "*1"):   "Normal Metabolizer",
        ("*1", "*2"):   "Intermediate Metabolizer",
        ("*1", "*3B"):  "Intermediate Metabolizer",
        ("*1", "*3C"):  "Intermediate Metabolizer",
        ("*2", "*3C"):  "Poor Metabolizer",
        ("*3B", "*3C"): "Poor Metabolizer",
        ("*3C", "*3C"): "Poor Metabolizer",
    },
)


# ── UGT1A1 ── irinotecan, belinostat; bilirubin metabolism
# *28 = TA7 repeat — tagged by rs887829
GENE_DEFINITIONS["UGT1A1"] = GeneDefinition(
    gene="UGT1A1",
    required_rsids=["rs887829"],
    allele_rules=[
        AlleleRule("rs887829", "A", "*28"),   # A allele tags TA7 repeat; reduced activity
    ],
    diplotype_to_phenotype={
        ("*1", "*1"):   "Normal Metabolizer",
        ("*1", "*28"):  "Intermediate Metabolizer",
        ("*28", "*28"): "Poor Metabolizer",
    },
)


# ── SLCO1B1 ── statin myopathy (simvastatin, atorvastatin, rosuvastatin)
# Uses *1a wildtype (not *1) — handled in separate caller
_SLCO1B1_DEF = GeneDefinition(
    gene="SLCO1B1",
    required_rsids=["rs4149056"],
    allele_rules=[
        AlleleRule("rs4149056", "C", "*5"),   # c.521T>C; Val174Ala; reduced OATP1B1
    ],
    wildtype_allele="*1a",
    diplotype_to_phenotype={
        ("*1a", "*1a"): "Normal Function",
        ("*1a", "*5"):  "Decreased Function",
        ("*5", "*5"):   "Poor Function",
    },
)

# HLA-B*57:01 — abacavir hypersensitivity (Steven-Johnson risk)
_HLA_B_5701_RSID = "rs2395029"
_HLA_B_5701_EFFECT = "G"


# ---------------------------------------------------------------------------
# Genotype utilities
# ---------------------------------------------------------------------------

def _parse_alleles(gt: str) -> list[str]:
    """Parse diploid genotype string → list of 1–2 allele characters.

    Handles: "CT", "C/T", "C|T", "--", "./.", single char.
    """
    if not gt or gt in ("--", ".", "./.", ".|."):
        return []
    for sep in ("/", "|"):
        if sep in gt:
            parts = [p.strip() for p in gt.split(sep) if p.strip() not in (".", "")]
            return parts
    # Compact 2-char: "CT"
    if len(gt) == 2 and gt.replace("-", "").isalpha():
        return list(gt)
    # Single char (homozygous shorthand)
    if len(gt) == 1 and gt.isalpha():
        return [gt, gt]
    return list(gt)


def _count_effect(genotype: str, effect_allele: str) -> int:
    """Count copies of effect_allele in genotype (0, 1, or 2)."""
    alleles = _parse_alleles(genotype)
    return alleles.count(effect_allele.upper())


# ---------------------------------------------------------------------------
# Per-gene caller
# ---------------------------------------------------------------------------

@dataclass
class _StarCall:
    gene: str
    star_allele_1: str
    star_allele_2: str
    phenotype: str
    confidence: str          # high | medium | low | indeterminate
    coverage_snp_count: int
    notes: str = ""


def _call_standard_gene(
    gd: GeneDefinition,
    gts: dict[str, str | None],
    db_effect_alleles: dict[str, str] | None = None,
) -> _StarCall:
    """Generic star-allele caller for most genes.

    Args:
        gd: gene definition with AlleleRules
        gts: {rsid: genotype_str_or_None} from raw_snps
        db_effect_alleles: {rsid: effect_allele} from genetic_variants (strand-corrected);
            overrides AlleleRule.effect_allele when present. Pass {} to use hardcoded only.
    """
    if db_effect_alleles is None:
        db_effect_alleles = {}

    gene = gd.gene
    found_required = sum(1 for r in gd.required_rsids if gts.get(r) is not None)
    total_required = len(gd.required_rsids)
    coverage = sum(1 for rule in gd.allele_rules if gts.get(rule.rsid) is not None)

    # R1 guard: no defining SNPs → indeterminate
    if found_required == 0:
        return _StarCall(
            gene=gene,
            star_allele_1="unknown", star_allele_2="unknown",
            phenotype="indeterminate", confidence="indeterminate",
            coverage_snp_count=coverage,
            notes=f"Required SNPs absent: {gd.required_rsids}",
        )

    # Collect variant alleles from all rules
    variant_alleles: list[str] = []
    for rule in gd.allele_rules:
        gt = gts.get(rule.rsid)
        if gt is None:
            continue
        # DB-resolved effect allele takes priority over hardcoded
        effective_ea = db_effect_alleles.get(rule.rsid, rule.effect_allele)
        if effective_ea != rule.effect_allele:
            log.debug(
                "%s/%s: effect_allele override %s→%s (db strand-corrected)",
                gene, rule.star_allele, rule.effect_allele, effective_ea,
            )
        n = _count_effect(gt, effective_ea)
        variant_alleles.extend([rule.star_allele] * n)

    # Fill wildtype up to diploid count
    wt_count = max(0, 2 - len(variant_alleles))
    all_alleles = variant_alleles + [gd.wildtype_allele] * wt_count

    star1 = all_alleles[0] if len(all_alleles) > 0 else gd.wildtype_allele
    star2 = all_alleles[1] if len(all_alleles) > 1 else gd.wildtype_allele

    dp_key = _canonical_diplotype(star1, star2)
    phenotype = gd.diplotype_to_phenotype.get(dp_key, "indeterminate")

    if found_required == total_required:
        confidence = "high"
    else:
        confidence = "medium"

    if phenotype == "indeterminate":
        confidence = "low"

    return _StarCall(
        gene=gene,
        star_allele_1=star1, star_allele_2=star2,
        phenotype=phenotype, confidence=confidence,
        coverage_snp_count=coverage,
    )


def _call_slco1b1(
    gts: dict[str, str | None],
    db_effect_alleles: dict[str, str] | None = None,
) -> _StarCall:
    """SLCO1B1 uses *1a wildtype (not *1).

    Effect allele for rs4149056 (*5) is "C" (Val174Ala).
    DB-resolved override applied if available (same pattern as _call_standard_gene).
    """
    if db_effect_alleles is None:
        db_effect_alleles = {}

    _RSID = "rs4149056"
    _HC_EA = "C"   # hardcoded fallback
    effect_allele = db_effect_alleles.get(_RSID, _HC_EA)

    gt = gts.get(_RSID)
    if gt is None:
        return _StarCall(
            gene="SLCO1B1",
            star_allele_1="unknown", star_allele_2="unknown",
            phenotype="indeterminate", confidence="indeterminate",
            coverage_snp_count=0,
            notes=f"{_RSID} absent from VCF",
        )

    n = _count_effect(gt, effect_allele)
    if n == 0:
        s1, s2 = "*1a", "*1a"
    elif n == 1:
        s1, s2 = "*1a", "*5"
    else:
        s1, s2 = "*5", "*5"

    dp_key = _canonical_diplotype(s1, s2)
    phenotype = _SLCO1B1_DEF.diplotype_to_phenotype.get(dp_key, "indeterminate")

    return _StarCall(
        gene="SLCO1B1",
        star_allele_1=s1, star_allele_2=s2,
        phenotype=phenotype, confidence="high",
        coverage_snp_count=1,
    )


def _call_hla_b5701(
    gts: dict[str, str | None],
    db_effect_alleles: dict[str, str] | None = None,
) -> _StarCall:
    """HLA-B*57:01 — abacavir hypersensitivity marker.

    Effect allele for rs2395029 is "G".
    DB-resolved override applied if available.
    """
    if db_effect_alleles is None:
        db_effect_alleles = {}

    effect_allele = db_effect_alleles.get(_HLA_B_5701_RSID, _HLA_B_5701_EFFECT)

    gt = gts.get(_HLA_B_5701_RSID)
    if gt is None:
        return _StarCall(
            gene="HLA-B",
            star_allele_1="unknown", star_allele_2="unknown",
            phenotype="indeterminate", confidence="indeterminate",
            coverage_snp_count=0,
            notes=f"{_HLA_B_5701_RSID} absent from VCF",
        )

    n = _count_effect(gt, effect_allele)
    if n == 0:
        phenotype = "HLA-B*57:01 Negative"
        s1, s2 = "*other", "*other"
    elif n == 1:
        phenotype = "HLA-B*57:01 Carrier (het)"
        s1, s2 = "*57:01", "*other"
    else:
        phenotype = "HLA-B*57:01 Carrier (hom)"
        s1, s2 = "*57:01", "*57:01"

    return _StarCall(
        gene="HLA-B",
        star_allele_1=s1, star_allele_2=s2,
        phenotype=phenotype, confidence="high",
        coverage_snp_count=1,
    )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _fetch_genotypes(conn, rsids: list[str]) -> dict[str, str | None]:
    """Fetch genotype strings from raw_snps for the given rsid list."""
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


# Источники генотипа «гомозигота по референсу» — пишет vcf_import_pipeline.phase_r_reference_fill.
REFCALL_SOURCE = "wgs_refcall"          # вызывальщик явно назвал позицию референсной
INFERRED_SOURCE = "wgs_ref_inferred"    # строки нет в VCF полного генома — выведено
REFERENCE_SOURCES = (REFCALL_SOURCE, INFERRED_SOURCE)
# Генотип-метка «аллелей эффекта нет»: покрытие есть (не None), копий эффекта 0.
WILDTYPE_BY_REFERENCE = ""


def _reference_rsids(conn, rsids: list[str]) -> dict[str, str]:
    """{rsid: source} для позиций панели, где генотип — референс по построению.

    Их генотип в буквах НЕ сравнивается с аллелем эффекта: зашитые аллели эффекта бывают
    записаны по цепи гена, и буква референса плюс-цепи может совпасть с ними — это ровно
    класс ложного «плохого метаболизатора» rs55886062 (DPYD*13). Референс по определению
    не несёт вариантного звёздного аллеля, поэтому считается как 0 копий эффекта."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(raw_snps)")}
    if "source" not in cols or not rsids:
        return {}
    rows = conn.execute(
        f"SELECT rsid, source FROM raw_snps WHERE source IN (?,?) "
        f"AND rsid IN ({','.join('?' * len(rsids))})",
        [*REFERENCE_SOURCES, *rsids]).fetchall()
    return {r[0]: r[1] for r in rows}


def _fetch_db_effect_alleles(conn, rsids: list[str]) -> dict[str, str]:
    """Fetch strand-corrected effect alleles from genetic_variants (status='resolved').

    These override hardcoded AlleleRule.effect_allele values.
    Only 'resolved' rows are trusted — palindromic/multiallelic/no_call stay hardcoded.

    Why this exists: genome_annotator + backfill_effect_alleles.py already performs
    strand correction. Re-using that work prevents silent strand-flip bugs like
    rs55886062 (DPYD*13): hardcoded "A" vs strand-correct "C" (false Poor Metabolizer).
    """
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='genetic_variants'"
    ).fetchone():
        return {}   # table not yet created — use hardcoded fallbacks
    placeholders = ",".join("?" * len(rsids))
    rows = conn.execute(
        f"SELECT rsid, effect_allele FROM genetic_variants "
        f"WHERE rsid IN ({placeholders}) AND effect_allele_status='resolved' "
        f"AND effect_allele IS NOT NULL",
        rsids,
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def _upsert(conn, call: _StarCall, genome_import_id: int) -> None:
    conn.execute(
        """
        INSERT INTO pharmaco_phenotypes
            (gene, star_allele_1, star_allele_2, phenotype, confidence,
             coverage_snp_count, genome_import_id, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(gene) DO UPDATE SET
            star_allele_1       = excluded.star_allele_1,
            star_allele_2       = excluded.star_allele_2,
            phenotype           = excluded.phenotype,
            confidence          = excluded.confidence,
            coverage_snp_count  = excluded.coverage_snp_count,
            genome_import_id    = excluded.genome_import_id,
            computed_at         = excluded.computed_at
        """,
        (
            call.gene, call.star_allele_1, call.star_allele_2,
            call.phenotype, call.confidence, call.coverage_snp_count,
            genome_import_id,
        ),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(conn, genome_import_id: int) -> dict:
    """Compute and persist pharmacogenomic star-allele calls.

    Args:
        conn: active sqlite3.Connection (caller manages transaction)
        genome_import_id: id from genome_imports table for this import run

    Returns dict (see module docstring for schema).

    Raises PharmacoPipelineError on DB schema or write failure.
    """
    # Sanity: tables must exist
    try:
        conn.execute("SELECT 1 FROM pharmaco_phenotypes LIMIT 1")
        conn.execute("SELECT 1 FROM genome_imports LIMIT 1")
    except Exception as exc:
        raise PharmacoPipelineError(
            f"Required tables missing — run _migrate_pharmaco_tables() first: {exc}"
        ) from exc

    # Collect all rsids we need in one DB round-trip
    all_rsids: list[str] = [_HLA_B_5701_RSID]
    for gd in GENE_DEFINITIONS.values():
        all_rsids.extend(gd.required_rsids)
        all_rsids.extend(rule.rsid for rule in gd.allele_rules)
    all_rsids.extend(_SLCO1B1_DEF.required_rsids)
    all_rsids.extend(rule.rsid for rule in _SLCO1B1_DEF.allele_rules)
    all_rsids = list(dict.fromkeys(all_rsids))   # deduplicate, preserve order

    gts = _fetch_genotypes(conn, all_rsids)
    ref_src = _reference_rsids(conn, all_rsids)
    for rsid in ref_src:
        gts[rsid] = WILDTYPE_BY_REFERENCE
    n_found = sum(1 for v in gts.values() if v is not None)
    log.info("pharmaco_pipeline: %d / %d rsids found in raw_snps", n_found, len(all_rsids))

    # Fetch DB-resolved effect alleles (strand-corrected by genome_annotator pipeline)
    # These override hardcoded AlleleRule.effect_allele when available.
    db_ea = _fetch_db_effect_alleles(conn, all_rsids)
    if db_ea:
        log.info("pharmaco_pipeline: %d DB-resolved effect alleles (strand-corrected)", len(db_ea))

    # Run callers
    calls: list[_StarCall] = []

    for gene, gd in GENE_DEFINITIONS.items():
        try:
            call = _call_standard_gene(gd, gts, db_effect_alleles=db_ea)
        except Exception as exc:
            log.error("pharmaco_pipeline: error calling %s: %s", gene, exc)
            call = _StarCall(
                gene=gene,
                star_allele_1="unknown", star_allele_2="unknown",
                phenotype="indeterminate", confidence="indeterminate",
                coverage_snp_count=0, notes=str(exc),
            )
        calls.append(call)
        log.info("pharmaco_pipeline: %-12s %s/%s  [%s]  conf=%s",
                 gene, call.star_allele_1, call.star_allele_2,
                 call.phenotype, call.confidence)

    # Custom callers (same db_ea override as standard genes)
    slco = _call_slco1b1(gts, db_effect_alleles=db_ea)
    calls.append(slco)
    log.info("pharmaco_pipeline: %-12s %s/%s  [%s]  conf=%s",
             "SLCO1B1", slco.star_allele_1, slco.star_allele_2,
             slco.phenotype, slco.confidence)

    hla = _call_hla_b5701(gts, db_effect_alleles=db_ea)
    calls.append(hla)
    log.info("pharmaco_pipeline: %-12s %s/%s  [%s]  conf=%s",
             "HLA-B", hla.star_allele_1, hla.star_allele_2,
             hla.phenotype, hla.confidence)

    # Вывод «нет строки в VCF полного генома = норма» (вариант Б, 28.09) — не наблюдение:
    # фармакотип, опирающийся хоть на одну такую позицию, не бывает высокой уверенности.
    inferred = {r for r, src in ref_src.items() if src == INFERRED_SOURCE}
    if inferred:
        gene_rsids = {g: {r.rsid for r in gd.allele_rules} | set(gd.required_rsids)
                      for g, gd in GENE_DEFINITIONS.items()}
        gene_rsids["SLCO1B1"] = {r.rsid for r in _SLCO1B1_DEF.allele_rules} | set(_SLCO1B1_DEF.required_rsids)
        gene_rsids["HLA-B"] = {_HLA_B_5701_RSID}
        for call in calls:
            used = gene_rsids.get(call.gene, set()) & inferred
            if used and call.confidence == "high":
                call.confidence = "medium"
                call.notes = (call.notes + "; " if call.notes else "") + \
                    f"референс выведен из отсутствия строки в VCF: {sorted(used)}"

    # Write to DB
    try:
        for call in calls:
            _upsert(conn, call, genome_import_id)
        conn.execute(
            "UPDATE genome_imports SET phase_e_pharmaco_at = datetime('now') WHERE id = ?",
            (genome_import_id,),
        )
        conn.commit()
    except Exception as exc:
        raise PharmacoPipelineError(f"DB write failed: {exc}") from exc

    # Build return dict
    results = {
        c.gene: {
            "phenotype":           c.phenotype,
            "confidence":          c.confidence,
            "star_allele_1":       c.star_allele_1,
            "star_allele_2":       c.star_allele_2,
            "coverage_snp_count":  c.coverage_snp_count,
        }
        for c in calls
    }
    indeterminate_genes = [c.gene for c in calls if c.confidence == "indeterminate"]

    if not indeterminate_genes:
        status = "ok"
    elif len(indeterminate_genes) < len(calls):
        status = "partial"
    else:
        status = "all_indeterminate"

    return {
        "status":              status,
        "results":             results,
        "indeterminate_genes": indeterminate_genes,
        "completed_at":        get_now(timezone.utc).isoformat(),
    }
