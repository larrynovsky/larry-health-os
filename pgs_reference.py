"""pgs_reference.py — ОБЩАЯ (не per-tenant) reference-БД полигенных весов.

`pgs_catalog` (104 строки) + `pgs_weights` (38.8М строк, ~2.5GB) — статические
веса PGS Catalog, ОДИНАКОВЫЕ для всех тенантов и не зависящие от персонального
генома. Вынесены из health.db (2026-07-02), чтобы персональный канон оставался
лёгким: бэкапы, VACUUM и pre-op снапшоты промоута больше не таскают 3.6GB.

Архитектура:
  • Писатель (genome_weights) пишет НАПРЯМУЮ сюда (get_ref_conn()).
  • Читателям нужен JOIN pgs_weights × raw_snps (персональный геном в каноне) —
    они ATTACH-ат эту БД к своему соединению как `pgsref` и читают
    `pgsref.pgs_weights` / `pgsref.pgs_catalog` (кросс-БД JOIN в одном запросе).

Путь: env HEALTH_PGS_DB (для тестов) или ~/.health_reference/pgs.db (shared).
Единственный публичный вход-набор: ref_db_path(), get_ref_conn(), attach().

NB: индекс ix_pgs_weights_pos (chr+pos, ~1.1GB) НЕ создаётся — fallback-джойн по
координатам в prs_pipeline не реализован (помечен future). rsid-индекс — нужен.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

_ALIAS = "pgsref"

# Схема reference — совпадает с genome_weights._DDL по таблицам, но БЕЗ pos-индекса.
_SCHEMA = """
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


def ref_db_path() -> Path:
    """Путь к reference-БД. env HEALTH_PGS_DB переопределяет (тесты)."""
    p = os.environ.get("HEALTH_PGS_DB")
    if p:
        return Path(p)
    d = Path.home() / ".health_reference"
    d.mkdir(parents=True, exist_ok=True)
    return d / "pgs.db"


def get_ref_conn() -> sqlite3.Connection:
    """Соединение с reference-БД (схема гарантирована). Для писателя/чтения каталога."""
    conn = sqlite3.connect(str(ref_db_path()))
    conn.executescript(_SCHEMA)
    return conn


def attach(conn: sqlite3.Connection, alias: str = _ALIAS) -> str:
    """ATTACH reference-БД к существующему соединению (для JOIN с raw_snps канона).

    Идемпотентно: повторный ATTACH того же алиаса игнорируется. Возвращает alias.
    """
    # схему в reference гарантируем один раз (на случай пустого файла)
    get_ref_conn().close()
    try:
        conn.execute(f"ATTACH DATABASE ? AS {alias}", (str(ref_db_path()),))
    except sqlite3.OperationalError as e:
        if "already in use" not in str(e).lower() and "already attached" not in str(e).lower():
            raise
    return alias
