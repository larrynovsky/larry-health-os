"""Независимо придуманные сценарии: совет о повторном анализе проверяется по сроку и дате последней строки. Совет и аналит должны находиться в одном предложении."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

SCHED = {"Ferritin": {"interval_days": 180}, "PLT": {"interval_days": 180}}


def test_fresh_row_without_date_is_flagged(clock):
    """Придуманный совет без даты внутри окна мониторинга должен флагаться."""
    clock.set("2040-04-12")
    import gp_context as gc
    hits = gc.recommendations_without_evidence(
        "рекомендую сдать ферритин", {"Ferritin": "2040-04-09"}, SCHED)
    assert [h["test"] for h in hits] == ["Ferritin"]
    assert hits[0]["kind"] == "fresh_row" and hits[0]["age_days"] == 3


def test_stale_row_is_legitimate_repeat(clock):
    """Строка старше окна разрешает повтор. Удаление сравнения с interval_days делает контроль красным."""
    clock.set("2040-09-17")
    import gp_context as gc
    assert gc.recommendations_without_evidence(
        "стоит сдать PLT", {"PLT": "2039-08-11"}, SCHED) == []


def test_named_date_is_honest(clock):
    """Названная дата последней строки делает контекст явным."""
    clock.set("2040-04-12")
    import gp_context as gc
    assert gc.recommendations_without_evidence(
        "ферритин последний раз 2040-04-09 — рекомендую сдать повторно через полгода",
        {"Ferritin": "2040-04-09"}, SCHED) == []


def test_named_date_test_is_not_green_by_accident(clock):
    """Позитивный контроль к предыдущему: тот же текст БЕЗ даты обязан флагаться.

    Без этого теста предыдущий был бы зелёным по другой причине — так и случилось
    при первой редакции: в тексте стояло «сдано», а рекомендательный словарь ищет
    «сдать», и предикат не доходил до проверки даты вовсе (§20: зелёный причинён
    формулировкой теста, а не механизмом).
    """
    clock.set("2040-04-12")
    import gp_context as gc
    hits = gc.recommendations_without_evidence(
        "ферритин последний раз давно — рекомендую сдать повторно через полгода",
        {"Ferritin": "2040-04-09"}, SCHED)
    assert [h["test"] for h in hits] == ["Ferritin"]


def test_no_schedule_is_a_separate_class(clock):
    """Аналит без расписания — «судить нечем», отдельный класс, не тишина (§18)."""
    clock.set("2040-04-12")
    import gp_context as gc
    hits = gc.recommendations_without_evidence(
        "рекомендую сдать ферритин", {"Ferritin": "2040-04-09"}, {})
    assert [h["kind"] for h in hits] == ["no_schedule"]


def test_no_recommendation_no_verdict(clock):
    """Позитивный контроль: без рекомендательного слова предикат не срабатывает вовсе."""
    clock.set("2040-04-12")
    import gp_context as gc
    assert gc.recommendations_without_evidence(
        "ферритин в норме", {"Ferritin": "2040-04-09"}, SCHED) == []


def test_no_row_means_recommendation_is_fine(clock):
    """Строки нет вовсе — совет сдать законен и обязан пройти молча."""
    clock.set("2040-04-12")
    import gp_context as gc
    assert gc.recommendations_without_evidence("сдать ферритин", {}, SCHED) == []


def test_task_text_gets_last_date_and_sensor_goes_quiet(clock, monkeypatch):
    """Аннотация задачи добавляет дату из синтетического ряда. После этого тот же датчик молчит; без аннотации assert краснеет."""
    clock.set("2040-04-26")
    import gp_context as gc
    monkeypatch.setattr(gc._ldb, "get_recent_labs",
                        lambda n: [{"test_name": "Ferritin", "date": "2040-04-09"}])
    monkeypatch.setattr(gc._ldb, "get_effective_lab_schedule", lambda: SCHED)
    out = gc.annotate_lab_recency("Сдать кровь: ферритин")
    assert out.endswith("(последний раз сдано: Ferritin — 09.04.2040)")
    assert gc.recommendations_without_evidence(out, {"Ferritin": "2040-04-09"}, SCHED) == []
    assert gc.annotate_lab_recency("Пить воду") == "Пить воду"   # не совет сдать — не трогаем


def test_advice_and_analyte_must_share_a_sentence(clock):
    """Совет и аналит судятся в пределах предложения. Разные пункты не создают совместного утверждения."""
    clock.set("2040-04-27")
    import gp_context as gc
    text = ('[{"summary": "Назначить опросник в окне 14 дней."}, '
            '{"summary": "Проверить, соответствует ли схема стабилизации HGB ритмам сна."}]')
    assert gc.recommendations_without_evidence(text, {"HGB": "2040-04-09"},
                                               {"HGB": {"interval_days": 90}}) == []
    hits = gc.recommendations_without_evidence("Повтор HGB через месяц.", {"HGB": "2040-04-09"},
                                               {"HGB": {"interval_days": 90}})
    assert [h["test"] for h in hits] == ["HGB"]      # позитивный контроль: совет в том же предложении
