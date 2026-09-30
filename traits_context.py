"""traits_context.py — Deterministic trait text block for consilium injection.

Public interface (one module = one function rule):
    build_traits_block(conn=None) -> str

Args:
    conn: sqlite3.Connection (optional; opens own connection if None)

Returns:
    Formatted multi-line string ready for LLM prompt injection.
    Special return values:
        '[TRAITS MISSING: run vcf_import_pipeline.py --phase F first]'
            — no rows in trait_phenotypes
        '[TRAITS STALE: genome reimported since trait computation]'
            — latest genome_imports.id != genome_import_id in trait_phenotypes

Staleness check (WFR guard, Таненбаум §7.3.5):
    If a new VCF was imported after traits were computed, the block is stale.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel strings
# ---------------------------------------------------------------------------

SENTINEL_MISSING = "[TRAITS MISSING: run vcf_import_pipeline.py --phase F first]"
SENTINEL_STALE   = "[TRAITS STALE: genome reimported since trait computation]"

# ---------------------------------------------------------------------------
# Category labels
# ---------------------------------------------------------------------------

_CATEGORY_LABELS = {
    "nutrition":     "🥗 ПИТАНИЕ",
    "appearance":    "👁 ВНЕШНОСТЬ",
    "disease_risk":  "⚕️ ГЕНЕТИЧЕСКИЙ РИСК",
}

_CONFIDENCE_NOTES = {
    "high":          "",
    "medium":        " (probabilistic)",
    "indeterminate": " (indeterminate)",
}

# ---------------------------------------------------------------------------
# Staleness check
# ---------------------------------------------------------------------------

def _check_staleness(conn) -> bool:
    """Return True if trait_phenotypes is out of date vs genome_imports."""
    try:
        latest_import = conn.execute(
            "SELECT MAX(id) FROM genome_imports"
        ).fetchone()[0]
        if latest_import is None:
            return False
        trait_import = conn.execute(
            "SELECT MIN(genome_import_id) FROM trait_phenotypes"
        ).fetchone()[0]
        if trait_import is None:
            return False
        return int(latest_import) != int(trait_import)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_traits_block(conn=None) -> str:
    """Build a formatted traits text block for consilium injection.

    Args:
        conn: sqlite3.Connection; if None, opens own connection via health_db.get_conn()

    Returns:
        Multi-line string, or SENTINEL_* string.
    """
    _own_conn = conn is None
    if _own_conn:
        from health_db import get_conn
        conn = get_conn()

    try:
        # Check table exists and has data
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trait_phenotypes'"
        ).fetchone()
        if not has_table:
            return SENTINEL_MISSING

        row_count = conn.execute(
            "SELECT COUNT(*) FROM trait_phenotypes"
        ).fetchone()[0]
        if row_count == 0:
            return SENTINEL_MISSING

        # Staleness check (WFR guard)
        if _check_staleness(conn):
            log.warning("traits_context: stale data detected")
            return SENTINEL_STALE

        # Fetch all trait rows
        rows = conn.execute(
            """
            SELECT trait, category, phenotype, confidence, notes, computed_at
            FROM trait_phenotypes
            ORDER BY category, trait
            """
        ).fetchall()

        lines: list[str] = ["=== ДЕТЕРМИНИРОВАННЫЕ ЧЕРТЫ (Phase F) ==="]

        by_category: dict[str, list] = {}
        for row in rows:
            trait, category, phenotype, confidence, notes, computed_at = row
            by_category.setdefault(category, []).append(row)

        for category in ["nutrition", "disease_risk", "appearance"]:
            if category not in by_category:
                continue
            label = _CATEGORY_LABELS.get(category, category.upper())
            lines.append(f"\n{label}:")
            for row in by_category[category]:
                trait, cat, phenotype, confidence, notes, computed_at = row
                conf_note = _CONFIDENCE_NOTES.get(confidence, "")
                trait_label = trait.replace("_", " ").title()
                lines.append(f"  {trait_label}: {phenotype}{conf_note}")
                if notes and confidence != "indeterminate":
                    # Truncate long notes for consilium (keep first ~200 chars)
                    short_note = notes[:220].rstrip()
                    if len(notes) > 220:
                        short_note += "…"
                    lines.append(f"    {short_note}")

        if rows:
            computed_at = rows[0][5]
            lines.append(f"\nДанные рассчитаны: {computed_at} UTC")

        return "\n".join(lines)

    finally:
        if _own_conn:
            conn.close()
