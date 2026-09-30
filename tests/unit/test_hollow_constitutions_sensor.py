"""
integrity_tests.scan_hollow_constitutions — датчик полых конституций.

Доказываем оба исхода: ловит полые (cons>0, genome>0, resolved==0), молчит на
здоровых и на неприменимых (нет генома / нет конституций).
"""
from __future__ import annotations

import sqlite3

import pytest

import integrity_tests as it

pytestmark = pytest.mark.unit


def _mk(path, gv, cons, resolved):
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE genetic_variants (rsid TEXT, effect_allele_status TEXT)")
    c.execute("CREATE TABLE constitutions (domain TEXT)")
    for i in range(gv):
        c.execute("INSERT INTO genetic_variants VALUES (?,?)",
                  (f"rs{i}", "resolved" if i < resolved else None))
    for i in range(cons):
        c.execute("INSERT INTO constitutions VALUES (?)", (f"d{i}",))
    c.commit()
    c.close()
    return path


def _tp(tmp_path, name):
    # подкаталог: fixture `db` занимает tmp_path/health/data
    d = tmp_path / "hollow" / name / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d / "health.db"


def test_hollow_detected(db, tmp_path):
    p = _mk(_tp(tmp_path, "health_partner"), gv=100, cons=5, resolved=0)
    hits = it.scan_hollow_constitutions([p])
    assert hits and "health_partner" in hits[0]


def test_healthy_clean(db, tmp_path):
    p = _mk(_tp(tmp_path, "health"), gv=100, cons=5, resolved=60)
    assert it.scan_hollow_constitutions([p]) == []


def test_no_genome_not_flagged(db, tmp_path):
    p = _mk(_tp(tmp_path, "health_x"), gv=0, cons=5, resolved=0)
    assert it.scan_hollow_constitutions([p]) == []


def test_no_constitutions_not_flagged(db, tmp_path):
    p = _mk(_tp(tmp_path, "health_y"), gv=100, cons=0, resolved=0)
    assert it.scan_hollow_constitutions([p]) == []


def test_dev_clone_discriminator():
    # BL-TENANT-OURA-1: дев/стейджинг-клоны исключаются из скана тенантов
    assert it._is_dev_clone("/x/health_staging/data/health.db") is True
    assert it._is_dev_clone("/x/health_dev/data/health.db") is True
    assert it._is_dev_clone("/x/health_test/data/health.db") is True
    assert it._is_dev_clone("/x/health_partner/data/health.db") is False
    assert it._is_dev_clone("/x/health/data/health.db") is False
