"""Карточка «совет сдать уже сданное» 22.11: две ложные тревоги датчика check_reco_repeats_fresh_lab.

Замер в живой базе 22.11 (нить reco-repeat-paths):
(1) Folate — из отчёта agent_type='literature_search': сырая аннотация PubMed, её читает
    publication_reader, а не человек. Ночная починка винила literature_curator — его отчёт
    датчик не задевал (отклонена 22.11).
(2) Magnesium — задача 332 со status='duplicate': след судьи повторов, человек её не видит.

Значения выдуманы: синтетический ряд и учебное расписание. Каркас случая (2) — из отклонённой
ночной починки 2026-11-22-3eb2776c.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.unit

SCHED = {"Folate": {"interval_days": 105}, "Magnesium": {"interval_days": 180}}


def _labs(today):
    return [{"test_name": "Folate", "date": str(today - timedelta(days=85))},
            {"test_name": "Magnesium", "date": str(today - timedelta(days=176))}]


@pytest.fixture
def sensor(db, clock, monkeypatch):
    today = date.today()
    clock.set(today.isoformat())
    import labs_db
    monkeypatch.setattr(labs_db, "get_recent_labs", lambda n: _labs(today))
    monkeypatch.setattr(labs_db, "tenant_lab_schedule", lambda: SCHED)
    import integrity_tests as it

    def warns():
        start = len(it._warnings)
        it.check_reco_repeats_fresh_lab()
        return [w for w in it._warnings[start:] if w[0] == "совет сдать уже сданное"]
    return today, warns


def test_raw_literature_is_not_judged_as_advice(db, sensor):
    today, warns = sensor
    db.execute("INSERT INTO agent_reports (date, agent_type, agent_name, has_findings, findings) "
               "VALUES (?, 'literature_search', 'survivorship_literature', 1, ?)",
               (today.isoformat(), "Рекомендуется сдать фолат."))
    assert warns() == []
    # Позитивный контроль: тот же текст в отчёте, который читает человек, датчик обязан увидеть.
    db.execute("UPDATE agent_reports SET agent_type='literature_curator'")
    ws = warns()
    assert ws and "Folate" in ws[0][1], ws


def test_duplicate_trace_is_not_judged_as_delivered_task(db, sensor):
    today, warns = sensor
    db.execute("INSERT INTO tasks (source, source_date, type, content, status) "
               "VALUES ('gp_weekly', ?, 'lab_test', 'Сдать магний', 'duplicate')",
               (today.isoformat(),))
    assert warns() == []
    db.execute("UPDATE tasks SET status='open'")
    ws = warns()
    assert ws and "Magnesium" in ws[0][1], ws
