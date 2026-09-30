"""
tests/unit/test_config_lookups_characterization.py — характеризационные пины
config/lookup-функций health_db (Поток B рефакторинга, 2026-06-27):
  get_config, get_domain_signals, get_routing_keywords, get_doc_patterns,
  get_all_canonical_names, get_confirmed_aliases, get_imported_sources.

behavior-preserving пины: «как есть» ≠ «как правильно».
"""
from __future__ import annotations

import health_db


def test_get_config_missing_returns_default(db):
    assert health_db.get_config("nope", default="d") == "d"


def test_get_config_returns_parsed_json(db):
    db.execute("INSERT INTO system_config (key, value_json) VALUES (?, ?)",
               ("k", '{"a": 1}'))
    assert health_db.get_config("k") == {"a": 1}


def test_get_domain_signals_empty_without_config(db):
    assert health_db.get_domain_signals("sleep") == []


def test_get_domain_signals_from_config(db):
    db.execute("INSERT INTO system_config (key, value_json) VALUES (?, ?)",
               ("domain_signals.sleep", '[{"metric": "hrv"}]'))
    assert health_db.get_domain_signals("sleep") == [{"metric": "hrv"}]


def test_get_routing_keywords_groups_by_domain(db):
    for d, k in [("sleep", "deep"), ("sleep", "hrv"), ("stress", "cortisol")]:
        db.execute("INSERT INTO routing_keywords (domain, keyword) VALUES (?, ?)", (d, k))
    assert health_db.get_routing_keywords() == {
        "sleep": ["deep", "hrv"], "stress": ["cortisol"]}


def test_get_doc_patterns_returns_rows(db):
    db.execute("INSERT INTO doc_patterns (pattern, doc_type, match_on) VALUES (?, ?, ?)",
               ("pet", "pet_ct", "filename"))
    patterns = [p["pattern"] for p in health_db.get_doc_patterns()]
    assert "pet" in patterns


def test_get_all_canonical_names_only_confirmed(db):
    db.execute("INSERT INTO lab_name_aliases (canonical, raw_name, format_id, confirmed) "
               "VALUES (?, ?, ?, ?)", ("WBC", "wbc", 1, 1))
    db.execute("INSERT INTO lab_name_aliases (canonical, raw_name, format_id, confirmed) "
               "VALUES (?, ?, ?, ?)", ("RBC", "rbc", 1, 0))
    names = health_db.get_all_canonical_names()
    assert "WBC" in names
    assert "RBC" not in names


def test_get_confirmed_aliases_for_format(db):
    db.execute("INSERT INTO lab_name_aliases (canonical, raw_name, format_id, confirmed) "
               "VALUES (?, ?, ?, ?)", ("WBC", "wbc", 7, 1))
    assert health_db.get_confirmed_aliases(7) == {"wbc": "WBC"}


def test_get_imported_sources_returns_set(db):
    db.execute("INSERT INTO imported_docs (source_file) VALUES (?)", ("a.pdf",))
    assert health_db.get_imported_sources() == {"a.pdf"}
