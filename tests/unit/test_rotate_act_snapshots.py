"""Оракул scripts/rotate_act_snapshots.sh (BL-SNAPSHOT-SPRAWL-1, BACKUP_POLICY R1b).

Главное свойство — канон не удаляется НИКОГДА, даже старый; снимок узнаётся по устройству
(SQLite / .bak / data/backups), а не по списку имён актов.
"""
import os
import sqlite3
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "rotate_act_snapshots.sh"
OLD = time.time() - 40 * 86400


def _db(p: Path, old=True):
    p.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(p).execute("create table t(x)").connection.close()
    if old:
        os.utime(p, (OLD, OLD))


def _touch(p: Path, old=True):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    if old:
        os.utime(p, (OLD, OLD))


def test_old_snapshots_go_canon_and_young_stay(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    _db(d / "health.db")                                   # канон, старый — остаётся
    _touch(d / "health.db-wal"); _touch(d / "health.db-shm")
    _db(d / "health_preop_x_20260730.db")                  # имя, которого нет ни в одном списке
    _touch(d / "health_preop_x_20260730.db-wal", old=False)
    _db(d / "presnap_young.db", old=False)                 # молодой снимок — остаётся
    _touch(d / "gone.db-shm")                              # сирота
    _touch(d / "instruments" / "isi.json.bak_1")           # .bak не-SQLite
    _touch(d / "instruments" / "isi.json")                 # живой каталог — остаётся
    _touch(d / "backups" / "dump.sql")                     # дамп акта
    _touch(d / "calendar_cache.json")                      # старый не-снимок — остаётся
    _db(d / "data" / "health.db")                          # вложенный форк — чужой SQLite

    out = subprocess.run(["bash", str(SCRIPT), str(d), "30"], capture_output=True,
                         text=True, check=True).stdout.split()
    gone = {Path(p).relative_to(d).as_posix() for p in out}

    assert {"health.db", "health.db-wal", "health.db-shm"}.isdisjoint(gone)
    assert (d / "health.db").exists() and (d / "health.db-wal").exists()
    assert not (d / "health_preop_x_20260730.db").exists()
    assert not (d / "health_preop_x_20260730.db-wal").exists()
    assert (d / "presnap_young.db").exists()
    assert not (d / "gone.db-shm").exists()
    assert not (d / "instruments" / "isi.json.bak_1").exists()
    assert (d / "instruments" / "isi.json").exists()
    assert not (d / "backups" / "dump.sql").exists()
    assert (d / "calendar_cache.json").exists()
    assert not (d / "data" / "health.db").exists()


def test_nested_fork_seen_canon_not(tmp_path):
    import secrets_paths
    canon = tmp_path / "health" / "data" / "health.db"
    _db(canon)
    assert secrets_paths.nested_db_forks([canon]) == []
    _db(canon.parent / "data" / "health.db")
    assert secrets_paths.nested_db_forks([canon]) == [canon.parent / "data" / "health.db"]


def test_missing_dir_is_quiet(tmp_path):
    r = subprocess.run(["bash", str(SCRIPT), str(tmp_path / "nope")], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == ""


BSCRIPT = SCRIPT.with_name("rotate_backup_snapshots.sh")
OLD70 = time.time() - 70 * 86400


def test_backups_dir_60_days_daily_and_keep_stay(tmp_path):
    """R1b в backups/ (решение владельца 27.09, 60 дней): ежедневные бэкапы — не снимки (их срок R1),
    keep/ — осознанное «храни дольше»; остальное старше срока уходит вместе с -wal/-shm.
    Мутации: убрать is_daily → уйдёт ежедневный; убрать ! -path keep → уйдёт keep/; порог
    не 60 → снимок 40 дней уйдёт или 70-дневный останется."""
    b = tmp_path / "health_partner" / "backups"
    def old(p, age=OLD70):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
        os.utime(p, (age, age))
    old(b / "health_partner_2026-07-01.db")                 # ежедневный — не наш срок
    old(b / "pgs_reference.before_discovery_20260702_0700.db")
    old(b / "pgs_reference.before_discovery_20260702_0700.db-wal")
    old(b / "presnaps" / "health.presnap_20260630.db")
    old(b / "keep" / "important_20260601.db")               # осознанно храним
    old(b / "pre_young_40d.db", OLD)                        # 40 дней < 60 — остаётся
    old(b / "gone.db-shm")                                  # сирота

    out = subprocess.run(["bash", str(BSCRIPT), str(b), "health_partner", "60"],
                         capture_output=True, text=True, check=True).stdout.split()
    gone = {Path(p).relative_to(b).as_posix() for p in out}

    assert (b / "health_partner_2026-07-01.db").exists()
    assert (b / "keep" / "important_20260601.db").exists()
    assert (b / "pre_young_40d.db").exists()
    assert not (b / "pgs_reference.before_discovery_20260702_0700.db").exists()
    assert not (b / "pgs_reference.before_discovery_20260702_0700.db-wal").exists()
    assert not (b / "presnaps").exists()                    # пустая папка убрана
    assert "gone.db-shm" in gone
