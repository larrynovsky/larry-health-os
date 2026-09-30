"""prs_pipeline.py — Polygenic Risk Score computation (Wave 4, Phase H).

Public interface (one module = one function rule):
    run(conn, genome_import_id) -> dict

For each score registered in pgs_catalog, computes:
    raw_score = Σ (effect_weight × allele_copies)

where allele_copies = count of effect_allele in the 2-char diploid genotype.

Matching strategy:
    Primary: pgs_weights.rsid → raw_snps.rsid (fast index join, ~95% coverage
             for chip-genotyped common variants with rsIDs)
    Fallback (future): chr_name + chr_position join if rsid absent in weight file

Missing SNPs are skipped — score is computed over matched subset only.
Coverage (snps_matched / snps_total) is reported to detect under-coverage.

Write-through (Таненбаум §7.5.4):
    Stamps genome_imports.phase_h_prs_at after successful run().

R1 guard:
    Missing or indeterminate genotype → skipped, never substituted.

Ancestry warning:
    PGS Catalog models are predominantly European-trained.
    For non-European or mixed ancestry, expect 15–30% calibration deviation.

No-weights state:
    If pgs_catalog is empty or missing → status='no_weights'.
    Run: python3.11 genome_weights.py --pgs-id PGS000036
"""

from __future__ import annotations
from _time_inject import get_utcnow  # seam

import logging
import sqlite3
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

# ── DDL ──────────────────────────────────────────────────────────────────────

_DDL_PRS_SCORES = """
CREATE TABLE IF NOT EXISTS prs_scores (
    id               INTEGER PRIMARY KEY,
    genome_import_id INTEGER NOT NULL,
    pgs_id           TEXT    NOT NULL,
    trait_label      TEXT,
    raw_score        REAL    NOT NULL,
    snps_matched     INTEGER NOT NULL,
    snps_total       INTEGER NOT NULL,
    computed_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(genome_import_id, pgs_id)
);
"""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL_PRS_SCORES)
    # Migration: add phase_h_prs_at to genome_imports if absent
    cols = {r[1] for r in conn.execute("PRAGMA table_info(genome_imports)").fetchall()}
    if "phase_h_prs_at" not in cols:
        conn.execute("ALTER TABLE genome_imports ADD COLUMN phase_h_prs_at TEXT")
    conn.commit()


def _count_allele(genotype: Optional[str], allele: str) -> int:
    """Count copies of allele in 2-char diploid string.

    Returns -1 if genotype is None or not exactly 2 characters (indeterminate).
    """
    if not genotype or len(genotype) != 2:
        return -1
    return genotype.count(allele)


def _weights_source(conn: sqlite3.Connection) -> str:
    """Откуда читать веса: '' — локально в этом соединении (тест/legacy до миграции),
    'pgsref.' — из общей reference-БД (ATTACH). pgs_weights вынесены из health.db
    2026-07-02; raw_snps остаётся в каноне → JOIN кросс-БД через ATTACH."""
    try:
        # ЯВНО main.* — иначе после ATTACH неквалифицированное имя резолвится в
        # pgsref.pgs_weights (main пуст) и повторный вызов ложно решает «локально».
        if conn.execute("SELECT COUNT(*) FROM main.pgs_weights").fetchone()[0] > 0:
            return ""  # локальные веса присутствуют — не аттачим (backward-compat)
    except sqlite3.OperationalError:
        pass           # таблицы нет в main → веса в reference
    import pgs_reference
    pgs_reference.attach(conn)
    return "pgsref."


def unscored_models(conn: sqlite3.Connection, genome_import_id: int) -> list[str]:
    """Модели справочника, для которых у ЭТОГО генома ещё нет балла в prs_scores.

    Справочник весов общий для всех тенантов, а баллы — у каждого свои. Наличие модели
    в общем справочнике не означает, что она уже рассчитана для данного генома.
    Поэтому список отвечает на вопрос «что не посчитано у меня», а не «что нового скачано»."""
    _ensure_tables(conn)
    src = _weights_source(conn)
    schema = src[:-1] if src else "main"
    if not conn.execute(f"SELECT name FROM {schema}.sqlite_master "
                        "WHERE type='table' AND name='pgs_catalog'").fetchone():
        return []
    have = {r[0] for r in conn.execute(
        "SELECT pgs_id FROM prs_scores WHERE genome_import_id=?", (genome_import_id,))}
    return sorted(r[0] for r in conn.execute(f"SELECT pgs_id FROM {src}pgs_catalog")
                  if r[0] not in have)


def _compute_score(
    conn: sqlite3.Connection, pgs_id: str, wtbl: str = "pgs_weights"
) -> dict:
    """Compute raw PRS for one registered score.

    Returns dict: raw_score (float), snps_matched (int), snps_total (int).
    wtbl — квалифицированное имя таблицы весов ('pgs_weights' локально либо
    'pgsref.pgs_weights' при ATTACH reference-БД).
    """
    snps_total = conn.execute(
        f"SELECT COUNT(*) FROM {wtbl} WHERE pgs_id=?", (pgs_id,)
    ).fetchone()[0]

    if snps_total == 0:
        return {"raw_score": 0.0, "snps_matched": 0, "snps_total": 0}

    # rsid-join: the fast path (works for chip data with rsIDs in both tables).
    # raw_snps — в main (персональный геном); wtbl может быть в приаттаченной
    # reference-БД — SQLite джойнит через границу БД в одном запросе.
    rows = conn.execute(
        f"""
        SELECT w.effect_allele, w.effect_weight, s.genotype
        FROM   {wtbl} w
        JOIN   raw_snps    s ON s.rsid = w.rsid
        WHERE  w.pgs_id = ?
          AND  w.rsid IS NOT NULL
          AND  s.genotype IS NOT NULL
        """,
        (pgs_id,),
    ).fetchall()

    raw_score    = 0.0
    snps_matched = 0

    for effect_allele, weight, genotype in rows:
        count = _count_allele(genotype, effect_allele)
        if count < 0:
            continue  # R1: skip indeterminate, never substitute
        raw_score    += weight * count
        snps_matched += 1

    return {
        "raw_score":    raw_score,
        "snps_matched": snps_matched,
        "snps_total":   snps_total,
    }


# ── Public entry point ───────────────────────────────────────────────────────

def run(conn: sqlite3.Connection, genome_import_id: int) -> dict:
    """Compute PRS for all registered PGS scores and store results.

    Args:
        conn:             Open SQLite connection (canonical health.db).
        genome_import_id: ID of the genome_imports row being processed.

    Returns:
        {
            status:    'ok' | 'no_weights' | 'partial',
            results:   {pgs_id: {trait_label, raw_score, snps_matched,
                                  snps_total, coverage_pct}},
            failed:    [pgs_id, ...],
        }
    """
    _ensure_tables(conn)

    # Источник весов: локально (тест/legacy) или reference-БД (ATTACH pgsref).
    src = _weights_source(conn)            # '' | 'pgsref.'
    schema = src[:-1] if src else "main"   # 'pgsref' | 'main'

    # Guard: is pgs_catalog table present and populated?
    has_catalog = conn.execute(
        f"SELECT name FROM {schema}.sqlite_master WHERE type='table' AND name='pgs_catalog'"
    ).fetchone()
    if not has_catalog:
        log.info("Phase H: pgs_catalog missing — run genome_weights.py first")
        return {"status": "no_weights", "results": {}, "failed": []}

    registered = conn.execute(
        f"SELECT pgs_id, trait_label FROM {src}pgs_catalog"
    ).fetchall()

    if not registered:
        log.info("Phase H: no PGS scores registered — run genome_weights.py first")
        return {"status": "no_weights", "results": {}, "failed": []}

    results: dict[str, dict] = {}
    failed:  list[str]       = []

    for pgs_id, trait_label in registered:
        log.info("Phase H: computing %s (%s)", pgs_id, trait_label)
        try:
            score_data = _compute_score(conn, pgs_id, f"{src}pgs_weights")
        except Exception as exc:
            log.error("Phase H: %s failed: %s", pgs_id, exc)
            failed.append(pgs_id)
            continue

        snps_total   = score_data["snps_total"]
        snps_matched = score_data["snps_matched"]
        coverage     = (snps_matched / snps_total * 100) if snps_total > 0 else 0.0

        conn.execute(
            """
            INSERT OR REPLACE INTO prs_scores
                (genome_import_id, pgs_id, trait_label,
                 raw_score, snps_matched, snps_total)
            VALUES (?,?,?,?,?,?)
            """,
            (genome_import_id, pgs_id, trait_label or pgs_id,
             score_data["raw_score"], snps_matched, snps_total),
        )
        conn.commit()

        results[pgs_id] = {
            "trait_label":  trait_label,
            "raw_score":    round(score_data["raw_score"], 6),
            "snps_matched": snps_matched,
            "snps_total":   snps_total,
            "coverage_pct": round(coverage, 1),
        }
        log.info(
            "  %s: score=%.5f  coverage=%.1f%%  matched=%d/%d",
            pgs_id, score_data["raw_score"], coverage, snps_matched, snps_total,
        )

    # Write-through stamp (Таненбаум §7.5.4)
    now = get_utcnow().isoformat()
    conn.execute(
        "UPDATE genome_imports SET phase_h_prs_at=? WHERE id=?",
        (now, genome_import_id),
    )
    conn.commit()

    status = "ok" if not failed else "partial"
    log.info(
        "Phase H done: %s | %d scores | %d failed",
        status, len(results), len(failed),
    )
    return {"status": status, "results": results, "failed": failed}
