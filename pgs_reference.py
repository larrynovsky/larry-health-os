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
Единственный публичный вход-набор: ref_db_path(), get_ref_conn(), attach();
для писателя справочника — snapshot_dir(), writable(), import_room().

NB: индекс ix_pgs_weights_pos (chr+pos, ~1.1GB) НЕ создаётся — fallback-джойн по
координатам в prs_pipeline не реализован (помечен future). rsid-индекс — нужен.
"""
from __future__ import annotations

import os
import shutil
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


_WRITER_SCHEMA = """
CREATE TABLE IF NOT EXISTS pgs_rejected (
    pgs_id            TEXT PRIMARY KEY,
    num_variants      INTEGER NOT NULL,
    best_coverage_pct REAL NOT NULL,
    genome_positions  INTEGER NOT NULL,
    rejected_at       TEXT NOT NULL,
    reason            TEXT NOT NULL DEFAULT 'coverage'   -- coverage | format
);
CREATE TABLE IF NOT EXISTS discovery_runs (
    at              TEXT NOT NULL,     -- UTC ISO, конец прогона
    writer          TEXT NOT NULL,     -- каталог данных тенанта-писателя (имя, без пути)
    imported        INTEGER NOT NULL,
    pruned          INTEGER NOT NULL,
    capped          INTEGER NOT NULL,  -- 1: кандидаты остались на следующий прогон
    next_due_at     TEXT               -- UTC ISO: позже этого следующий прогон опоздал
);
"""


def ensure_writer_tables(conn: sqlite3.Connection) -> None:
    """Таблицы, которые ведёт только писатель справочника (02.11.2026). Не в _SCHEMA:
    get_ref_conn() зовут и читатели с `:ro` (контейнер) — CREATE на новой таблице у них
    упал бы «readonly database»."""
    conn.executescript(_WRITER_SCHEMA)
    # 03.11.2026: у отказа есть причина. coverage — бесполезна на геномах установки
    # (пересматривается, когда придёт геном крупнее); format — файл шкалы не в аддитивной
    # форме (веса dosage_0/1/2_weight вместо effect_weight), её не посчитать ни на каком геноме.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pgs_rejected)")}
    if "reason" not in cols:
        conn.execute("ALTER TABLE pgs_rejected ADD COLUMN reason TEXT NOT NULL DEFAULT 'coverage'")
        conn.commit()


def rejected(conn: sqlite3.Connection) -> dict:
    """{pgs_id: genome_positions} отвергнутых моделей; таблицы ещё нет → пусто."""
    try:
        return dict(conn.execute("SELECT pgs_id, genome_positions FROM pgs_rejected"))
    except sqlite3.OperationalError:
        return {}


def writer_alarm(now=None) -> "str | None":
    """Тревога ночного датчика «у справочника есть писатель» или None.

    Квитанции пишет только писатель (discovery_runs, 02.11.2026). Красный — последний прогон
    оставил кандидатов, а его срок «следующий прогон не позже» прошёл: докачка встала —
    писателя на машине нет или он падает до квитанции. Таблицы ещё нет — до первого прогона
    писателя судить нечего (граница: установка, где писателя не было никогда, молчит)."""
    import datetime as _dt
    from _time_inject import get_now
    now = now or get_now(_dt.timezone.utc)
    p = ref_db_path()
    if not p.exists():
        return None
    try:
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        row = c.execute("SELECT at, writer, capped, next_due_at FROM discovery_runs "
                        "ORDER BY at DESC LIMIT 1").fetchone()
        c.close()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return "квитанций докачки справочника нет, хотя таблица заведена — писатель ни разу не дошёл до конца"
    at, writer, capped, due = row
    if capped and due and now > _dt.datetime.fromisoformat(due):
        return (f"докачка справочника встала: последний прогон {at[:16]} ({writer}) оставил "
                f"кандидатов, следующий ожидался до {due[:16]} UTC и не отметился")
    return None


def snapshot_dir() -> Path:
    """Единственный дом снимков справочника — рядом с ним, а не в backups/ тенанта.

    Справочник один на машину, значит и снимок один: до 02.11.2026 каждый тенант клал
    свою копию (16 ГБ) в свою папку бэкапов и держал по 5 штук (BACKUP_POLICY R2).
    """
    return ref_db_path().parent / "snapshots"


def writable() -> bool:
    """Может ли ЭТОТ процесс писать в справочник — по факту доступа, а не по имени тенанта.

    Контейнер владельца видит справочник `:ro` (compose.override.yaml); тот, кто не может
    писать, не импортирует и не снимает копию — только пересчитывает свои баллы (Phase H).
    """
    p = ref_db_path()
    return os.access(p, os.W_OK) and os.access(p.parent, os.W_OK)


def import_room(models_per_run: int) -> dict:
    """Хватит ли диска на один прогон докачки: снимок (= размер справочника) плюс рост.

    Рост = средний размер модели × моделей за прогон × 2 (WAL пишет страницы дважды до
    чекпойнта; урок 06-27: WAL 31 ГБ). Всё вычисляется из самого справочника — порога-
    литерала нет (до 02.11 был DB_SIZE_LIMIT_MB=20000, который остановил бы докачку
    при 20 ГБ при любом свободном месте и не остановил бы при пустом диске).
    """
    p = ref_db_path()
    size = p.stat().st_size if p.exists() else 0
    n = 0
    if size:
        try:
            c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            n = c.execute("SELECT COUNT(*) FROM pgs_catalog").fetchone()[0]
            c.close()
        except sqlite3.Error:
            n = 0
    growth = (size // n if n else 0) * models_per_run * 2
    need = size + growth
    free = shutil.disk_usage(p.parent if p.parent.exists() else Path.home()).free
    return {"ok": free >= need, "free": free, "need": need, "ref_size": size, "models": n}


def room_alarm(models_per_run: int) -> "str | None":
    """Текст тревоги ночного датчика места или None. Предикат живёт здесь, а не в
    integrity_tests: импорт integrity_tests исполняет весь монитор (урок), а датчик
    без исполненного негативного контроля не принимается (C-20)."""
    room = import_room(models_per_run)
    if room["ok"]:
        return None
    snaps = sorted(snapshot_dir().glob("*.db"))
    held = sum(p.stat().st_size for p in snaps)
    return (f"свободно {room['free'] / 1e9:.1f} ГБ, следующей докачке PGS нужно "
            f"{room['need'] / 1e9:.1f} ГБ (снимок справочника + рост); снимков висит "
            f"{len(snaps)} на {held / 1e9:.1f} ГБ в {snapshot_dir()}. "
            f"Что делать: docs/how-to/free_studio_disk.md")


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
