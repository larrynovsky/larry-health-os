"""
genome_pipeline.py — идемпотентный tenant-safe оркестратор геном-стека тенанта.

Сшивает СУЩЕСТВУЮЩИЕ скрипты в фиксированном порядке, ничего не переписывая.
Появился после инцидента 2026-07-04: геном-стек гонялся вручную, и конституции
были сгенерированы ДО backfill_effect_alleles → вышли полыми (Bad=0/Good=0).

Порядок стадий (менять нельзя):
    S1 import_raw_genome        → raw_snps
    S2 annotate_all             → genetic_variants (ClinVar/myvariant)
    S3 backfill_effect_alleles  → effect_allele + status
    S4 fix_palindromic_het      → палиндромные гетерозиготы
    S5 prs (prs_pipeline)       → prs_scores        (независим от S2–S4)
    S6 constitutions            → constitutions     ← HARD-GATE: нужен S3
    S7 verify                   → сводка

Идемпотентность: «сделано ли» определяется ФАКТИЧЕСКИМ состоянием БД (счётчики),
а НЕ хранимым флагом — чтобы не завести stale/split-brain слой прогресса
(см. feedback_stale_intermediate_layer). Повторный запуск дозаполняет пропущенное.

Tenant-safety: все стадии работают через db.DB_PATH (HEALTH_DATA_DIR); никаких
owner-хардкодов. Запускать под явным env тенанта.

CLI:
    HEALTH_DATA_DIR=~/health_partner HEALTH_SECRETS_DIR=~/.health_secrets_partner \\
    python3.11 genome_pipeline.py --file /path/to/23andme.txt
    [--from S3] [--to S6] [--force-import] [--dry] [--notify]
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import health_db as db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("genome_pipeline")

DOMAINS_DONE_TARGET = 5  # число доменов конституций (см. generate_constitutions.DOMAINS)


# ── data-маркеры (источник правды об idempotency) ────────────────────────────
def _count(sql: str) -> int:
    with db.get_conn() as c:
        return c.execute(sql).fetchone()[0]


def _n_raw():        return _count("SELECT COUNT(*) FROM raw_snps")
def _n_annotated():  return _count("SELECT COUNT(*) FROM genetic_variants")
def _n_resolved():   return _count("SELECT COUNT(*) FROM genetic_variants WHERE effect_allele_status='resolved'")
def _n_prs():        return _count("SELECT COUNT(*) FROM prs_scores")
def _n_constitutions(): return _count("SELECT COUNT(*) FROM constitutions")


# ── стадии ────────────────────────────────────────────────────────────────────
def s1_import(filepath, force_import, **_):
    import genome_parser
    if _n_raw() > 0 and not force_import:
        log.info("S1 skip: raw_snps=%d уже есть", _n_raw())
        return
    if not filepath:
        raise ValueError("S1: нужен --file (путь к 23andMe .txt)")
    genome_parser.import_raw_genome(Path(filepath), force=force_import)


def s2_annotate(**_):
    import genome_annotator
    if _n_annotated() > 0:
        log.info("S2 skip: genetic_variants=%d уже есть", _n_annotated())
        return
    genome_annotator.annotate_all()


def s3_effect_allele(**_):
    import backfill_effect_alleles
    if _n_resolved() > 0:
        log.info("S3 skip: resolved effect_allele=%d уже есть", _n_resolved())
        return
    if _n_annotated() == 0:
        raise RuntimeError("S3 abort: genetic_variants пусты — сначала S2 annotate")
    backfill_effect_alleles.run()


def s4_palindromic(**_):
    import fix_palindromic_het
    if _n_annotated() == 0:
        log.info("S4 skip: нет genetic_variants")
        return
    fix_palindromic_het.run()


def s5_prs(**_):
    import sqlite3
    import prs_pipeline
    if _n_prs() > 0:
        log.info("S5 skip: prs_scores=%d уже есть", _n_prs())
        return
    conn = sqlite3.connect(db.DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=120000")
    gid = conn.execute("SELECT MAX(id) FROM genome_imports").fetchone()[0]
    if gid is None:
        cur = conn.execute("INSERT INTO genome_imports (vcf_source) VALUES (?)",
                           ("genome_pipeline",))
        gid = cur.lastrowid
        conn.commit()
    res = prs_pipeline.run(conn, gid)
    conn.commit()
    conn.close()
    log.info("S5 PRS: status=%s scores=%d", res.get("status"), len(res.get("results", {})))


def s6_constitutions(notify=False, regen=False, **_):
    import generate_constitutions as gc
    if not regen and _n_constitutions() >= len(gc.DOMAINS):
        log.info("S6 skip: конституций=%d уже есть (--regen для перегенерации)",
                 _n_constitutions())
        return
    # HARD-GATE (урок 2026-07-04): без resolved effect_allele конституции полые.
    if _n_resolved() == 0:
        raise RuntimeError(
            "S6 abort: effect_allele resolved==0 — конституции вышли бы полыми "
            "(Bad=0/Good=0). Сначала выполни S3 backfill_effect_alleles.")
    if not notify:
        gc._tg_notify = lambda *a, **k: True  # оператор-прогон: не слать тенанту
    try:
        from profile_reconciler import reconcile
        reconcile()
    except Exception as e:
        log.warning("profile_reconciler skip: %r", e)
    targets = list(gc.DOMAINS.keys())
    for d in targets:
        t = gc._generate_one(d)
        if t:
            gc._save(d, t)
        log.info("S6 domain %s: %d симв", d, len(t) if t else 0)
    try:
        gc._run_alert_review(targets)
    except Exception as e:
        log.warning("alert_review skip: %r", e)


def s7_verify(**_):
    summary = {
        "raw_snps": _n_raw(),
        "genetic_variants": _n_annotated(),
        "effect_allele_resolved": _n_resolved(),
        "prs_scores": _n_prs(),
        "constitutions": _n_constitutions(),
        "db_path": str(db.DB_PATH),
    }
    hollow = summary["constitutions"] > 0 and summary["effect_allele_resolved"] == 0
    summary["hollow_constitutions"] = hollow
    log.info("S7 verify: %s", summary)
    if hollow:
        raise RuntimeError("S7: конституции на пустом effect_allele — ПОЛЫЕ. Перегенерируй после S3.")
    return summary


STAGES = [
    ("S1", "import", s1_import),
    ("S2", "annotate", s2_annotate),
    ("S3", "effect_allele", s3_effect_allele),
    ("S4", "palindromic", s4_palindromic),
    ("S5", "prs", s5_prs),
    ("S6", "constitutions", s6_constitutions),
    ("S7", "verify", s7_verify),
]
_ORDER = [s[0] for s in STAGES]


def run_pipeline(filepath=None, force_import=False, notify=False, regen=False,
                 from_stage=None, to_stage=None, dry=False) -> dict:
    lo = _ORDER.index(from_stage) if from_stage else 0
    hi = _ORDER.index(to_stage) if to_stage else len(STAGES) - 1
    log.info("genome_pipeline старт: DB=%s стадии %s..%s dry=%s",
             db.DB_PATH, _ORDER[lo], _ORDER[hi], dry)
    for i, (sid, name, fn) in enumerate(STAGES):
        if i < lo or i > hi:
            continue
        log.info("═══ %s %s ═══", sid, name)
        if dry:
            log.info("  dry: пропуск исполнения")
            continue
        fn(filepath=filepath, force_import=force_import, notify=notify, regen=regen)
    return s7_verify() if (not dry and hi == len(STAGES) - 1) else {}


def main():
    ap = argparse.ArgumentParser(description="Оркестратор геном-стека тенанта")
    ap.add_argument("--file", help="путь к raw 23andMe .txt (для S1)")
    ap.add_argument("--from", dest="from_stage", choices=_ORDER, help="начать со стадии")
    ap.add_argument("--to", dest="to_stage", choices=_ORDER, help="закончить стадией")
    ap.add_argument("--force-import", action="store_true", help="перезаписать raw_snps (identity-guard действует)")
    ap.add_argument("--notify", action="store_true", help="слать тенанту telegram из alert_review")
    ap.add_argument("--regen", action="store_true", help="перегенерировать конституции даже если уже есть")
    ap.add_argument("--dry", action="store_true", help="только показать порядок, без исполнения")
    a = ap.parse_args()
    res = run_pipeline(filepath=a.file, force_import=a.force_import, notify=a.notify,
                       regen=a.regen, from_stage=a.from_stage, to_stage=a.to_stage, dry=a.dry)
    print("PIPELINE_SUMMARY", res)


if __name__ == "__main__":
    main()
