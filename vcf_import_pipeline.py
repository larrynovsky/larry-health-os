#!/usr/bin/env python3
"""
VCF Import Pipeline — Tellmegen WGS 30x → health.db
Studio-only (single-writer). Checkpointed.

Phases:
  A — update_known   : обновить генотипы raw_snps по позиции из VCF (быстро, без API)
  B — discover_new   : найти новые ClinVar-варианты через myvariant.info (фон, часы)
  C — backfill       : пересчитать effect_allele для изменённых записей
  D — validate       : concordance 23andMe↔WGS + coverage report
  E — pharmaco       : CPIC star-allele calling → pharmaco_phenotypes (write-through)

Usage (Studio):
  python3.11 vcf_import_pipeline.py --phase A        # only fast phase
  python3.11 vcf_import_pipeline.py --phase AB       # A + background discovery
  python3.11 vcf_import_pipeline.py --phase all      # full pipeline (A-E)
  python3.11 vcf_import_pipeline.py --phase D        # just validation
  python3.11 vcf_import_pipeline.py --phase E        # (re)run pharmaco only
  python3.11 vcf_import_pipeline.py --phase D --dry  # report without changes
"""

from _time_inject import get_now  # seam
import sys
import os
import time
import logging
import argparse
import sqlite3
import requests
from pathlib import Path
from datetime import datetime

# ── Config ───────────────────────────────────────────────────────────────────────

def _default_vcf() -> Path:
    """Дефолт --vcf: путь к геному тенанта — из private/local_samples.yaml (имя файла —
    идентификатор набора, личное); нет записи — <каталог данных>/data/genome.vcf."""
    p = Path(__file__).resolve().parent / "private" / "local_samples.yaml"
    if p.exists():
        import yaml
        v = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("genome_vcf")
        if v:
            return Path(v)
    import health_db
    return health_db.DB_PATH.parent / "genome.vcf"


VCF_DEFAULT = _default_vcf()

# R2-fix (2026-06-29, multitenancy Phase 0): путь к БД — ТОЛЬКО из health_db,
# единого резолвера. Раньше: Path(HEALTH_DATA_DIR)/"health.db" — это ДРУГОЙ
# файл, чем канонический HEALTH_DATA_DIR/data/health.db. Та же переменная
# окружения давала vcf_import и health_db РАЗНЫЕ файлы → split-brain hazard.
import health_db as _hdb
DB_PATH = _hdb.DB_PATH

# Лог и отчёт — в каталоге данных тенанта, а не в ~/health_data: у постороннего импорт модуля
# (даже тестом) создавал каталог в домашней папке (приёмка урока 24.09, BL-PUB-12).
LOG_PATH    = DB_PATH.parent / "vcf_import.log"
REPORT_PATH = DB_PATH.parent / "vcf_validation_report.txt"

MIN_GQ   = 20    # genotype quality threshold
MIN_DP   = 10    # minimum read depth
BATCH_SZ = 1000  # myvariant.info batch size

MYVARIANT_URL = "https://myvariant.info/v1"

KEEP_SIGNIFICANCE = frozenset({
    "Pathogenic",
    "Likely pathogenic",
    "Risk factor",
    "Association",
    "Pathogenic/Likely pathogenic",
    # Фармакогенетика (28.09): «drug response» — класс ClinVar для вариантов ответа на
    # лекарство. Без него фармаковарианты, найденные только WGS, отбрасывались.
    "drug response",
})

# ── Logging ──────────────────────────────────────────────────────────────────────

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH),
    ],
)
log = logging.getLogger(__name__)

# ── DB helpers ───────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def ensure_tables(con: sqlite3.Connection):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS vcf_staging (
            chrom    TEXT    NOT NULL,
            pos      INTEGER NOT NULL,
            ref      TEXT    NOT NULL,
            alt      TEXT    NOT NULL,
            gt       TEXT    NOT NULL,
            genotype TEXT,
            gq       INTEGER,
            dp       INTEGER,
            PRIMARY KEY (chrom, pos, ref, alt)
        );
        CREATE TABLE IF NOT EXISTS vcf_discovery (
            chrom        TEXT    NOT NULL,
            pos          INTEGER NOT NULL,
            ref          TEXT    NOT NULL,
            alt          TEXT    NOT NULL,
            rsid         TEXT,
            gene         TEXT,
            significance TEXT,
            conditions   TEXT,
            genotype     TEXT,
            gq           INTEGER,
            dp           INTEGER,
            annotated_at TEXT,
            PRIMARY KEY (chrom, pos, ref, alt)
        );
        CREATE TABLE IF NOT EXISTS vcf_import_log (
            phase   TEXT NOT NULL,
            step    TEXT NOT NULL,
            status  TEXT NOT NULL,
            count   INTEGER,
            message TEXT,
            ts      TEXT DEFAULT (datetime('now'))
        );
    """)
    con.commit()
    # Add source column to raw_snps if missing
    cols = {r[1] for r in con.execute("PRAGMA table_info(raw_snps)").fetchall()}
    if "source" not in cols:
        log.info("Adding source column to raw_snps...")
        con.execute("ALTER TABLE raw_snps ADD COLUMN source TEXT DEFAULT '23andme'")
        con.execute("UPDATE raw_snps SET source='23andme' WHERE source IS NULL")
        con.commit()


def log_step(con, phase, step, status, count=None, message=None):
    con.execute(
        "INSERT INTO vcf_import_log (phase,step,status,count,message) VALUES (?,?,?,?,?)",
        (phase, step, status, count, message),
    )
    con.commit()
    sfx  = f" ({count:,})" if count is not None else ""
    note = f" — {message}" if message else ""
    log.info(f"[{phase}] {step}: {status}{sfx}{note}")

# ── VCF helpers ──────────────────────────────────────────────────────────────────

def norm_chrom(chrom: str) -> str:
    """chr1→1  chrX→X  chrM→MT  (23andMe convention)."""
    c = chrom.removeprefix("chr")
    return "MT" if c == "M" else c


def is_snp(ref: str, alt: str) -> bool:
    return len(ref) == 1 and len(alt) == 1 and alt not in (".", "*")


def resolve_genotype(gt_str: str, ref: str, alt: str):
    """VCF GT + REF + ALT → 23andMe two-char genotype, or None."""
    gt = gt_str.replace("|", "/")
    if "." in gt:
        return None
    alleles = [ref] + alt.split(",")
    try:
        bases = [alleles[int(i)] for i in gt.split("/")]
    except (ValueError, IndexError):
        return None
    if any(len(b) != 1 for b in bases):
        return None   # indel
    return "".join(bases)


def clinvar_summary(cv: dict) -> tuple:
    """(значимость, условия) по ВСЕМ записям RCV варианта, а не по первой.

    У одного варианта в ClinVar бывает несколько RCV: «Benign» по одному состоянию и
    «drug response» по лекарству. До 28.09 бралась rcv[0] — и значимость, стоящая не
    первой, терялась (замер 28.09: F5 Leiden вышел с None). Значимости — уникальные, в
    порядке появления, через «; »; условия — до трёх."""
    rcv = cv.get("rcv") or []
    if isinstance(rcv, dict):
        rcv = [rcv]
    sigs, conds = [], []
    for r in rcv:
        if not isinstance(r, dict):
            continue
        s = r.get("clinical_significance")
        if s and s not in sigs:
            sigs.append(s)
        c = r.get("conditions") or {}
        for one in (c if isinstance(c, list) else [c]):
            name = one.get("name") if isinstance(one, dict) else None
            if name and name not in conds:
                conds.append(str(name))
    return ("; ".join(sigs) or None, "; ".join(conds[:3]) or None)


def significance_kept(sig: "str | None") -> bool:
    """Хоть одна из значимостей варианта — из KEEP_SIGNIFICANCE (без учёта регистра)."""
    if not sig:
        return False
    keep = {k.lower() for k in KEEP_SIGNIFICANCE}
    parts = {p.strip().lower() for chunk in sig.split(";") for p in chunk.split("/")}
    return bool(parts & keep) or any(p.strip().lower() in keep for p in sig.split(";"))


class GenomeIdentityError(RuntimeError):
    """VCF расходится с уже загруженным чипом так, как свой геном не расходится."""


def open_vcf(path: Path):
    """VCF как текст, сжатый (.vcf.gz, bgzip) или нет — по сигнатуре файла, не по имени.
    До 28.09 сжатый файл читался как текст: мусор без строк-записей, 0 вариантов и зелёный
    лог — тихий пустой импорт (замер 28.09 на синтетике)."""
    import gzip
    with open(path, "rb") as fh:
        magic = fh.read(2)
    if magic == b"\x1f\x8b":
        return gzip.open(path, "rt", errors="replace")
    return open(path, "r", errors="replace")


def parse_sample(fmt: str, sample: str) -> dict:
    return dict(zip(fmt.split(":"), sample.split(":")))


# ── Phase A ──────────────────────────────────────────────────────────────────────

def phase_a_update_known(con, vcf_path: Path, dry: bool = False):
    """
    Update raw_snps genotypes from WGS positions.
    Fast: single VCF scan, no API calls.
    Uses raw_snps.chromosome/position as the position index.
    """
    log.info("═══ Phase A: update_known ═══")
    log_step(con, "A", "start", "started")

    # Build position index: (chrom_norm, pos) → rsid
    log.info("Building position index from raw_snps...")
    pos_index: dict = {}
    for r in con.execute("SELECT rsid, chromosome, position FROM raw_snps"):
        pos_index[(str(r["chromosome"]), int(r["position"]))] = r["rsid"]
    log.info(f"Position index: {len(pos_index):,} entries")

    updates: dict  = {}    # rsid → new_genotype
    conflicts: dict = {}   # rsid → (old_gt, new_gt)
    total = matched = low_qual = no_call = 0

    log.info(f"Scanning {vcf_path} ...")
    with open_vcf(vcf_path) as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            total += 1
            if total % 1_000_000 == 0:
                log.info(f"  {total:,} lines | {matched:,} matched")

            cols = line.rstrip("\n").split("\t")
            if len(cols) < 10:
                continue
            chrom_raw, pos_s, _, ref, alt, _, flt, _, fmt, sample = cols[:10]

            if flt != "PASS":
                continue

            smap   = parse_sample(fmt, sample)
            gt_raw = smap.get("GT", "./.")
            if "." in gt_raw:
                no_call += 1
                continue

            try:
                gq = int(smap.get("GQ", 0))
                dp = int(smap.get("DP", 0))
            except ValueError:
                continue
            if gq < MIN_GQ or dp < MIN_DP:
                low_qual += 1
                continue

            key  = (norm_chrom(chrom_raw), int(pos_s))
            rsid = pos_index.get(key)
            if rsid is None:
                continue

            matched += 1
            genotype = resolve_genotype(gt_raw, ref, alt)
            if genotype is None:
                continue

            old = con.execute(
                "SELECT genotype FROM raw_snps WHERE rsid=?", (rsid,)
            ).fetchone()
            # Сравниваем без учёта порядка аллелей: "AG" == "GA" биологически.
            # Настоящий конфликт = разные наборы аллелей (AA→GG, AG→CC и т.п.)
            if old and old[0] and sorted(old[0]) != sorted(genotype):
                conflicts[rsid] = (old[0], genotype)

            updates[rsid] = genotype

    log.info(
        f"Scan done: {total:,} PASS | {matched:,} matched | "
        f"{len(updates):,} SNP updates | {len(conflicts):,} conflicts"
    )
    if conflicts:
        log.warning("Genotype conflicts (23andMe → WGS):")
        for rsid, (old, new) in sorted(conflicts.items())[:50]:
            log.warning(f"  {rsid}: {old} → {new}")
    # Чужой геном поверх своего чипа (28.09, приём VCF через бот): правило «WGS побеждает»
    # переписало бы чип владельца генотипами другого человека. Свой WGS против своего чипа —
    # 0 несовпадений из 157 950 (замер 28.09); у разных людей их десятки процентов.
    import genome_intake as _gi
    max_share, min_matched = _gi.chip_identity_limits(con)   # пороги — system_config тенанта
    if matched >= min_matched and len(conflicts) / matched > max_share:
        log_step(con, "A", "identity", "error", count=len(conflicts),
                 message=f"{len(conflicts)}/{matched} расходятся с чипом — не пишу")
        raise GenomeIdentityError(
            f"{len(conflicts)} из {matched} позиций расходятся с чипом — это не тот же человек")

    if dry:
        log_step(con, "A", "dry-done", "done", count=len(updates))
        return updates

    applied = 0
    with con:
        for rsid, new_gt in updates.items():
            con.execute(
                "UPDATE raw_snps SET genotype=?, source='wgs_updated' WHERE rsid=?",
                (new_gt, rsid),
            )
            applied += 1
    log_step(con, "A", "done", "done", count=applied,
             message=f"{len(conflicts)} genotype conflicts (see log)")
    return updates

# ── Phase B ──────────────────────────────────────────────────────────────────────

def phase_b_discover_new(con, vcf_path: Path, dry: bool = False):
    """
    Scan VCF for PASS SNPs not in raw_snps.
    Batch-query myvariant.info by HGVS position (hg19).
    Write ClinVar-significant hits to vcf_discovery + raw_snps.
    Runtime: 2-4 hours. Checkpointed per chromosome.
    """
    log.info("═══ Phase B: discover_new ═══")

    done_chroms = {
        r[0]
        for r in con.execute(
            "SELECT step FROM vcf_import_log WHERE phase='B' AND status='done'"
        )
    }
    log.info(f"Resuming. Done chroms: {sorted(done_chroms) or 'none'}")

    known_pos: set = set(
        (str(r[0]), int(r[1]))
        for r in con.execute("SELECT chromosome, position FROM raw_snps")
    )
    log.info(f"Known positions to skip: {len(known_pos):,}")

    def flush_batch(batch: list) -> int:
        if not batch:
            return 0
        queries = [f"chr{c}:g.{p}{r}>{a}" for c, p, r, a, *_ in batch]
        try:
            resp = requests.post(
                f"{MYVARIANT_URL}/query",
                data={
                    "q": ",".join(queries),
                    "fields": (
                        "_id,rsid,dbsnp.rsid,dbsnp.gene.symbol,"
                        "clinvar.rcv.clinical_significance,"
                        "clinvar.rcv.conditions.name,clinvar.gene.symbol"
                    ),
                    "size": "1",
                    "hg19": "true",
                },
                timeout=40,
            )
            resp.raise_for_status()
            raw = resp.json()
            hits = raw if isinstance(raw, list) else [raw]
        except Exception as exc:
            log.warning(f"myvariant.info error: {exc}")
            return 0

        added = 0
        for item, (chrom, pos, ref, alt, genotype, gq, dp) in zip(hits, batch):
            if not item or item.get("notfound"):
                continue
            rsid = item.get("rsid") or (item.get("dbsnp") or {}).get("rsid")
            dbsnp = item.get("dbsnp") or {}
            g    = dbsnp.get("gene") or {}
            gene = g.get("symbol") if isinstance(g, dict) else None
            sig, conds = clinvar_summary(item.get("clinvar") or {})
            if not dry:
                con.execute(
                    "INSERT OR REPLACE INTO vcf_discovery "
                    "(chrom,pos,ref,alt,rsid,gene,significance,conditions,"
                    "genotype,gq,dp,annotated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                    (chrom, pos, ref, alt, rsid, gene, sig, conds, genotype, gq, dp),
                )
                if rsid and sig:
                    if significance_kept(sig):
                        con.execute(
                            "INSERT OR IGNORE INTO raw_snps "
                            "(rsid,chromosome,position,genotype,source) VALUES (?,?,?,?,'wgs_new')",
                            (rsid, chrom, pos, genotype),
                        )
                        added += 1
        if not dry:
            con.commit()
        return added

    current_chrom = None
    batch:  list  = []
    total_new     = 0
    scanned       = 0

    with open_vcf(vcf_path) as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 10:
                continue

            chrom_norm = norm_chrom(cols[0])
            if chrom_norm != current_chrom:
                if batch:
                    total_new += flush_batch(batch); batch = []
                if current_chrom and current_chrom not in done_chroms:
                    log_step(con, "B", current_chrom, "done", count=total_new)
                current_chrom = chrom_norm
                if current_chrom in done_chroms:
                    log.info(f"Skipping chr{current_chrom} (checkpoint)")
                    continue
                log.info(f"chr{current_chrom} ...")

            if current_chrom in done_chroms:
                continue

            pos_s, _, ref, alt, _, flt, _, fmt, sample = cols[1:10]
            if flt != "PASS" or not is_snp(ref, alt):
                continue
            if (chrom_norm, int(pos_s)) in known_pos:
                continue

            smap   = parse_sample(fmt, sample)
            gt_raw = smap.get("GT", "./.")
            if "." in gt_raw or gt_raw == "0/0":
                continue
            try:
                gq = int(smap.get("GQ", 0)); dp = int(smap.get("DP", 0))
            except ValueError:
                continue
            if gq < MIN_GQ or dp < MIN_DP:
                continue
            genotype = resolve_genotype(gt_raw, ref, alt)
            if genotype is None:
                continue

            batch.append((chrom_norm, int(pos_s), ref, alt, genotype, gq, dp))
            scanned += 1
            if len(batch) >= BATCH_SZ:
                total_new += flush_batch(batch); batch = []
                time.sleep(0.05)

    if batch and current_chrom not in done_chroms:
        total_new += flush_batch(batch)
    if current_chrom and current_chrom not in done_chroms:
        log_step(con, "B", current_chrom, "done", count=total_new)
    log_step(con, "B", "complete", "done", count=total_new,
             message=f"scanned {scanned:,} candidate SNPs")
    return total_new


# ── Phase C ──────────────────────────────────────────────────────────────────────

def phase_c_backfill(con):
    log.info("═══ Phase C: backfill ═══")
    log_step(con, "C", "start", "started")
    import subprocess, shutil
    PYTHON = shutil.which("python3.11") or "/opt/homebrew/bin/python3.11"
    HDATA  = str(_hdb.DB_PATH.parent.parent)  # канонический каталог тенанта (R2-fix 2026-06-29)
    scripts = Path(__file__).resolve().parent
    for script, label in [
        ("genome_annotator.py",       "genome_annotator"),
        ("backfill_effect_alleles.py", "backfill_effect_alleles"),
    ]:
        log.info(f"Running {script} ...")
        r = subprocess.run(
            [PYTHON, str(scripts / script)],
            capture_output=True, text=True, cwd=scripts, timeout=3600,
            env={**os.environ, "HEALTH_DATA_DIR": HDATA},
        )
        if r.returncode != 0:
            log.error(f"{script} FAILED:\n{r.stderr[:600]}")
            log_step(con, "C", label, "error", message=r.stderr[:200])
        else:
            log.info(f"{script} OK")
    log_step(con, "C", "done", "done")


# ── Phase D ──────────────────────────────────────────────────────────────────────

REPORT_RSIDS = [
    ("rs1801133", "MTHFR C677T"),
    ("rs1801131", "MTHFR A1298C"),
    ("rs4680",    "COMT Val158Met"),
    ("rs6265",    "BDNF Val66Met"),
    ("rs53576",   "OXTR"),
    ("rs1800497", "DRD2/ANKK1"),
    ("rs1801260", "CLOCK 3111T/C"),
    ("rs1815739", "ACTN3 R577X"),
    ("rs9939609", "FTO"),
    ("rs4994",    "ADRB3"),
    ("rs662",     "PON1"),
    ("rs1799963", "F2 Prothrombin"),
    ("rs6025",    "F5 Factor V Leiden"),
    ("rs1045642", "ABCB1/MDR1"),
    ("rs4244285", "CYP2C19*2"),
    ("rs4986893", "CYP2C19*3"),
    ("rs1799853", "CYP2C9*2"),
    ("rs1057910", "CYP2C9*3"),
    ("rs1800896", "IL10"),
    ("rs361525",  "TNF"),
    ("rs429358",  "APOE ε4"),
    ("rs7412",    "APOE ε2"),
]


def phase_d_validate(con):
    log.info("═══ Phase D: validate ═══")
    lines = []

    # Summary
    updated   = con.execute("SELECT COUNT(*) FROM raw_snps WHERE source='wgs_updated'").fetchone()[0]
    new_vars  = con.execute("SELECT COUNT(*) FROM raw_snps WHERE source='wgs_new'").fetchone()[0]
    orig      = con.execute("SELECT COUNT(*) FROM raw_snps WHERE source='23andme' OR source IS NULL").fetchone()[0]
    lines += [
        "IMPORT SUMMARY",
        f"  23andMe original    : {orig:,}",
        f"  WGS genotype update : {updated:,}",
        f"  WGS new (ClinVar)   : {new_vars:,}",
        f"  Total raw_snps      : {orig + updated + new_vars:,}",
        "",
    ]

    # Effect allele resolution
    lines.append("EFFECT ALLELE RESOLUTION")
    for status in ("resolved","palindromic","genotype_mismatch","multiallelic_ambiguous"):
        n = con.execute(
            "SELECT COUNT(*) FROM genetic_variants WHERE effect_allele_status=?", (status,)
        ).fetchone()[0]
        lines.append(f"  {status:<32}: {n:,}")
    lines.append("")

    # Key rsID table
    lines.append(f"{'rsID':<14} {'Label':<22} {'gt':^5} {'src':<17} ea_status")
    lines.append("─" * 76)
    for rsid, label in REPORT_RSIDS:
        rs  = con.execute("SELECT genotype,source FROM raw_snps WHERE rsid=?", (rsid,)).fetchone()
        gv  = con.execute("SELECT effect_allele_status FROM genetic_variants WHERE rsid=?", (rsid,)).fetchone()
        gt  = rs["genotype"] if rs else "—"
        src = rs["source"]   if rs else "MISSING ⚠️"
        eas = gv["effect_allele_status"] if gv else "—"
        flag = " ⚠️" if eas == "palindromic" else ""
        lines.append(f"{rsid:<14} {label:<22} {gt:^5} {src:<17} {eas}{flag}")
    lines.append("")

    # Discovery
    disc = con.execute("""
        SELECT significance, COUNT(*) n FROM vcf_discovery
        WHERE rsid IS NOT NULL GROUP BY significance ORDER BY n DESC LIMIT 12
    """).fetchall()
    if disc:
        lines.append("NEW VARIANTS BY CLINVAR SIGNIFICANCE")
        for r in disc:
            lines.append(f"  {(r['significance'] or 'Unknown'):<44} {r['n']:,}")
    else:
        lines.append("Discovery (Phase B): not yet run.")

    report = "\n".join(lines)
    print("\n" + "═"*60 + "\nVCF VALIDATION REPORT  " + get_now().strftime("%Y-%m-%d %H:%M") + "\n" + "═"*60)
    print(report)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report)
    log.info(f"Report → {REPORT_PATH}")


# ── Phase E ──────────────────────────────────────────────────────────────────────

def phase_e_pharmaco(con: sqlite3.Connection, dry: bool = False):
    """Phase E: CPIC star-allele calling.

    Write-through consistency (Таненбаум §7.5.4):
    Called after Phase A updates raw_snps, so pharmaco_phenotypes always
    reflect the latest genotype data in the same pipeline run.

    Creates one genome_imports record per call, writes pharmaco_phenotypes
    rows (one per gene, upserted), stamps phase_e_pharmaco_at.
    """
    log.info("═══ Phase E: pharmaco_pipeline ═══")
    log_step(con, "E", "start", "started")

    # Ensure pharmaco tables exist (created by health_db._migrate_pharmaco_tables;
    # inline DDL here guarantees idempotency even if health_db wasn't imported yet)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS genome_imports (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            import_date           TEXT    NOT NULL DEFAULT (date('now')),
            vcf_source            TEXT,
            phase_e_pharmaco_at   TEXT,
            phase_f_monogenic_at  TEXT,
            phase_g_prs_at        TEXT,
            completed_at          TEXT,
            notes                 TEXT
        );
        CREATE TABLE IF NOT EXISTS pharmaco_phenotypes (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            gene              TEXT    NOT NULL,
            star_allele_1     TEXT,
            star_allele_2     TEXT,
            phenotype         TEXT    NOT NULL,
            confidence        TEXT    NOT NULL DEFAULT 'indeterminate',
            coverage_snp_count INTEGER DEFAULT 0,
            genome_import_id  INTEGER REFERENCES genome_imports(id),
            computed_at       TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_pharmaco_gene
            ON pharmaco_phenotypes(gene);
    """)
    con.commit()

    if dry:
        log.info("Phase E: dry run — skipping pharmaco computation")
        log_step(con, "E", "dry-done", "done")
        return

    # Create genome_imports record for this pipeline run
    cur = con.execute(
        "INSERT INTO genome_imports (vcf_source) VALUES (?)",
        (str(VCF_DEFAULT),),
    )
    genome_import_id = cur.lastrowid
    con.commit()
    log.info("Phase E: genome_import_id=%d", genome_import_id)

    try:
        import pharmaco_pipeline
        result = pharmaco_pipeline.run(con, genome_import_id)
    except Exception as exc:
        log.error("Phase E: pharmaco_pipeline.run() failed: %s", exc)
        log_step(con, "E", "error", "error", message=str(exc)[:200])
        return

    indeterminate = result.get("indeterminate_genes", [])
    log_step(
        con, "E", "done", "done",
        count=len(result.get("results", {})),
        message=f"status={result['status']} indeterminate={indeterminate}",
    )
    log.info("Phase E done: %s", result["status"])
    if indeterminate:
        log.warning("Phase E: indeterminate genes (SNPs missing): %s", indeterminate)


# ── Phase F ──────────────────────────────────────────────────────────────────────

def phase_f_traits(con: sqlite3.Connection, dry: bool = False):
    """Phase F: Deterministic trait calling (lactase, eye color, APOE).

    Uses traits_pipeline.run() — same write-through pattern as Phase E.
    Reuses the genome_imports row created by Phase E (or creates one if needed).
    """
    log.info("═══ Phase F: traits_pipeline ═══")
    log_step(con, "F", "start", "started")

    if dry:
        log.info("Phase F: dry run — skipping")
        log_step(con, "F", "dry-done", "done")
        return

    # Reuse latest genome_imports id (Phase E should have created it)
    genome_import_id = con.execute(
        "SELECT MAX(id) FROM genome_imports"
    ).fetchone()[0]
    if genome_import_id is None:
        cur = con.execute(
            "INSERT INTO genome_imports (vcf_source) VALUES (?)",
            (str(VCF_DEFAULT),),
        )
        genome_import_id = cur.lastrowid
        con.commit()

    log.info("Phase F: genome_import_id=%d", genome_import_id)

    try:
        import traits_pipeline
        result = traits_pipeline.run(con, genome_import_id)
    except Exception as exc:
        log.error("Phase F: traits_pipeline.run() failed: %s", exc)
        log_step(con, "F", "error", "error", message=str(exc)[:200])
        return

    indeterminate = result.get("indeterminate_traits", [])
    log_step(
        con, "F", "done", "done",
        count=len(result.get("results", {})),
        message=f"status={result['status']} indeterminate={indeterminate}",
    )
    log.info("Phase F done: %s", result["status"])
    if indeterminate:
        log.warning("Phase F: indeterminate traits (SNPs missing): %s", indeterminate)


# ── Phase G ──────────────────────────────────────────────────────────────────────

def phase_g_wellness(con: sqlite3.Connection, dry: bool = False):
    """Phase G: Wellness SNP calling (MTHFR, COMT, FTO, HFE).

    Uses wellness_pipeline.run(). Same write-through pattern as Phases E & F.
    """
    log.info("═══ Phase G: wellness_pipeline ═══")
    log_step(con, "G", "start", "started")

    if dry:
        log.info("Phase G: dry run — skipping")
        log_step(con, "G", "dry-done", "done")
        return

    genome_import_id = con.execute(
        "SELECT MAX(id) FROM genome_imports"
    ).fetchone()[0]
    if genome_import_id is None:
        cur = con.execute(
            "INSERT INTO genome_imports (vcf_source) VALUES (?)",
            (str(VCF_DEFAULT),),
        )
        genome_import_id = cur.lastrowid
        con.commit()

    log.info("Phase G: genome_import_id=%d", genome_import_id)

    try:
        import wellness_pipeline
        result = wellness_pipeline.run(con, genome_import_id)
    except Exception as exc:
        log.error("Phase G: wellness_pipeline.run() failed: %s", exc)
        log_step(con, "G", "error", "error", message=str(exc)[:200])
        return

    indeterminate = result.get("indeterminate_traits", [])
    log_step(
        con, "G", "done", "done",
        count=len(result.get("results", {})),
        message=f"status={result['status']} indeterminate={indeterminate}",
    )
    log.info("Phase G done: %s", result["status"])
    if indeterminate:
        log.warning("Phase G: indeterminate traits: %s", indeterminate)

# ── Phase H ──────────────────────────────────────────────────────────────────────

def phase_h_prs(con: sqlite3.Connection, dry: bool = False):
    """Phase H: Polygenic Risk Scores (PGS Catalog weights → prs_scores).

    Requires genome_weights.py to have been run first to populate pgs_catalog
    and pgs_weights. Gracefully returns early if no weights are registered.
    Uses prs_pipeline.run(). Same write-through stamp pattern as Phases E–G.
    """
    log.info("═══ Phase H: prs_pipeline ═══")
    log_step(con, "H", "start", "started")

    if dry:
        log.info("Phase H: dry run — skipping")
        log_step(con, "H", "dry-done", "done")
        return

    genome_import_id = con.execute(
        "SELECT MAX(id) FROM genome_imports"
    ).fetchone()[0]
    if genome_import_id is None:
        cur = con.execute(
            "INSERT INTO genome_imports (vcf_source) VALUES (?)",
            (str(VCF_DEFAULT),),
        )
        genome_import_id = cur.lastrowid
        con.commit()

    log.info("Phase H: genome_import_id=%d", genome_import_id)

    try:
        import prs_pipeline
        result = prs_pipeline.run(con, genome_import_id)
    except Exception as exc:
        log.error("Phase H: prs_pipeline.run() failed: %s", exc)
        log_step(con, "H", "error", "error", message=str(exc)[:200])
        return

    status   = result.get("status", "unknown")
    n_scores = len(result.get("results", {}))
    failed   = result.get("failed", [])
    log_step(
        con, "H", "done", "done",
        count=n_scores,
        message=f"status={status} failed={failed}",
    )
    log.info("Phase H done: %s | %d scores | %d failed", status, n_scores, len(failed))
    if status == "no_weights":
        log.warning("Phase H: no PGS weights — run genome_weights.py --pgs-id <ID> first")


# ── Phase R: панель фармакогенетики — явный референс и «нет строки = норма» ─────────
# Решение владельца 28.09 (вариант Б «в рамках»). VCF хранит только отличия от референса,
# и позиция панели, которой нет в файле, у человека без чипа давала «не определено» по
# всем генам. Правило:
#   * строка PASS — генотип как есть; строка RefCall 0/0 с качеством — гомозигота по
#     референсу (это ЯВНОЕ свидетельство вызывальщика, не вывод);
#   * строки нет — гомозигота по референсу ТОЛЬКО если файл — полный геном (записей много)
#     с вызывальщиком в заголовке и сборкой GRCh37; источник помечается как вывод, и
#     уверенность фармакотипа понижается (pharmaco_pipeline);
#   * CYP2D6 и HLA-B — никогда (их нет в methodology/pharmaco_panel_grch37.yaml);
#   * панель и экзом — вариант A: без строки ничего не выводится;
#   * есть чип и вывод с ним разошёлся хоть в одной позиции — откат ВСЕГО вывода по файлу:
#     расхождение значит, что файл не то, чем кажется.

PANEL_FILE = Path(__file__).resolve().parent / "methodology" / "pharmaco_panel_grch37.yaml"
CALLER_MARKS = ("DeepVariant", "GATK", "HaplotypeCaller", "DRAGEN", "Sentieon", "Strelka")
WGS_MIN_RECORDS = 3_000_000       # экзом — десятки-сотни тысяч записей, полный геном — миллионы
GRCH37_CHR1 = "249250621"          # длина chr1 в GRCh37/hg19 (в GRCh38 — 248956422)
from pharmaco_pipeline import REFCALL_SOURCE as SRC_REFCALL, INFERRED_SOURCE as SRC_INFERRED  # noqa: E402
SRC_PANEL = "wgs_panel"


def load_panel(path: Path = PANEL_FILE) -> dict:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8"))["positions"]


def vcf_kind(header: list, records: int) -> dict:
    """Что за файл: вызывальщик из заголовка, сборка, полный ли геном (по числу записей)."""
    text = "\n".join(header)
    caller = next((m for m in CALLER_MARKS if m.lower() in text.lower()), None)
    grch37 = (GRCH37_CHR1 in text or any(b in text for b in ("GRCh37", "hg19", "b37", "hs37d5"))) \
        and "248956422" not in text
    return {"caller": caller, "grch37": grch37, "records": records,
            "wgs": records >= WGS_MIN_RECORDS}


def phase_r_reference_fill(con, vcf_path: Path, dry: bool = False, panel: dict = None) -> dict:
    log.info("═══ Phase R: pharmaco panel reference ═══")
    panel = panel if panel is not None else load_panel()
    by_pos = {(p["chrom"], int(p["pos"])): rsid for rsid, p in panel.items()}
    header, found, records = [], {}, 0
    with open_vcf(vcf_path) as fh:
        for line in fh:
            if line.startswith("#"):
                header.append(line.rstrip("\n"))
                continue
            records += 1
            cols = line.rstrip("\n").split("\t", 10)
            if len(cols) < 10:
                continue
            key = (norm_chrom(cols[0]), int(cols[1]))
            if key in by_pos:
                found[by_pos[key]] = cols
    kind = vcf_kind(header, records)
    may_infer = bool(kind["wgs"] and kind["caller"] and kind["grch37"])
    log.info(f"Phase R: {kind} → вывод «нет строки = норма» {'разрешён' if may_infer else 'запрещён'}")

    calls = {}                                    # rsid → (genotype, source)
    for rsid, p in panel.items():
        cols = found.get(rsid)
        if cols is None:
            if may_infer:
                calls[rsid] = (p["ref"] * 2, SRC_INFERRED)
            continue
        _, _, _, ref, alt, _, flt, _, fmt, sample = cols[:10]
        smap = parse_sample(fmt, sample)
        try:
            gq, dp = int(smap.get("GQ", 0)), int(smap.get("DP", 0))
        except ValueError:
            continue
        gt = smap.get("GT", "./.")
        if "." in gt or gq < MIN_GQ or dp < MIN_DP or ref != p["ref"]:
            continue                               # нет вызова, слабый вызов или чужая сборка
        if flt == "RefCall" and gt.replace("|", "/") == "0/0":
            calls[rsid] = (ref * 2, SRC_REFCALL)
        elif flt == "PASS":
            g = resolve_genotype(gt, ref, alt)
            if g:
                calls[rsid] = (g, SRC_PANEL)

    chip = {r["rsid"]: r["genotype"] for r in con.execute(
        f"SELECT rsid, genotype FROM raw_snps WHERE rsid IN ({','.join('?' * len(panel))})",
        list(panel)) if r["genotype"] and r["genotype"] not in ("--",)}
    disagree = [rs for rs, (g, src) in calls.items()
                if src == SRC_INFERRED and rs in chip and sorted(chip[rs]) != sorted(g)]
    if disagree:
        log.warning(f"Phase R: вывод разошёлся с чипом в {len(disagree)} позициях — откат всего вывода")
        calls = {rs: v for rs, v in calls.items() if v[1] != SRC_INFERRED}

    written = 0
    if not dry:
        with con:
            for rsid, (g, src) in calls.items():
                if rsid in chip:                    # чип уже есть: не перетираем (сверка выше)
                    continue
                p = panel[rsid]
                con.execute("INSERT OR IGNORE INTO raw_snps (rsid,chromosome,position,genotype,source) "
                            "VALUES (?,?,?,?,?)", (rsid, p["chrom"], int(p["pos"]), g, src))
                written += 1
    out = {"kind": kind, "may_infer": may_infer, "calls": calls,
           "rolled_back": disagree, "written": written}
    log_step(con, "R", "done", "done", count=written,
             message=f"infer={'on' if may_infer else 'off'} rollback={len(disagree)}")
    return out


# ── Main ─────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all",
                    choices=["A","R","B","C","D","E","F","G","H","AB","ACD","RE","all"])
    ap.add_argument("--vcf",  default=str(VCF_DEFAULT))
    ap.add_argument("--dry",  action="store_true", help="No DB writes")
    args = ap.parse_args()

    vcf_path = Path(args.vcf)
    if any(p in args.phase for p in "ABR") or args.phase == "all":
        if not vcf_path.exists():
            log.error(f"VCF not found: {vcf_path}"); sys.exit(1)

    con    = get_db()
    ensure_tables(con)
    phases = args.phase if args.phase != "all" else "ARBCDEFGH"

    if "A" in phases: phase_a_update_known(con, vcf_path, dry=args.dry)
    if "R" in phases: phase_r_reference_fill(con, vcf_path, dry=args.dry)
    if "B" in phases: phase_b_discover_new(con, vcf_path, dry=args.dry)
    if "C" in phases:
        if not args.dry: phase_c_backfill(con)
    if "D" in phases: phase_d_validate(con)
    if "E" in phases: phase_e_pharmaco(con, dry=args.dry)
    if "F" in phases: phase_f_traits(con, dry=args.dry)
    if "G" in phases: phase_g_wellness(con, dry=args.dry)
    if "H" in phases: phase_h_prs(con, dry=args.dry)

    log.info("Pipeline complete.")


if __name__ == "__main__":
    main()

