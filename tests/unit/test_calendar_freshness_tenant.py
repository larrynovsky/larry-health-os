"""Тенант-осознанность датчика свежести календаря (integrity_tests.scan_calendar_staleness).

Инцидент 2026-07-10: токен партнёра протух → его calendar_cache гнил 9 дней, а
однотенантный чек (gcf.cache_path) видел только owner и молчал. Датчик, который не
умеет ловить партнёрскую несвежесть, бесполезен — доказываем оба исхода: устаревший
кэш партнёра ловится; свежий — молчит; чужой без кэша не даёт ложный 'missing'.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _tenant_db(tmp_path, name):
    d = tmp_path / "calfresh" / name / "data"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "health.db"
    p.write_text("")  # существование БД-файла (родитель для кэша)
    return p


def _cache(db_path, age_h):
    p = db_path.parent / "calendar_cache.json"
    p.write_text("{}")
    t = time.time() - age_h * 3600
    os.utime(p, (t, t))
    return p


def test_partner_stale_cache_flagged(db, tmp_path):
    import integrity_tests as it
    owner = _tenant_db(tmp_path, "health")
    partner = _tenant_db(tmp_path, "health_partner")
    _cache(owner, age_h=1)      # owner свежий
    _cache(partner, age_h=200)  # партнёр гниёт — ровно слепой угол 2026-07-10
    hits = it.scan_calendar_staleness([owner, partner], owner, error_age_h=25)
    assert any(t == "health_partner" and k == "stale" for t, k, _ in hits), \
        "устаревший кэш ПАРТНЁРА обязан ловиться"
    assert not any(t == "health" for t, k, _ in hits), "owner свежий — молчит"


def test_all_fresh_silent(db, tmp_path):
    import integrity_tests as it
    owner = _tenant_db(tmp_path, "health")
    partner = _tenant_db(tmp_path, "health_partner")
    _cache(owner, 1)
    _cache(partner, 1)
    assert it.scan_calendar_staleness([owner, partner], owner, 25) == []


def test_foreign_tenant_missing_not_flagged(db, tmp_path):
    # чужой тенант без кэша (не использует календарь) — НЕ 'missing' (только current)
    import integrity_tests as it
    owner = _tenant_db(tmp_path, "health")
    partner = _tenant_db(tmp_path, "health_partner")
    _cache(owner, 1)  # у партнёра кэша нет вовсе
    assert it.scan_calendar_staleness([owner, partner], owner, 25) == [], \
        "чужой тенант без календаря не должен давать ложный missing"


def test_owner_missing_flagged(db, tmp_path):
    import integrity_tests as it
    owner = _tenant_db(tmp_path, "health")  # у owner кэша нет
    hits = it.scan_calendar_staleness([owner], owner, 25)
    assert any(t == "health" and k == "missing" for t, k, _ in hits), \
        "у текущего (owner) отсутствие кэша обязано ловиться как missing"
