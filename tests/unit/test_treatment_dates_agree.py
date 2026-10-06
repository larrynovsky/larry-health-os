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


# ── medications и профиль против periods (нить treatment-homes, 04.10.2026) ─────────────
# Синтетика. Форма живого случая: статус вне словаря, «активен» после конца периода, одна
# схема подтверждена дважды, режим с датой документа вместо начала, дата ремиссии в профиле
# позже главного дома. Негатив: фаза ВНУТРИ курса (поддерживающая) — норма, датчик молчит.
from datetime import date as _date

_ST = ("active", "completed", "discontinued")
_TODAY = _date(2033, 6, 1)
_PERIODS = [
    {"name": "Курс A", "type": "treatment", "start_date": "2031-01-01", "end_date": "2031-11-30"},
    {"name": "Экзамплимаб", "type": "immunotherapy", "start_date": "2030-10-01",
     "end_date": "2031-04-30"},
    {"name": "Наблюдение", "type": "remission", "start_date": "2031-12-01", "end_date": None},
]


def _med(i, name, modality, start, end, status):
    return {"id": i, "name": name, "modality": modality, "start_date": start,
            "end_date": end, "status": status}


def _kinds(meds, diagnosis=""):
    found, _ = _it().medication_fact_findings(meds, _PERIODS, _ST, _TODAY, diagnosis=diagnosis)
    return sorted(k for k, _t in found)


def test_согласованные_режимы_молчат_и_фаза_внутри_курса_норма():
    meds = [_med(1, "REG-A", "chemo", "2031-01", "2031-09", "completed"),
            _med(2, "REG-M", "chemo", "2031-09-15", "2031-11-30", "completed"),  # поддерживающая
            _med(3, "Экзамплимаб", "immunotherapy", "2030-10-01", "2031-04-30", "completed")]
    assert _kinds(meds, "ремиссия с 12.2031") == []


def test_статус_вне_словаря_назван():
    assert "статус" in _kinds([_med(1, "REG-A", "chemo", "2031-01", None, "ongoing")])


def test_активный_после_конца_периода_назван():
    assert "статус" in _kinds([_med(1, "REG-A", "chemo", "2031-09-15", None, "active")])


def test_дата_документа_вне_периода_названа():
    assert _kinds([_med(1, "REG-A", "chemo", "2032-05-10", None, "completed")]) == ["период"]


def test_одна_схема_дважды_названа():
    meds = [_med(1, "REG-A", "chemo", "2031-01", None, "completed"),
            _med(2, "REG-A", "chemo", "2031-02", None, "completed")]
    assert "дубль" in _kinds(meds)


def test_одна_схема_в_разные_годы_не_дубль():
    meds = [_med(1, "REG-A", "chemo", "2031-01", "2031-03", "completed"),
            _med(2, "REG-A", "chemo", "2031-09", "2031-11", "completed")]
    assert "дубль" not in _kinds(meds)


def test_ремиссия_в_профиле_позже_главного_дома_названа():
    assert _kinds([], "Диагноз X (ремиссия с 04.2032, обследование)") == ["ремиссия"]
