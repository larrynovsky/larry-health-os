"""prs_context.py — PRS context block for consilium injection (Wave 4).

Public interface (one module = one function rule):
    build_prs_block(conn=None) -> str

Sentinel patterns (WFR consistency, Таненбаум §7.3.5):
    SENTINEL_MISSING — prs_scores table absent or empty (Phase H not run)
    SENTINEL_STALE   — latest genome_import_id not in prs_scores

Coverage tiers:
    ≥70%  — полное покрытие (chip vs GWS score)
    40–69% — ограниченная точность ⚠️
    <40%  — недостаточно ⛔ (score не информативен)

Ancestry note:
    Non-European or mixed ancestry → European-trained models deviate 15–30%.
    Shown as inline warning; not suppressed — consilium participants must see it.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Optional

log = logging.getLogger(__name__)

SENTINEL_MISSING = (
    "[PRS MISSING: запусти genome_weights.py --pgs-id <ID>,"
    " затем vcf_import_pipeline.py --phase H]"
)
SENTINEL_STALE = (
    "[PRS STALE: геном переимпортирован — запусти vcf_import_pipeline.py --phase H]"
)

_ANCESTRY_WARNING = (
    "⚠️  Калибровка: модели PGS Catalog обучены преимущественно на европейской популяции. "
    "Для не-европейского или смешанного происхождения возможно отклонение 15–30%. "
    "Показатели — ориентировочные, не процентильные."
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_conn(conn: Optional[sqlite3.Connection] = None):
    if conn is not None:
        return conn, False
    import health_db
    return health_db.get_conn(), True


def _check_staleness(conn: sqlite3.Connection) -> bool:
    """True if latest genome import is not represented in prs_scores."""
    latest = conn.execute("SELECT MAX(id) FROM genome_imports").fetchone()[0]
    if latest is None:
        return False
    scored_ids = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT genome_import_id FROM prs_scores "
            "WHERE genome_import_id IS NOT NULL"
        ).fetchall()
    }
    return bool(scored_ids) and latest not in scored_ids


def _coverage_note(coverage: float) -> str:
    if coverage >= 70:
        return f"покрытие {coverage:.0f}%"
    if coverage >= 40:
        return f"покрытие {coverage:.0f}% ⚠️ (ограниченная точность)"
    return f"покрытие {coverage:.0f}% ⛔ (недостаточно для интерпретации)"


# ── Public entry point ───────────────────────────────────────────────────────

def build_prs_block(conn: Optional[sqlite3.Connection] = None) -> str:
    """Return formatted PRS block for consilium injection.

    Returns one of SENTINEL_MISSING, SENTINEL_STALE, or a multi-line text block.
    """
    _conn, owned = _get_conn(conn)
    try:
        return _build(_conn)
    finally:
        if owned:
            _conn.close()


def _build(conn: sqlite3.Connection) -> str:
    # 1. Table existence check
    has_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='prs_scores'"
    ).fetchone()
    if not has_table:
        return SENTINEL_MISSING

    # 2. Staleness check (WFR guard)
    if _check_staleness(conn):
        return SENTINEL_STALE

    # 3. Fetch latest scores
    scores = conn.execute(
        """
        SELECT ps.pgs_id, ps.trait_label, ps.raw_score,
               ps.snps_matched, ps.snps_total, ps.computed_at
        FROM   prs_scores ps
        WHERE  ps.genome_import_id = (SELECT MAX(id) FROM genome_imports)
        ORDER  BY ps.trait_label
        """
    ).fetchall()

    if not scores:
        return SENTINEL_MISSING

    lines = ["## ПОЛИГЕННЫЕ ИНДЕКСЫ РИСКА (Wave 4, Phase H)", ""]
    lines.append(_ANCESTRY_WARNING)
    lines.append("")

    for pgs_id, trait_label, raw_score, matched, total, computed_at in scores:
        coverage = matched / total * 100 if total > 0 else 0.0
        label = trait_label or pgs_id
        cov_note = _coverage_note(coverage)
        lines.append(f"**{label}** ({pgs_id})")
        lines.append(
            f"  Сырой балл: {raw_score:.5f}"
            f"  |  {matched:,}/{total:,} SNP"
            f"  |  {cov_note}"
        )
        lines.append("")

    ts = scores[0][5] if scores else "?"
    lines.append(f"_Вычислено: {ts[:10]}_")
    return "\n".join(lines)
