"""wellness_context.py — Wellness SNP text block for consilium injection.

Public interface (one module = one function rule):
    build_wellness_block(conn=None) -> str

Args:
    conn: sqlite3.Connection (optional; opens own connection if None)

Returns:
    Formatted multi-line string ready for LLM prompt injection.
    Special return values:
        '[WELLNESS MISSING: run vcf_import_pipeline.py --phase G first]'
        '[WELLNESS STALE: genome reimported since wellness computation]'

Staleness check (WFR guard, Таненбаум §7.3.5):
    Same pattern as pharmaco_context and traits_context.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel strings
# ---------------------------------------------------------------------------

SENTINEL_MISSING = "[WELLNESS MISSING: run vcf_import_pipeline.py --phase G first]"
SENTINEL_STALE   = "[WELLNESS STALE: genome reimported since wellness computation]"

# ---------------------------------------------------------------------------
# Implication / action templates
# ---------------------------------------------------------------------------

# (gene, trait, phenotype_substring) → (icon, action_note)
_IMPLICATIONS: list[tuple[str, str, str, str, str]] = [
    # MTHFR
    ("MTHFR", "folate_metabolism", "Severely reduced",
     "⚠️", "L-метилфолат + метилкобаламин обязательны; контроль гомоцистеина."),
    ("MTHFR", "folate_metabolism", "compound heterozygous",
     "⚡", "L-метилфолат предпочтительнее фолиевой кислоты; мониторинг гомоцистеина."),
    ("MTHFR", "folate_metabolism", "homozygous",
     "⚡", "L-метилфолат предпочтительнее фолиевой кислоты; контроль гомоцистеина."),
    ("MTHFR", "folate_metabolism", "Mildly reduced",
     "ℹ️", "Предпочтительна активная форма фолата при дефиците группы B."),

    # COMT
    ("COMT", "dopamine_metabolism", "Low COMT",
     "ℹ️", "Повышенная чувствительность к стрессу; ограничение высоких доз кофеина."),
    ("COMT", "dopamine_metabolism", "Intermediate",
     "ℹ️", "Сбалансированный дофаминовый тонус."),
    ("COMT", "dopamine_metabolism", "High COMT",
     "ℹ️", "Высокая стрессоустойчивость; допаминовый тонус ниже в покое."),

    # FTO
    ("FTO", "obesity_risk", "Elevated",
     "ℹ️", "Физическая активность нейтрализует FTO-эффект; аэробная нагрузка приоритетна."),
    ("FTO", "obesity_risk", "Modest",
     "ℹ️", "Умеренный FTO-риск; образ жизни важнее генетики."),

    # HFE
    ("HFE", "iron_overload_risk", "homozygous",
     "⚡", "Контроль ферритина + насыщения трансферрина; исключить ненужные Fe-препараты."),
    ("HFE", "iron_overload_risk", "heterozygous carrier",
     "ℹ️", "Носитель H63D; при повышенном ферритине — проверить сатурацию трансферрина."),
]


def _get_implication(gene: str, trait: str, phenotype: str) -> tuple[str, str] | None:
    """Return (icon, action_note) for the best-matching implication."""
    for g, t, pheno_sub, icon, note in _IMPLICATIONS:
        if g == gene and t == trait and pheno_sub.lower() in phenotype.lower():
            return icon, note
    return None


# ---------------------------------------------------------------------------
# Staleness check
# ---------------------------------------------------------------------------

def _check_staleness(conn) -> bool:
    try:
        latest = conn.execute("SELECT MAX(id) FROM genome_imports").fetchone()[0]
        if latest is None:
            return False
        stored = conn.execute(
            "SELECT MIN(genome_import_id) FROM wellness_phenotypes"
        ).fetchone()[0]
        if stored is None:
            return False
        return int(latest) != int(stored)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_wellness_block(conn=None) -> str:
    """Build formatted wellness text block for consilium injection."""
    _own_conn = conn is None
    if _own_conn:
        from health_db import get_conn
        conn = get_conn()

    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wellness_phenotypes'"
        ).fetchone()
        if not has_table:
            return SENTINEL_MISSING

        row_count = conn.execute(
            "SELECT COUNT(*) FROM wellness_phenotypes"
        ).fetchone()[0]
        if row_count == 0:
            return SENTINEL_MISSING

        if _check_staleness(conn):
            log.warning("wellness_context: stale data detected")
            return SENTINEL_STALE

        rows = conn.execute(
            """
            SELECT gene, trait, phenotype, confidence, notes, computed_at
            FROM wellness_phenotypes
            ORDER BY gene, trait
            """
        ).fetchall()

        lines: list[str] = ["=== WELLNESS ГЕНОМИКА (Phase G) ==="]

        warn_lines: list[str] = []
        info_lines: list[str] = []

        for row in rows:
            gene, trait, phenotype, confidence, notes, computed_at = row
            trait_label = trait.replace("_", " ").title()
            conf_note = " (probabilistic)" if confidence == "medium" else ""

            impl = _get_implication(gene, trait, phenotype)
            icon = impl[0] if impl else "ℹ️"
            action = impl[1] if impl else ""

            gene_line = f"  {gene} / {trait_label}: {phenotype}{conf_note}"
            action_line = f"    {icon} {action}" if action else ""

            if icon == "⚠️":
                warn_lines.append(gene_line)
                if action_line:
                    warn_lines.append(action_line)
            else:
                info_lines.append(gene_line)
                if action_line:
                    info_lines.append(action_line)

        if warn_lines:
            lines.append("\n⚠️ ТРЕБУЮТ ВНИМАНИЯ:")
            lines.extend(warn_lines)

        if info_lines:
            lines.append("\nℹ️ ИНФОРМАЦИЯ:")
            lines.extend(info_lines)

        if rows:
            computed_at = rows[0][5]
            lines.append(f"\nДанные рассчитаны: {computed_at} UTC")

        return "\n".join(lines)

    finally:
        if _own_conn:
            conn.close()
