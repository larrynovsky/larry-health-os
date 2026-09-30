"""
tests/unit/test_backfill_critical_flag.py — бэкофилл Ф4.5-A (5 июля).

RST: риск в WHERE — взять не те строки (уже помеченные / неактивные) или пропустить
устойчивый факт. Бэкофилл нужен для LEGACY-строк, записанных ДО авто-флага save_fact
(critical_flag=0, но durable) — потому вставляем их в обход save_fact.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _raw(conn, key, value, critical_flag=0, active=1, mem_class="state"):
    conn.execute(
        "INSERT INTO memory_facts (mem_class, key, value, subject, active, critical_flag) "
        "VALUES (?, ?, ?, 'self', ?, ?)",
        (mem_class, key, value, active, critical_flag),
    )


def test_finds_durable_skips_metric_and_flagged(db):
    import health_db
    import backfill_critical_flag as bf
    with health_db.get_conn() as conn:
        _raw(conn, "genotype_GENEX", "p.Xaa100Ter homozygous")   # durable через КЛЮЧ, не помечен
        _raw(conn, "m1", "HRV today 29ms deep sleep 53m")         # метрика
        _raw(conn, "onc1", "статус X подтверждён", critical_flag=1)  # уже помечен
        _raw(conn, "old", "спал 7 часов", active=0)               # неактивный — не трогаем
        _raw(conn, "q1", "Почему сон не восстановился после GENEX?", mem_class="question")  # вопрос
        cands = bf.find_candidates(conn)
    keys = {k for _id, k, _v in cands}
    assert "genotype_GENEX" in keys, "устойчивый факт (ген в ключе) должен найтись"
    assert "m1" not in keys, "метрика не durable"
    assert "onc1" not in keys, "уже помеченное (critical_flag=1) не переобрабатываем"
    assert "old" not in keys, "неактивные строки не трогаем"
    assert "q1" not in keys, "вопрос (даже с MTHFR) не устойчивый факт — не флагаем"


def test_save_fact_now_flags_durable_by_key(db):
    """Фикс: save_fact авто-флагает durable по key+value (не только value)."""
    import memory_facts_db as mf
    import health_db
    fid = mf.save_fact("state", "p.Xaa100Ter homozygous", key="genotype_GENEX")
    with health_db.get_conn() as c:
        row = c.execute("SELECT critical_flag FROM memory_facts WHERE id=?", (fid,)).fetchone()
    assert row[0] == 1, "ген в ключе → durable → never-decay при записи"
