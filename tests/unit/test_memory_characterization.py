"""
tests/unit/test_memory_characterization.py — характеризационные пины домена
memory (Поток B рефакторинга, 2026-06-27).
  save_memory (upsert по key), get_memory (фильтр по category, active).

behavior-preserving пины: «как есть» ≠ «как правильно».
"""
from __future__ import annotations

import health_db


def test_save_memory_insert_and_get_by_category(db):
    health_db.save_memory("observation", "val1")
    vals = [m["value"] for m in health_db.get_memory("observation")]
    assert "val1" in vals


def test_save_memory_upsert_by_key_updates_in_place(db):
    health_db.save_memory("fact", "v1", key="k1")
    health_db.save_memory("fact", "v2", key="k1")
    k1 = [m for m in health_db.get_memory("fact") if m.get("key") == "k1"]
    assert len(k1) == 1            # обновление, не дубликат
    assert k1[0]["value"] == "v2"


def test_get_memory_category_filter_isolates(db):
    health_db.save_memory("catA", "a")
    health_db.save_memory("catB", "b")
    cats = {m["category"] for m in health_db.get_memory("catA")}
    assert cats == {"catA"}
