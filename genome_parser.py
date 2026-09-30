#!/usr/bin/env python3.11
"""
genome_parser.py
Одноразовый импорт raw 23andMe TSV → SQLite raw_snps таблица.
Запуск: python3.11 genome_parser.py
"""
from _time_inject import get_now  # seam
import sys
import sqlite3
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import health_db as db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# RAW_FILE-дефолт убран 2026-07-03: раньше указывал на файл владельца, и вызов
# import_raw_genome() без аргумента молча импортировал бы чужой геном. Путь теперь
# обязателен (см. import_raw_genome / __main__).
BATCH_SIZE = 10_000


# ── Public API ───────────────────────────────────────────────────────────────


def parse_tsv(filepath: Path) -> list[dict]:
    """
    Чистый парсер 23andMe TSV → list of {rsid, chromosome, position, genotype}.

    Без I/O в БД — только парсинг. Для тестируемости (UC-C-01).

    Поддерживает:
    - комментарии (строки начинающиеся с `#`);
    - стандартный формат: `rsid\\tchromosome\\tposition\\tgenotype`;
    - double-tab после rsid: `rsid\\t\\tchromosome\\tposition\\tgenotype`
      (исторический баг 23andMe экспорта — было важно его не терять).

    Пропускает:
    - не-rsID/non-iID идентификаторы;
    - строки с числом колонок ≠ 4 и ≠ 5.

    Не падает на `genotype="--"` — это валидное значение «no call».
    """
    rows: list[dict] = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) == 5 and parts[1] == "":
                rsid, _, chrom, pos, genotype = parts
            elif len(parts) == 4:
                rsid, chrom, pos, genotype = parts
            else:
                continue
            if not rsid.startswith("rs") and not rsid.startswith("i"):
                continue
            try:
                pos_int = int(pos)
            except ValueError:
                pos_int = None
            rows.append({
                "rsid": rsid,
                "chromosome": chrom,
                "position": pos_int,
                "genotype": genotype,
            })
    return rows


def _parse_source_identity(filepath: Path) -> dict:
    """Извлекает file_id/signature из заголовка 23andMe (строки '# file_id:' и
    '# signature:'). Это идентичность донора генома — уникальна на выгрузку."""
    fid = sig = None
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            if not line.startswith("#"):
                break
            low = line.lower()
            if "file_id:" in low:
                fid = line.split(":", 1)[1].strip()
            elif "signature:" in low:
                sig = line.split(":", 1)[1].strip()
    return {"file_id": fid, "signature": sig}


def _stored_identity(conn) -> dict | None:
    """Последняя записанная идентичность генома в этой БД (или None)."""
    try:
        row = conn.execute(
            "SELECT file_id, signature, filename FROM genome_source ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    return {"file_id": row[0], "signature": row[1], "filename": row[2]}


def _record_source(conn, identity: dict, filepath: Path, snp_count: int) -> None:
    from datetime import datetime
    conn.execute("""
        CREATE TABLE IF NOT EXISTS genome_source (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT, signature TEXT, filename TEXT,
            snp_count INTEGER, imported_at TEXT)""")
    conn.execute(
        "INSERT INTO genome_source (file_id, signature, filename, snp_count, imported_at) "
        "VALUES (?,?,?,?,?)",
        (identity.get("file_id"), identity.get("signature"),
         filepath.name, snp_count,
         get_now().isoformat(timespec="seconds")))


def import_raw_genome(filepath: Path = None, force: bool = False,
                      allow_identity_change: bool = False):
    """
    Читает raw 23andMe TSV, импортирует в raw_snps.

    Безопасность импорта между тенантами:
    - filepath ОБЯЗАТЕЛЕН: общий путь по умолчанию может импортировать
      чужой файл в БД запущенного тенанта.
    - identity-guard: file_id/signature из заголовка сохраняются в genome_source.
      force-перезапись непустого raw_snps геномом с ДРУГИМ (или неизвестным)
      file_id запрещена — иначе затрёшь чужой геном. Override: allow_identity_change=True.
    Если таблица уже заполнена и force=False — пропускает.
    """
    if filepath is None:
        raise ValueError(
            "import_raw_genome: укажи путь к файлу генома явно — дефолт убран "
            "во избежание случайного импорта чужого генома.")
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"Файл не найден: {filepath}")

    incoming = _parse_source_identity(filepath)

    db.init_db()

    conn = db.get_conn()
    existing = conn.execute("SELECT COUNT(*) FROM raw_snps").fetchone()[0]
    stored = _stored_identity(conn)
    conn.close()

    if existing > 0 and not force:
        log.info(f"raw_snps уже содержит {existing:,} записей. Пропускаем импорт. (force=True для перезаписи)")
        return existing

    if existing > 0 and force:
        same_person = bool(stored and stored.get("file_id")
                           and incoming.get("file_id")
                           and stored["file_id"] == incoming["file_id"])
        if not same_person and not allow_identity_change:
            raise RuntimeError(
                "ОТКАЗ: force-перезапись непустого raw_snps геномом с другим/"
                f"неизвестным file_id (вход={incoming.get('file_id')}, "
                f"сохранён={stored.get('file_id') if stored else 'нет'}). "
                "Иначе затрёшь чужой геном. allow_identity_change=True для override.")

    log.info(f"Начинаем импорт из {filepath}")

    conn = sqlite3.connect(db.DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")   # быстрее для bulk insert
    conn.execute("PRAGMA cache_size=-64000")  # 64MB cache

    if force:
        conn.execute("DELETE FROM raw_snps")
        conn.commit()

    batch = []
    total = 0
    skipped = 0

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) == 5 and parts[1] == "":
                # двойной таб после rsid (формат некоторых 23andMe экспортов)
                rsid, _, chrom, pos, genotype = parts
            elif len(parts) == 4:
                rsid, chrom, pos, genotype = parts
            else:
                skipped += 1
                continue
            # Пропускаем внутренние ID 23andMe (не rsID)
            if not rsid.startswith("rs") and not rsid.startswith("i"):
                skipped += 1
                continue
            try:
                pos_int = int(pos)
            except ValueError:
                pos_int = None

            batch.append((rsid, chrom, pos_int, genotype))

            if len(batch) >= BATCH_SIZE:
                conn.executemany(
                    "INSERT OR REPLACE INTO raw_snps (rsid, chromosome, position, genotype) VALUES (?,?,?,?)",
                    batch
                )
                conn.commit()
                total += len(batch)
                batch = []
                log.info(f"  {total:,} SNPs загружено...")

    if batch:
        conn.executemany(
            "INSERT OR REPLACE INTO raw_snps (rsid, chromosome, position, genotype) VALUES (?,?,?,?)",
            batch
        )
        conn.commit()
        total += len(batch)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_snps_rsid ON raw_snps(rsid)")
    _record_source(conn, incoming, filepath, total)
    conn.commit()
    conn.close()

    log.info(f"Импорт завершён: {total:,} SNPs, пропущено {skipped} "
             f"(file_id={incoming.get('file_id')})")
    return total


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Импорт raw 23andMe TSV в raw_snps")
    ap.add_argument("filepath", help="путь к raw 23andMe .txt (обязателен)")
    ap.add_argument("--force", action="store_true",
                    help="перезаписать непустой raw_snps (тем же геномом)")
    ap.add_argument("--allow-identity-change", action="store_true",
                    help="разрешить перезапись геномом с ДРУГИМ file_id (опасно)")
    a = ap.parse_args()
    count = import_raw_genome(Path(a.filepath), force=a.force,
                              allow_identity_change=a.allow_identity_change)
    print(f"\nИтого в raw_snps: {count:,} записей")
