"""
tests/unit/test_write_utils_characterization.py — характеризационные пины
остаточных write-утилит health_db (Поток B рефакторинга, 2026-06-27):
  upsert_monitoring_rule (приоритет source), mark_imported (идемпотентность),
  build_context, upsert_medication (гейт proposed), save_genome_update_log.

behavior-preserving пины: «как есть» ≠ «как правильно».
"""
from __future__ import annotations

from datetime import date

import health_db


def test_upsert_monitoring_rule_manual_not_overwritten_by_default(db):
    health_db.upsert_monitoring_rule("WBC", 90, source="manual")
    health_db.upsert_monitoring_rule("WBC", 30, source="default")  # default не перебивает manual
    assert health_db.get_effective_lab_schedule()["WBC"]["interval_days"] == 90


def test_mark_imported_idempotent(db):
    health_db.mark_imported("a.pdf", "lab")
    health_db.mark_imported("a.pdf", "lab")  # INSERT OR IGNORE — без дубля и без ошибки
    assert "a.pdf" in health_db.get_imported_sources()


def test_build_context_returns_dict_with_date(db):
    ctx = health_db.build_context(date(2026, 5, 10))
    assert isinstance(ctx, dict)
    assert ctx["date"] == "2026-05-10"


def test_upsert_medication_proposed_gated_confirmed_visible(db):
    health_db.upsert_medication("ProposedDrug")  # confirmation default 'proposed'
    health_db.upsert_medication("ConfirmedDrug", confirmation="confirmed")
    default_names = {m["name"] for m in health_db.get_medications()}
    with_proposed = {m["name"] for m in health_db.get_medications(include_proposed=True)}
    assert "ConfirmedDrug" in default_names
    assert "ProposedDrug" not in default_names
    assert "ProposedDrug" in with_proposed


def test_save_genome_update_log_returns_id(db):
    rid = health_db.save_genome_update_log({"run_date": "2026-05-01", "variants_checked": 10})
    assert isinstance(rid, int)
