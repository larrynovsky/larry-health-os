#!/usr/bin/env python3.11
"""
test_db_regression — порог регрессии числа строк (anti тихое стирание).

Чистый предикат проверяется на независимо придуманных размерах таблиц.
Снимки в отдельных тестах создаются из вымышленных строк.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import db_regression as r


def test_wipe_fires():
    """Полное исчезновение достаточно большой таблицы вызывает тревогу."""
    out = r.find_regressions({"medications": 0}, {"medications": 17})
    assert out and "medications" in out[0] and "−100%" in out[0], out


def test_minor_shrink_not_fired():
    """Вымышленное уменьшение на четверть не пересекает порог тревоги."""
    assert r.find_regressions({"workouts": 180}, {"workouts": 240}) == []


def test_tiny_table_ignored():
    """Крошечная таблица (пик < min_rows) — шум, игнор."""
    assert r.find_regressions({"x": 0}, {"x": 2}) == []


def test_boundary_50pct():
    """Ровно −50% не флаг; чуть больше — флаг."""
    assert r.find_regressions({"t": 5}, {"t": 10}) == []      # ровно 50%
    assert r.find_regressions({"t": 4}, {"t": 10}) != []      # 60%


def test_new_table_not_flagged():
    """Таблица есть в каноне, но не в бэкапах — не регрессия."""
    assert r.find_regressions({"newbie": 0}, {"medications": 0}) == []


def test_growth_not_flagged():
    """Рост — не регрессия."""
    assert r.find_regressions({"lab_results": 900}, {"lab_results": 800}) == []


def test_exclude_derived_keep_sources():
    """Регенерируемые/производные исключены; источники — под мониторингом."""
    for t in ["tasks", "hypotheses_cbcr", "hypothesis_outcomes",
              "pgs_weights", "lab_results_staging"]:
        assert r._excluded(t), f"{t} должна быть исключена"
    for t in ["medications", "lab_results", "memory", "documents",
              "genetic_variants", "prs_scores"]:
        assert not r._excluded(t), f"{t} должна оставаться под мониторингом"


if __name__ == "__main__":
    test_wipe_fires()
    test_minor_shrink_not_fired()
    test_tiny_table_ignored()
    test_boundary_50pct()
    test_new_table_not_flagged()
    test_growth_not_flagged()
    print("TEST PASS")


def _db(path, n):
    import sqlite3
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE events (id INTEGER)")
    c.executemany("INSERT INTO events VALUES (?)", [(i,) for i in range(n)])
    c.commit()
    c.close()
    return str(path)


def test_drop_saved_in_preop_snapshot_is_explained(tmp_path):
    """Вымышленный снимок сохраняет не меньше строк, чем было до уменьшения."""
    snap = _db(tmp_path / "pre_cr_event_dedupe.db", 150)
    assert r.explained_by_snapshot({"events": 23}, {"events": 148}, [snap]) == {
        "events": "pre_cr_event_dedupe.db"}


def test_drop_without_full_snapshot_still_rings(tmp_path):
    """Снимок уже после убыли (строк меньше пика) ничего не сохранил — тревога остаётся."""
    snap = _db(tmp_path / "pre_later.db", 23)
    assert r.explained_by_snapshot({"events": 23}, {"events": 148}, [snap]) == {}
    assert r.explained_by_snapshot({"events": 23}, {"events": 148}, []) == {}
