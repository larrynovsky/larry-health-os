"""Пересборка конституций по новым вводным (решение владельца 2026-09-25).

Оракулы: новая дата забора и новая подтверждённая связь меняют отпечаток; недельный дрейф
средних — нет (иначе «по триггеру» = «каждую неделю»); поездка — не вводная; пульс
расписания читается из system_config и называет молчание.
"""
import sqlite3
from datetime import date

import generate_constitutions as gc


def _db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE lab_results(date TEXT, test_name TEXT, value REAL);
        CREATE TABLE periods(name TEXT, type TEXT, start_date TEXT, end_date TEXT, deleted_at TEXT);
        CREATE TABLE genetic_variants(rsid TEXT);
        CREATE TABLE system_config(key TEXT PRIMARY KEY, value_text TEXT, value_num REAL, value_json TEXT);
        INSERT INTO lab_results VALUES ('2020-01-10', 'A', 1), ('2020-01-10', 'A', 1);
        INSERT INTO periods VALUES ('курс', 'treatment', '2019-01-01', '2019-06-01', NULL);
        INSERT INTO genetic_variants VALUES ('rs1');
    """)
    return c


def _belief(links, yearly=6.0):
    return {"accepted": True, "data": {
        "top_correlations": [{"a": a, "b": b, "r": 0.5} for a, b in links],
        "phases": [{"phase": "p", "start": "2019-01-01", "end": "2019-06-01"}],
        "yearly_trend": [{"year": 2025, "sleep_total": yearly}]}}


def _fp(conn, belief):
    return gc.inputs_fingerprint(gc.inputs_snapshot(conn, belief))


def test_new_draw_and_new_link_trigger_weekly_drift_does_not():
    c = _db()
    base = _fp(c, _belief([("sleep_total", "sleep_end")]))
    assert _fp(c, _belief([("sleep_total", "sleep_end")], yearly=6.4)) == base, \
        "недельный дрейф средних не вводная — иначе пересборка каждое воскресенье"
    assert _fp(c, _belief([("sleep_total", "sleep_end"), ("hrv", "resting_hr")])) != base, \
        "новая подтверждённая связь — вводная"
    c.execute("INSERT INTO periods VALUES ('поездка', 'travel', '2020-02-01', '2020-02-05', NULL)")
    assert _fp(c, _belief([("sleep_total", "sleep_end")])) == base, "поездка — не вводная"
    # Горизонт (решение владельца 25.09): отдельный новый забор — конъюнктура, НЕ вводная;
    # вводная — появление лабораторного РЯДА длиной от горизонта (≥3 точки, ≥ года).
    c.execute("INSERT INTO lab_results VALUES ('2020-03-01', 'A', 2)")
    assert _fp(c, _belief([("sleep_total", "sleep_end")])) == base, "отдельный забор — не вводная"
    c.execute("INSERT INTO lab_results VALUES ('2021-06-01', 'A', 3)")
    assert _fp(c, _belief([("sleep_total", "sleep_end")])) != base, "ряд длиной от года — вводная"


def test_changed_inputs_names_what_moved():
    old = {"links": ["a×b"], "lab_dates": ["2020-01-10"]}
    new = {"links": ["a×b"], "lab_dates": ["2020-01-10", "2020-03-01"]}
    assert gc.changed_inputs(old, new) == ["lab_dates"]
    assert gc.changed_inputs(None, new) == ["отпечатка ещё не было"]


def test_trigger_silence_days_reads_pulse():
    c = _db()
    assert gc.trigger_silence_days(c, date(2026, 10, 1)) is None, "ни разу — не ноль"
    c.execute("INSERT INTO system_config(key, value_text) VALUES (?, '2026-09-20')", (gc.CHECKED_KEY,))
    assert gc.trigger_silence_days(c, date(2026, 10, 1)) == 11
    assert 11 > gc.TRIGGER_STALE_DAYS


def test_if_changed_without_new_inputs_beats_pulse_and_skips_llm(monkeypatch):
    """Без новых вводных: ни одного вызова модели, но пульс отмечен — иначе датчик молчания
    краснел бы каждую спокойную неделю, а мёртвое расписание было бы неотличимо от тихого."""
    import sys
    import config_db
    writes = {}
    monkeypatch.setattr(gc, "_current_fingerprint", lambda: ("fp1", {"links": []}))
    monkeypatch.setattr(config_db, "get_config", lambda k, d=None, conn=None: {"fp": "fp1"} if k == gc.FP_KEY else d)
    monkeypatch.setattr(config_db, "upsert_config", lambda k, **kw: writes.__setitem__(k, kw))
    monkeypatch.setattr(gc, "_generate_one", lambda *a, **k: (_ for _ in ()).throw(AssertionError("модель звали без вводных")))
    monkeypatch.setattr(sys, "argv", ["generate_constitutions.py", "--if-changed"])
    gc.main()
    assert gc.CHECKED_KEY in writes, "пульс не отмечен на спокойной неделе"
    assert gc.FP_KEY not in writes, "отпечаток переписан без пересборки"
