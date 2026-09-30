"""Датчик «даты терапии: periods ↔ problem_list» (нить treatment-facts, 2026-08-30).

Замер 30.08 (даты в тестах — синтетика): окончание экзамплимаба — четыре даты в четырёх домах, никто не сравнивал.
Чистая функция `treatment_date_disagreements` судится на литералах: расхождение → назван,
согласие → пусто, терапия без пары по названию → в unpaired (не тихий зелёный).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _it():
    import integrity_tests as it
    return it


def test_title_dates_disagreeing_with_period_are_named():
    it = _it()
    periods = [{"name": "Экзамплимаб (иммунотерапия)", "type": "immunotherapy",
                "start_date": "2030-10-01", "end_date": "2031-02-28"}]
    problems = [{"problem_id": "aaaa0001",
                 "title": "Курс экзамплимабом — ноябрь 2030 — октябрь 2031",
                 "first_seen": "2030-11-01", "resolved_date": None}]
    mism, unpaired = it.treatment_date_disagreements(periods, problems)
    fields = {m[2] for m in mism}
    assert "end/title" in fields, mism            # февраль vs октябрь — 8 месяцев
    assert "start/first_seen" not in fields       # 31 день < 45 — молчит
    assert unpaired == []


def test_agreeing_dates_are_silent():
    it = _it()
    periods = [{"name": "Экзамплимаб (иммунотерапия)", "type": "immunotherapy",
                "start_date": "2030-10-01", "end_date": "2031-04-30"}]
    problems = [{"problem_id": "aaaa0001",
                 "title": "Курс экзамплимабом — ноябрь 2030 — апрель 2031",
                 "first_seen": "2030-11-01", "resolved_date": "2031-04-30"}]
    mism, unpaired = it.treatment_date_disagreements(periods, problems)
    assert mism == [] and unpaired == []


def test_therapy_without_named_mate_is_reported_not_silent():
    it = _it()
    periods = [{"name": "Exampla (Exampcitabine)", "type": "treatment",
                "start_date": "2032-01-01", "end_date": "2032-11-30"}]
    problems = [{"problem_id": "aaaa0002", "title": "Терапия — второй курс (январь 2032 — ноябрь 2032)",
                 "first_seen": "2032-01-01", "resolved_date": None}]
    mism, unpaired = it.treatment_date_disagreements(periods, problems)
    assert mism == [] and unpaired == ["Exampla (Exampcitabine)"]


def test_month_year_parser_handles_may_and_order():
    it = _it()
    ds = it._month_year_dates("REG-B — завершён июль 2030; начат май 2030")
    assert [str(d) for d in ds] == ["2030-07-01", "2030-05-01"]
