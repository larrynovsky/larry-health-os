"""
tests/unit/test_labs_characterization.py — характеризационные пины домена labs
(Поток B рефакторинга, 2026-06-27).
  get_lab_trend, get_recent_labs (исключение instrument:%-PRO), get_lab_history,
  get_lab_refs.

behavior-preserving пины: «как есть» ≠ «как правильно».
"""
from __future__ import annotations

import health_db


def test_get_lab_trend_returns_last_n_chronological(db):
    db.add_lab_result("2026-01-01", "WBC", 1.0)
    db.add_lab_result("2026-02-01", "WBC", 2.0)
    db.add_lab_result("2026-03-01", "WBC", 3.0)
    trend = health_db.get_lab_trend("WBC", n=2)
    # последние 2 по дате, отдаются хронологически (reversed)
    assert [r["value"] for r in trend] == [2.0, 3.0]


def test_get_lab_trend_filters_by_test_name(db):
    db.add_lab_result("2026-01-01", "WBC", 1.0)
    db.add_lab_result("2026-01-01", "RBC", 5.0)
    assert [r["value"] for r in health_db.get_lab_trend("WBC")] == [1.0]


def test_get_recent_labs_excludes_instrument_pro_by_default(db):
    db.add_lab_result("2026-05-01", "WBC", 4.2)
    db.add_lab_result("2026-05-01", "PRO12_sub", 3.0, source="instrument:PRO12")
    default_names = {r["test_name"] for r in health_db.get_recent_labs()}
    all_names = {r["test_name"] for r in health_db.get_recent_labs(exclude_pro=False)}
    assert "WBC" in default_names
    assert "PRO12_sub" not in default_names
    assert "PRO12_sub" in all_names


def test_get_lab_history_excludes_instrument_pro(db):
    db.add_lab_result("2026-05-01", "WBC", 4.2)
    db.add_lab_result("2026-05-01", "PRO12_sub", 3.0, source="instrument:PRO12")
    names = {r["test_name"] for r in health_db.get_lab_history()}
    assert "WBC" in names
    assert "PRO12_sub" not in names


def test_get_lab_refs_is_bank_cache_not_literal(db):
    """norm-from-documents 2026-09-02: референсы — мода бланков, литерала-резерва нет.
    Пустая БД → {} (число без документа хуже пустоты); ≥3 бланка → мода."""
    assert health_db.get_lab_refs() == {}
    for i in range(3):
        db.add_lab_result("2026-05-0%d" % (i + 1), "WBC", 4.2, source=f"doc:{i}",
                          ref_low=4.0, ref_high=10.0, unit="10^9/L")
    health_db._refresh_lab_refs()
    refs = health_db.get_lab_refs()
    assert isinstance(refs, dict) and refs["WBC"][:2] == (4.0, 10.0)
