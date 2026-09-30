#!/usr/bin/env python3
"""genome_weights.py — Download and import PGS Catalog weight files into health.db.

Public interface (one module = one function rule):
    download_and_import(pgs_id, trait_name=None, conn=None) -> dict

Downloads the harmonized GRCh38 scoring file from EBI PGS Catalog,
parses header metadata + variant data, stores in pgs_catalog + pgs_weights.
Run once per PGS score; re-run to refresh.

Usage (Studio):
    python3.11 genome_weights.py --pgs-id PGS000036 --trait "Диабет 2 типа"
    python3.11 genome_weights.py --list   # show registered scores

Recommended scores for this profile (run these to populate Wave 4):
    PGS000036  — Type 2 Diabetes (Khera 2018, ~6.8M variants; chip coverage ~5%)
    PGS003867  — Coronary Artery Disease
    PGS002308  — BMI / Obesity risk
    PGS001243  — Late-onset Alzheimer's disease

⚠️  Ancestry calibration:
    Models predominantly trained on European cohorts. For non-European or
    mixed ancestry, expect 15–30% calibration deviation for some traits.
    Use scores as directional risk indicators, not population percentiles.
"""

from __future__ import annotations
from _time_inject import get_utcnow  # seam

import argparse
import gzip
import json
import logging
import sqlite3
import sys
from datetime import datetime
from typing import Optional

import requests

log = logging.getLogger(__name__)

# ── Tables ───────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS pgs_catalog (
    pgs_id         TEXT PRIMARY KEY,
    trait_name     TEXT NOT NULL,
    trait_label    TEXT,
    num_variants   INTEGER,
    ancestry_broad TEXT,
    genome_build   TEXT DEFAULT 'GRCh38',
    downloaded_at  TEXT NOT NULL,
    trait_efo_ids  TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS pgs_weights (
    id            INTEGER PRIMARY KEY,
    pgs_id        TEXT NOT NULL REFERENCES pgs_catalog(pgs_id) ON DELETE CASCADE,
    rsid          TEXT,
    chr_name      TEXT,
    chr_position  INTEGER,
    effect_allele TEXT NOT NULL,
    other_allele  TEXT,
    effect_weight REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pgs_weights_rsid ON pgs_weights (pgs_id, rsid);
"""
# NB: ix_pgs_weights_pos (chr+pos, ~1.1GB) убран 2026-07-02 — fallback-джойн по
# координатам в prs_pipeline не реализован (future). rsid-индекс достаточен.

PGS_API_URL  = "https://www.pgscatalog.org/rest/score/{pgs_id}"
EBI_FTP_TMPL = (
    "https://ftp.ebi.ac.uk/pub/databases/spot/pgs/scores/{pgs_id}"
    "/ScoringFiles/Harmonized/{pgs_id}_hmPOS_GRCh38.txt.gz"
)

# ── Internal helpers ─────────────────────────────────────────────────────────

def _get_conn(conn: Optional[sqlite3.Connection] = None):
    if conn is not None:
        return conn, False
    # 2026-07-02: pgs_catalog/pgs_weights вынесены в общую reference-БД
    # (не health.db) — писатель пишет туда напрямую.
    import pgs_reference
    return pgs_reference.get_ref_conn(), True


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    # 2026-06-27: safe migration — добавить trait_efo_ids если таблица уже существует без неё
    try:
        conn.execute("ALTER TABLE pgs_catalog ADD COLUMN trait_efo_ids TEXT NOT NULL DEFAULT '[]'")
    except Exception:  # silent-ok: колонка уже существует
        pass
    conn.commit()


def _parse_file_header(lines: list[str]) -> dict:
    """Extract key=value pairs from PGS Catalog file header lines (starting #)."""
    meta: dict[str, str] = {}
    for line in lines:
        if not line.startswith("#"):
            break
        if "=" in line:
            key, _, val = line[1:].partition("=")
            meta[key.strip()] = val.strip()
    return meta


def _get_col(row: list[str], col_idx: dict[str, int], name: str,
             default: Optional[str] = None) -> Optional[str]:
    i = col_idx.get(name)
    if i is None or i >= len(row):
        return default
    v = row[i].strip()
    return v if v else default


# ── Public entry point ───────────────────────────────────────────────────────

def download_and_import(
    pgs_id: str,
    trait_name: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """Download harmonised GRCh38 PGS scoring file and import into health.db.

    Returns:
        {status, pgs_id, n_inserted, n_skipped, ancestry, num_variants}
        status: 'ok' | 'api_error' | 'download_error' | 'empty_file'
    """
    pgs_id = pgs_id.upper()
    _conn, owned = _get_conn(conn)
    _ensure_tables(_conn)

    # ── Step 1: metadata from PGS Catalog REST API ───────────────────────────
    log.info("PGS API: fetching metadata for %s", pgs_id)
    try:
        resp = requests.get(PGS_API_URL.format(pgs_id=pgs_id), timeout=30)
        resp.raise_for_status()
        api_meta = resp.json()
    except Exception as exc:
        log.error("PGS API error %s: %s", pgs_id, exc)
        if owned:
            _conn.close()
        return {"status": "api_error", "pgs_id": pgs_id, "error": str(exc)}

    reported_trait = trait_name or api_meta.get("trait_reported", pgs_id)

    # EFO IDs из API (2026-06-27: Block D2)
    efo_ids_json = json.dumps([e["id"] for e in (api_meta.get("trait_efo") or []) if "id" in e])

    # Ancestry from evaluation samples (best available signal)
    anc_dist = (api_meta.get("ancestry_distribution") or {})
    eval_anc = (anc_dist.get("eval") or anc_dist.get("dev") or {})
    anc_items = list((eval_anc.get("dist") or {}).items())[:3]
    ancestry_str = ",".join(f"{k}:{v}" for k, v in anc_items) or "unknown"

    # Prefer harmonized GRCh38 URL from API; fall back to EBI template
    ftp_harm = api_meta.get("ftp_harmonized_scoring_files") or {}
    grch38_url = (
        (ftp_harm.get("GRCh38") or {}).get("positions")
        or EBI_FTP_TMPL.format(pgs_id=pgs_id)
    )

    # ── Step 2: download + decompress ────────────────────────────────────────
    log.info("Downloading %s from %s", pgs_id, grch38_url)
    try:
        r2 = requests.get(grch38_url, timeout=300, stream=True)
        r2.raise_for_status()
        raw_bytes = gzip.decompress(r2.content)
    except Exception as exc:
        log.error("Download error %s: %s", pgs_id, exc)
        if owned:
            _conn.close()
        return {"status": "download_error", "pgs_id": pgs_id, "error": str(exc)}

    all_lines = raw_bytes.decode("utf-8").splitlines()

    # ── Step 3: parse header ─────────────────────────────────────────────────
    header_lines = [l for l in all_lines if l.startswith("#")]
    file_meta    = _parse_file_header(header_lines)
    # PGS Catalog header uses "variants_number" (not "Number_of_Variants")
    num_variants = int(file_meta.get("variants_number", 0))

    # ── Step 4: find data section ────────────────────────────────────────────
    data_lines = [l for l in all_lines if not l.startswith("#") and l.strip()]
    if not data_lines:
        log.error("PGS %s: empty scoring file", pgs_id)
        if owned:
            _conn.close()
        return {"status": "empty_file", "pgs_id": pgs_id}

    col_headers = data_lines[0].split("\t")
    col_idx = {c.strip(): i for i, c in enumerate(col_headers)}

    # ── Step 5: register in pgs_catalog ─────────────────────────────────────
    _conn.execute(
        "INSERT OR REPLACE INTO pgs_catalog "
        "(pgs_id, trait_name, trait_label, num_variants, ancestry_broad, downloaded_at, trait_efo_ids) "
        "VALUES (?,?,?,?,?,?,?)",
        (pgs_id, reported_trait, reported_trait,
         num_variants or len(data_lines) - 1,
         ancestry_str, get_utcnow().isoformat(), efo_ids_json),
    )
    _conn.execute("DELETE FROM pgs_weights WHERE pgs_id=?", (pgs_id,))
    _conn.commit()

    # ── Step 6: bulk-insert weights ──────────────────────────────────────────
    n_inserted = 0
    n_skipped  = 0
    batch: list[tuple] = []
    BATCH_SZ = 5000

    def _flush(b: list[tuple]) -> int:
        if not b:
            return 0
        # Use total_changes delta — SELECT changes() only counts the last row
        # of executemany(), not the whole batch.
        before = _conn.total_changes
        _conn.executemany(
            "INSERT OR IGNORE INTO pgs_weights "
            "(pgs_id,rsid,chr_name,chr_position,effect_allele,other_allele,effect_weight) "
            "VALUES (?,?,?,?,?,?,?)",
            b,
        )
        inserted = _conn.total_changes - before
        _conn.commit()
        return inserted

    for raw in data_lines[1:]:
        row = raw.split("\t")
        rsid = _get_col(row, col_idx, "rsID") or _get_col(row, col_idx, "rsid")

        # Prefer GRCh38 harmonized coordinates (hm_chr / hm_pos) over
        # original GRCh37 coordinates (chr_name / chr_position)
        chr_name    = _get_col(row, col_idx, "hm_chr") or _get_col(row, col_idx, "chr_name")
        chr_pos_str = _get_col(row, col_idx, "hm_pos") or _get_col(row, col_idx, "chr_position")

        effect_allele = _get_col(row, col_idx, "effect_allele")
        # Harmonized files use hm_inferOtherAllele instead of other_allele
        other_allele = (
            _get_col(row, col_idx, "other_allele")
            or _get_col(row, col_idx, "hm_inferOtherAllele")
        )
        weight_str = _get_col(row, col_idx, "effect_weight")

        if not effect_allele or not weight_str:
            n_skipped += 1
            continue
        try:
            chr_pos = int(chr_pos_str) if chr_pos_str else None
            weight  = float(weight_str)
        except (ValueError, TypeError):
            n_skipped += 1
            continue

        # Normalise: discard non-rs IDs, uppercase alleles
        if rsid and not rsid.startswith("rs"):
            rsid = None

        # other_allele may be multi-allelic (e.g. "G/T") — store as-is; only
        # the effect_allele matters for dosage counting.
        batch.append((
            pgs_id, rsid, chr_name, chr_pos,
            effect_allele.upper(),
            (other_allele if other_allele else None),
            weight,
        ))
        if len(batch) >= BATCH_SZ:
            n_inserted += _flush(batch)
            batch.clear()

    n_inserted += _flush(batch)

    log.info("PGS %s: %d inserted, %d skipped", pgs_id, n_inserted, n_skipped)
    if owned:
        _conn.close()

    return {
        "status": "ok",
        "pgs_id": pgs_id,
        "n_inserted": n_inserted,
        "n_skipped": n_skipped,
        "ancestry": ancestry_str,
        "num_variants": num_variants,
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(
        description="Download PGS Catalog weights → health.db"
    )
    ap.add_argument("--pgs-id", help="PGS Catalog ID, e.g. PGS000036")
    ap.add_argument("--trait",  help="Human-readable trait label (optional)")
    ap.add_argument("--list",   action="store_true", help="List registered scores")
    args = ap.parse_args()

    import pgs_reference
    _c = pgs_reference.get_ref_conn()

    if args.list:
        try:
            rows = _c.execute(
                "SELECT pgs_id, trait_label, num_variants, ancestry_broad, downloaded_at "
                "FROM pgs_catalog ORDER BY downloaded_at DESC"
            ).fetchall()
        except Exception:
            rows = []
        if rows:
            print(f"{'PGS ID':<12} {'Trait':<30} {'Variants':>10}  Downloaded")
            for r in rows:
                print(f"{r[0]:<12} {(r[1] or ''):<30} {str(r[2] or '?'):>10}  {(r[4] or '')[:10]}")
        else:
            print("Нет зарегистрированных PGS. Запусти с --pgs-id.")
    elif args.pgs_id:
        result = download_and_import(args.pgs_id, trait_name=args.trait, conn=_c)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        ap.print_help()

    _c.close()
