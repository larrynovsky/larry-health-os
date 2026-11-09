"""Карточка report:44f643 (06.11, v0.2.5, установка без генома): ежемесячный скан PGS
сбоил там, где генома нет. Генетические задачи без генома тихо выходят; с геномом идут.
Мутация «снять _no_genome с discover_pgs_monthly» — красная."""
from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace as NS

import pytest

pytestmark = pytest.mark.unit


def test_genome_present_reads_raw_snps(monkeypatch, tmp_path):
    import contextlib
    import genome_db
    db = tmp_path / "h.db"
    sqlite3.connect(db).close()

    @contextlib.contextmanager
    def _conn():
        c = sqlite3.connect(db)
        try:
            yield c
        finally:
            c.close()
    monkeypatch.setattr(genome_db._hdb, "get_conn", _conn)
    assert genome_db.genome_present() is False, "нет таблицы — генома нет"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE raw_snps (rsid TEXT, genotype TEXT)")
    assert genome_db.genome_present() is False, "пустая таблица — генома нет"
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO raw_snps VALUES ('rs1','AG')")
    assert genome_db.genome_present() is True


@pytest.mark.parametrize("present", [False, True])
def test_pgs_scan_runs_only_with_genome(monkeypatch, present):
    import genome_db
    import notify
    from jobs import scheduled
    monkeypatch.setattr(genome_db, "genome_present", lambda: present)
    calls, faults = [], []

    def _scan(*a):
        calls.append(a)
        return {"imported": [], "errors": [], "snapshot_error": None}
    monkeypatch.setattr(scheduled.pgsd, "discover_and_import_new_scores", _scan)

    async def _fault(*a, **k):
        faults.append(a)
    monkeypatch.setattr(notify, "afault", _fault)
    monkeypatch.setattr(scheduled, "get_chat_id", lambda: 1)
    ctx = NS(job=NS(next_t=None, job=None), bot=NS())
    asyncio.run(scheduled.discover_pgs_monthly(ctx))
    assert bool(calls) is present and faults == []
