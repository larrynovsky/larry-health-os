"""
promethease_context.py — Genomic context from Promethease for three specialist tiers.

Tiers:
  1. Lifestyle coaches  — always included, build_lifestyle_block(domain)
  2. Specialists / GP  — trigger-based, build_triggered_block(domain, trigger_type, detail)
  3. Constitution       — one-time analysis, build_constitution_block(domain)

Trigger types for specialists: persistent_trend | new_hypothesis | lab_deviation

Usage:
    python promethease_context.py import /path/to/promethease.html
    python promethease_context.py top-risks
    python promethease_context.py lifestyle sleep
"""

from __future__ import annotations
from _time_inject import get_now  # seam
import json
import sys
from pathlib import Path
from typing import Optional

from health_db import get_conn

# ---------------------------------------------------------------------------
# Domain → gene / topic mappings
# ---------------------------------------------------------------------------

DOMAIN_GENES: dict[str, list[str]] = {
    "sleep":       ["CLOCK", "PER1", "PER2", "PER3", "ARNTL", "CRY1", "CRY2", "MTNR1B"],
    "recovery":    ["IL6", "TNF", "CRP", "PPARGC1A", "SOD2", "CAT", "GSTP1"],
    "nutrition":   ["MTHFR", "FUT2", "VDR", "BCMO1", "APOE", "TCF7L2", "SLC23A1"],
    "stress":      ["COMT", "MAOA", "SLC6A4", "FKBP5", "NR3C1", "CRHR1", "BDNF"],
    "metabolism":  ["PPARG", "ADIPOQ", "FTO", "MC4R", "LEPR", "ADRB3", "IRS1"],
    "cardio":      ["ACE", "AGTR1", "CETP", "PCSK9", "LDLR", "APOB", "LPA"],
    "longevity":   ["FOXO3", "SIRT1", "SIRT3", "TERT", "TP53", "IGF1R", "APOE"],
    "inflammation":["IL1B", "IL6", "TNF", "NFE2L2", "PTGS2", "IL10", "TLR4"],
}

DOMAIN_TOPICS: dict[str, list[str]] = {
    "sleep":       ["sleep", "circadian", "melatonin"],
    "recovery":    ["recovery", "antioxidant", "inflammation", "muscle"],
    "nutrition":   ["folate", "vitamin", "absorption", "methylation", "detox"],
    "stress":      ["stress", "dopamine", "serotonin", "cortisol", "anxiety"],
    "metabolism":  ["obesity", "diabetes", "insulin", "fat", "weight"],
    "cardio":      ["heart", "cardiovascular", "cholesterol", "blood pressure"],
    "longevity":   ["aging", "longevity", "telomere", "cancer risk"],
    "inflammation":["inflammation", "autoimmune", "immune"],
}


# Key rsids for lifestyle coaches — pinned variants always shown if present
LIFESTYLE_PINNED: dict[str, list[str]] = {
    "sleep":       ["rs57875989", "rs1801260", "rs2304672"],   # PER3, CLOCK
    "recovery":    ["rs4880", "rs1799945"],                    # SOD2, HFE
    "nutrition":   ["rs1801133", "rs602662", "rs7501331"],     # MTHFR, FUT2, BCMO1
    "stress":      ["rs4680", "rs6323", "rs25531"],            # COMT, MAOA, SLC6A4
    "metabolism":  ["rs9939609", "rs1801282", "rs7903146"],    # FTO, PPARG, TCF7L2
    "cardio":      ["rs1799752", "rs429358", "rs1042522"],     # ACE, APOE, TP53
    "longevity":   ["rs2802292", "rs10457180"],                # FOXO3
    "inflammation":["rs1800795", "rs1800629"],                 # IL6, TNF
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _query_domain(
    domain: str,
    magnitude_min: float = 1.5,
    limit: int = 10,
    repute_filter: Optional[str] = None,
) -> list[dict]:
    """Return variants for a domain, ordered by magnitude DESC."""
    genes = DOMAIN_GENES.get(domain, [])
    topics = DOMAIN_TOPICS.get(domain, [])

    gene_placeholders = ",".join("?" * len(genes)) if genes else "''"
    topic_placeholders = ",".join("?" * len(topics)) if topics else "''"

    repute_clause = ""
    repute_params: list = []
    if repute_filter:
        repute_clause = "AND repute = ?"
        repute_params = [repute_filter]

    sql = f"""
        SELECT rsnum, geno, magnitude, repute, genes, genosummary, topic, numrefs
        FROM promethease_variants
        WHERE magnitude >= ?
          AND (
              genes IN ({gene_placeholders})
              OR topic IN ({topic_placeholders})
          )
          {repute_clause}
        ORDER BY magnitude DESC
        LIMIT ?
    """
    params = [magnitude_min] + genes + topics + repute_params + [limit]
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _format_variant(r: dict, compact: bool = False) -> str:
    """Format a single SNP row for inclusion in a prompt."""
    repute_icon = {"Bad": "⚠️", "Good": "✅", "unknown": "❓"}.get(r["repute"], "❓")
    mag = f"{r['magnitude']:.1f}"
    if compact:
        return f"  {repute_icon} {r['rsnum']} ({r['genes']}) mag={mag} — {r['genosummary']}"
    return (
        f"  {repute_icon} {r['rsnum']} | {r['genes']} | geno={r['geno']} | "
        f"mag={mag} | {r['repute']}\n"
        f"     {r['genosummary']}\n"
        f"     Topic: {r['topic']} | refs: {r['numrefs']}"
    )


def _get_pinned(domain: str) -> list[dict]:
    """Fetch pinned rsids for a domain (if present in DB)."""
    rsids = LIFESTYLE_PINNED.get(domain, [])
    if not rsids:
        return []
    placeholders = ",".join("?" * len(rsids))
    sql = f"""
        SELECT rsnum, geno, magnitude, repute, genes, genosummary, topic, numrefs
        FROM promethease_variants
        WHERE rsnum IN ({placeholders})
        ORDER BY magnitude DESC
    """
    with get_conn() as conn:
        rows = conn.execute(sql, rsids).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Tier 1 — Lifestyle coaches (always included)
# ---------------------------------------------------------------------------

def build_lifestyle_block(domain: str) -> str:
    """
    Return genomic context for lifestyle coaches.
    Always included in domain reports. Magnitude >= 1.5, all reputes.
    Includes pinned key SNPs + top variants by magnitude.
    """
    pinned = _get_pinned(domain)
    top = _query_domain(domain, magnitude_min=1.5, limit=5)

    # merge, deduplicate, preserve order (pinned first)
    seen: set[str] = set()
    variants: list[dict] = []
    for v in pinned + top:
        if v["rsnum"] not in seen:
            seen.add(v["rsnum"])
            variants.append(v)

    if not variants:
        return ""

    lines = [f"\n🧬 Genomic context [{domain}] (lifestyle):"]
    for v in variants[:7]:
        lines.append(_format_variant(v, compact=True))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tier 2 — Specialists / GP (trigger-based)
# ---------------------------------------------------------------------------

TRIGGER_LABELS = {
    "persistent_trend": "устойчивый тренд",
    "new_hypothesis":   "новая гипотеза",
    "lab_deviation":    "отклонение в анализах",
}


def build_triggered_block(
    domain: str,
    trigger_type: str,
    detail: str = "",
) -> str:
    """
    Return genomic context for medical specialists / GP.
    Only call when a trigger condition is met.
    Shows Bad variants only, magnitude >= 2.0.

    Args:
        domain:       One of DOMAIN_GENES keys.
        trigger_type: persistent_trend | new_hypothesis | lab_deviation
        detail:       Human-readable description of what triggered this.
    """
    variants = _query_domain(domain, magnitude_min=2.0, limit=8, repute_filter="Bad")
    if not variants:
        return ""

    trigger_label = TRIGGER_LABELS.get(trigger_type, trigger_type)
    lines = [
        f"\n🧬 Genomic factors [{domain}] — triggered by: {trigger_label}",
    ]
    if detail:
        lines.append(f"   Context: {detail}")
    lines.append("   Adverse variants (magnitude ≥ 2.0):")
    for v in variants:
        lines.append(_format_variant(v, compact=False))
    return "\n".join(lines)


def has_actionable_variants(domain: str, magnitude_min: float = 2.0) -> bool:
    """Check if domain has any Bad variants above threshold. Use as gating check."""
    variants = _query_domain(domain, magnitude_min=magnitude_min, limit=1, repute_filter="Bad")
    return len(variants) > 0


def get_top_risks(magnitude_min: float = 3.0) -> str:
    """
    Return top Bad variants across all domains, for safety_net / GP reports.
    Magnitude >= 3.0 by default.
    """
    sql = """
        SELECT rsnum, geno, magnitude, repute, genes, genosummary, topic, numrefs
        FROM promethease_variants
        WHERE repute = 'Bad' AND magnitude >= ?
        ORDER BY magnitude DESC
        LIMIT 10
    """
    with get_conn() as conn:
        rows = conn.execute(sql, [magnitude_min]).fetchall()
    variants = [dict(r) for r in rows]

    if not variants:
        return ""

    lines = [f"\n🧬 Top genomic risk variants (magnitude ≥ {magnitude_min}):"]
    for v in variants:
        lines.append(_format_variant(v, compact=True))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tier 2 — GP weekly / monthly section
# ---------------------------------------------------------------------------

def build_weekly_monthly_section(
    active_domains: list[str],
    magnitude_min: float = 2.0,
) -> str:
    """
    Build genomic section for weekly/monthly GP reports.
    Only included if domain has actionable Bad variants.
    active_domains: list of domains with notable activity this period.
    """
    blocks = []
    for domain in active_domains:
        if not has_actionable_variants(domain, magnitude_min):
            continue
        variants = _query_domain(domain, magnitude_min=magnitude_min, limit=5, repute_filter="Bad")
        if not variants:
            continue
        block_lines = [f"\n  [{domain}] genomic risk factors:"]
        for v in variants:
            block_lines.append(_format_variant(v, compact=True))
        blocks.append("\n".join(block_lines))

    if not blocks:
        return ""

    return "\n🧬 Genomic context (domains with activity this period):" + "".join(blocks)


# ---------------------------------------------------------------------------
# Tier 3 — Constitution analysis (one-time per domain)
# ---------------------------------------------------------------------------

def build_constitution_block(domain: str) -> str:
    """
    Return full genomic profile for constitution analysis.
    One-time use per domain. All reputes, magnitude >= 1.5, up to 15 variants.
    Includes both protective and adverse variants for holistic picture.
    """
    variants = _query_domain(domain, magnitude_min=1.5, limit=15)
    if not variants:
        return ""

    good = [v for v in variants if v["repute"] == "Good"]
    bad  = [v for v in variants if v["repute"] == "Bad"]
    unk  = [v for v in variants if v["repute"] == "unknown"]

    lines = [f"\n🧬 Full genomic constitution [{domain}]:"]

    if bad:
        lines.append("  ⚠️  Adverse variants:")
        for v in bad:
            lines.append(_format_variant(v, compact=False))
    if good:
        lines.append("  ✅ Protective variants:")
        for v in good:
            lines.append(_format_variant(v, compact=False))
    if unk:
        lines.append("  ❓ Variants of uncertain significance:")
        for v in unk:
            lines.append(_format_variant(v, compact=True))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Import from Promethease HTML
# ---------------------------------------------------------------------------

def import_from_html(path: str) -> int:
    """
    Parse Promethease HTML report and populate promethease_variants table.
    Returns number of variants imported.

    Promethease encodes SNP data as zlib-compressed base64 (not gzip).
    The data blocks start with 'eJz' prefix in the HTML source.
    """
    import re
    import zlib
    import base64
    from datetime import datetime, timezone

    html = Path(path).read_text(encoding="utf-8", errors="replace")

    # Find all base64-encoded zlib blocks (start with eJz = zlib magic)
    # Promethease wraps them in decompressString('eJz...') — single or double quotes
    pattern = re.compile(r"""['\"](eJz[A-Za-z0-9+/=]{20,})['\"]""")
    matches = pattern.findall(html)

    all_variants: dict[str, dict] = {}
    imported_at = get_now(timezone.utc).isoformat()

    for match in matches:
        try:
            raw = base64.b64decode(match + "==")
            decompressed = zlib.decompress(raw).decode("utf-8", errors="replace")
            data = json.loads(decompressed)
        except Exception:
            continue

        # data may be a list of variant dicts or a single dict
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            rsnum = item.get("rsnum") or item.get("rs")
            if not rsnum or not rsnum.startswith("rs"):
                continue
            all_variants[rsnum] = item

    if not all_variants:
        print(f"No variants found in {path}", file=sys.stderr)
        return 0

    rows = []
    for rsnum, v in all_variants.items():
        genes_raw = v.get("genes", [])
        genes = ",".join(genes_raw) if isinstance(genes_raw, list) else str(genes_raw)
        rows.append((
            rsnum,
            str(v.get("geno", "")),
            float(v.get("magnitude", 0)),
            str(v.get("repute", "unknown")),
            genes,
            str(v.get("genosummary", "")),
            str(v.get("topic", "")),
            int(v.get("numrefs", 0)),
            json.dumps(v.get("clinvar_diseases", [])),
            None,   # domain_tags (computed separately)
            imported_at,
        ))

    with get_conn() as conn:
        conn.executemany("""
            INSERT OR REPLACE INTO promethease_variants
              (rsnum, geno, magnitude, repute, genes, genosummary,
               topic, numrefs, clinvar_diseases, domain_tags, imported_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, rows)

    print(f"Imported {len(rows)} variants from {path}")
    return len(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli():
    import argparse
    parser = argparse.ArgumentParser(description="Promethease genomic context tool")
    sub = parser.add_subparsers(dest="cmd")

    p_import = sub.add_parser("import", help="Import from Promethease HTML")
    p_import.add_argument("path", help="Path to promethease.html")

    p_top = sub.add_parser("top-risks", help="Show top risk variants")
    p_top.add_argument("--min-mag", type=float, default=3.0)

    p_life = sub.add_parser("lifestyle", help="Lifestyle coach block for domain")
    p_life.add_argument("domain", choices=list(DOMAIN_GENES.keys()))

    p_trig = sub.add_parser("triggered", help="Specialist triggered block")
    p_trig.add_argument("domain", choices=list(DOMAIN_GENES.keys()))
    p_trig.add_argument("trigger", choices=["persistent_trend", "new_hypothesis", "lab_deviation"])
    p_trig.add_argument("--detail", default="")

    p_const = sub.add_parser("constitution", help="Full constitution block")
    p_const.add_argument("domain", choices=list(DOMAIN_GENES.keys()))

    p_weekly = sub.add_parser("weekly", help="Weekly/monthly GP section")
    p_weekly.add_argument("domains", nargs="+", choices=list(DOMAIN_GENES.keys()))

    args = parser.parse_args()

    if args.cmd == "import":
        import_from_html(args.path)
    elif args.cmd == "top-risks":
        print(get_top_risks(args.min_mag) or "No high-risk variants found.")
    elif args.cmd == "lifestyle":
        print(build_lifestyle_block(args.domain) or "No variants found.")
    elif args.cmd == "triggered":
        print(build_triggered_block(args.domain, args.trigger, args.detail) or "No variants found.")
    elif args.cmd == "constitution":
        print(build_constitution_block(args.domain) or "No variants found.")
    elif args.cmd == "weekly":
        print(build_weekly_monthly_section(args.domains) or "No actionable variants found.")
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli()
