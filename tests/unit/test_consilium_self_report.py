"""Самоотчёт доходит до месячного консилиума (28.09.2026).

До фикса баллы опросников не видел ни один читатель: конституция их не берёт (правило 27.09),
лаб-история режет по источнику; заметки куратора писались в пустоту. Все значения выдуманы.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.unit


def test_self_report_block_shows_scores_and_notes(db):
    import health_db
    import monthly_consilium as mc
    from _time_inject import get_today
    today = get_today()
    health_db._ensure_lab_table()
    with health_db.get_conn() as conn:
        conn.execute("INSERT INTO lab_results (date, test_name, value, unit, source) "
                     "VALUES (?, 'ISI_total', 11, 'score', 'instrument:isi')", (str(today - timedelta(days=3)),))
        conn.execute("INSERT INTO lab_results (date, test_name, value, unit, source) "
                     "VALUES (?, 'Ferritin', 50, 'ng/mL', 'doc:x')", (str(today - timedelta(days=3)),))
    health_db.save_memory(category="survivorship_note", key="surv_x",
                          value=json.dumps({"curator_summary": "учебное расхождение опросника с кольцом"},
                                           ensure_ascii=False), confidence=0.5, source="survivorship_curator")
    lines = mc._self_report_lines(today, 30)
    text = "\n".join(lines)
    assert "ISI_total: 11" in text and "учебное расхождение" in text
    assert "Ferritin" not in text, "анализы — не самоотчёт"


def test_self_report_block_says_when_empty(db):
    import monthly_consilium as mc
    assert mc._self_report_lines(date(2000, 1, 31), 30) == [
        "  (за период опросников не заполнялось и заметок куратора нет)"]
