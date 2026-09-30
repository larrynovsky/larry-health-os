"""Ночной датчик check_hae_arrivals_have_owner — доставка вердикта судьи (решение владельца 26.09).

Два свойства: (1) крик держится по состоянию реестра (unowned/new);
(2) свежие файлы при отставшем реестре обнаруживают слепоту самого судьи.
Хронология ниже независимо придумана.
"""
import sqlite3
from pathlib import Path

import integrity_tests as it


def _tenant(tmp_path, rows, newest="20320517"):
    home = tmp_path / "health"
    (home / "data" / "hae_rest").mkdir(parents=True)
    dbp = home / "data" / "health.db"
    con = sqlite3.connect(dbp)
    con.execute("CREATE TABLE hae_metric_registry (metric_name TEXT, status TEXT, notes TEXT, "
                "last_seen TEXT)")
    con.executemany("INSERT INTO hae_metric_registry VALUES (?,?,?,?)", rows)
    con.commit()
    con.close()
    (home / "data" / "hae_rest" / f"HealthAutoExport-REST-{newest}T000000.json").write_text("{}")
    return dbp


def _fired(monkeypatch, dbp, needle):
    monkeypatch.setattr(it, "_tenant_db_paths", lambda include_current=True: [str(dbp)])
    monkeypatch.setattr(it.db, "DB_PATH", str(dbp))
    it._warnings.clear()
    it.check_hae_arrivals_have_owner()
    return [w for w in it._warnings if needle in w[0]]


def test_unowned_metric_rings_even_when_it_stopped_arriving(tmp_path, monkeypatch):
    dbp = _tenant(tmp_path, [("steps", "handled", "", "2032-05-17"),
                             ("cardio_recovery", "unowned", "", "2026-09-22")])
    fired = _fired(monkeypatch, dbp, "без хозяина")
    assert fired and "cardio_recovery" in fired[0][1]


def test_all_owned_is_silent(tmp_path, monkeypatch):
    dbp = _tenant(tmp_path, [("steps", "handled", "", "2032-05-17"),
                             ("apple_stand_hour", "tracked", "не берём: дубль", "2032-05-17")])
    assert _fired(monkeypatch, dbp, "без хозяина") == []
    assert _fired(monkeypatch, dbp, "слеп") == []


def test_blind_judge_rings(tmp_path, monkeypatch):
    # Вымышленное свежее сырьё не совпадает со старой датой реестра.
    dbp = _tenant(tmp_path, [("steps", "handled", "", "2032-01-03")])
    assert _fired(monkeypatch, dbp, "слеп")


# ── архив сырья сжат (реестр Q2, 26.09): датчик судит по файлам на диске, не по квитанции ──
import datetime as _dt

import pytest


def _archive(tmp_path, monkeypatch, names):
    rest = tmp_path / "health" / "data" / "hae_rest"
    rest.mkdir(parents=True)
    for n in names:
        (rest / n).write_text("{}")
    monkeypatch.setattr(it, "_tenant_db_paths",
                        lambda include_current=True: [str(rest.parent / "health.db")])
    monkeypatch.setattr(it, "get_today", lambda: _dt.date(2026, 9, 26))


def test_stale_uncompressed_raw_fails(tmp_path, monkeypatch):
    _archive(tmp_path, monkeypatch, ["HealthAutoExport-REST-20260901T000000.json",
                                     "HealthAutoExport-REST-20260925T000000.json"])
    with pytest.raises(AssertionError, match="несжатые"):
        it.check_hae_raw_archive_compressed()


def test_compressed_or_fresh_raw_is_silent(tmp_path, monkeypatch):
    _archive(tmp_path, monkeypatch, ["HealthAutoExport-REST-20260901T000000.json.gz",
                                     "HealthAutoExport-REST-20260915T000000.json"])
    assert it.check_hae_raw_archive_compressed() is None
