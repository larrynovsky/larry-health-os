"""partner-integrity Phase B: per-tenant db_integrity + мета-датчик достижимости.

Позит-контроль ОБОИХ направлений (RST): здоровые тенант-БД → pass; недостижимая тенант-БД →
ЯВНЫЙ fail (не тихий skip). Расширение выбрано хирургически: corruption/reachability СТРУКТУРНЫ,
не зависят от объёма данных → не дают ложных срабатываний на data-бедном партнёре (в отличие от
problem_list/lab-regression — те осознанно оставлены owner-only, см. PLAN_forward §⟳⟳⟳).
"""
from __future__ import annotations

import pytest

import health_db
import integrity_tests as it


def test_db_integrity_per_tenant_healthy(db, monkeypatch):
    """Здоровая тенант-БД (фикстура) → ok, счётчик тенантов = 1."""
    monkeypatch.setattr(it, "_tenant_db_paths", lambda include_current=True: [health_db.DB_PATH])
    res = it.check_db_integrity()
    assert res["ok"] and res["tenants"] == 1


def test_db_integrity_per_tenant_unopenable_fails_loud(db, monkeypatch):
    """Недостижимая тенант-БД → ЯВНЫЙ AssertionError (не тихий skip). Тег тенанта в сообщении."""
    monkeypatch.setattr(it, "_tenant_db_paths",
                        lambda include_current=True: [health_db.DB_PATH,
                                                      "/nonexistent/health_ghost/data/health.db"])
    with pytest.raises(AssertionError) as e:
        it.check_db_integrity()
    assert "health_ghost" in str(e.value)


def test_tenant_dbs_reachable_healthy(db, monkeypatch):
    """Все обнаруженные тенант-БД открываются + есть daily_metrics → ok."""
    monkeypatch.setattr(it, "_tenant_db_paths", lambda include_current=True: [health_db.DB_PATH])
    res = it.check_tenant_dbs_reachable()
    assert res["ok"] and res["tenants"] == 1


def test_tenant_dbs_reachable_unreachable_fails_loud(db, monkeypatch):
    """Недостижимая тенант-БД → ЯВНЫЙ fail (закрывает тихую усечёнку _iter_tenant_ro)."""
    monkeypatch.setattr(it, "_tenant_db_paths",
                        lambda include_current=True: [health_db.DB_PATH,
                                                      "/nonexistent/health_ghost/data/health.db"])
    with pytest.raises(AssertionError) as e:
        it.check_tenant_dbs_reachable()
    assert "health_ghost" in str(e.value)
