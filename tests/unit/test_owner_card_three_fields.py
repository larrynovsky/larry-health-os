"""Правило трёх полей: на стол владельца кладётся только отвечаемое (13.09).

Повод дословный: первый гейт, приехавший новым путём, нёс summary «...указывает на
несбалансированность порогов фильтрации или темпа пополнения пула». Это написано
инженеру; владелец не инженер и ответить на такое не может. Стол, наполненный такими
карточками, он перестанет открывать — и мы получим прежний телеграм-список под новым
именем.

Проверяем СТРУКТУРУ (поля есть или нет). Читаемость формулировки машинно не судится, и
делать вид, что судится, нельзя — граница названа и в модуле.
"""
from __future__ import annotations

import json

import pytest

import night_cycle as nc
import night_investigator
import parked_decisions as pd
from _time_inject import set_test_clock, clear_test_clock

READY = {
    "class": "owner_decision", "diagnosis": "fictional diagnosis", "park_reason": "internal_module.py §13",
    "owner_ask": {
        "subject": "Учебный отчёт не собирается третью ночь подряд.",
        "question": "Учебный проект ждёт решения. Что делаем?",
        "options": [{"label": "продолжить", "cost": "нужен час"},
                    {"label": "отложить", "cost": "результат придётся подождать"}],
        "recommended": "отложить"},
}
NOT_READY = {"class": "owner_decision", "diagnosis": "несбалансированность порогов фильтрации",
             "park_reason": "домен владельца", "owner_ask": None}
ONE_OPTION = {"class": "owner_decision", "diagnosis": "x", "owner_ask": {
    "question": "ок?", "options": [{"label": "да", "cost": "ничего"}]}}
NO_COST = {"class": "owner_decision", "diagnosis": "x", "owner_ask": {
    "question": "ок?", "options": [{"label": "да", "cost": ""}, {"label": "нет", "cost": ""}]}}


def test_card_without_subject_is_not_ready():
    """30.09: без предмета звонок не может назвать решение — карточка не готова."""
    no_subject = {**READY, "owner_ask": {k: v for k, v in READY["owner_ask"].items()
                                         if k != "subject"}}
    assert nc.owner_card("любая находка", no_subject) is None


def test_subject_is_the_first_line_and_the_bell_line():
    """Предмет — первая строка карточки, и именно его колокол показывает владельцу."""
    import owner_nag
    card = nc.owner_card("internal_check.py §13", READY)
    assert card.splitlines()[0] == "Учебный отчёт не собирается третью ночь подряд."
    assert owner_nag._headline(card) == "Учебный отчёт не собирается третью ночь подряд."


def test_ready_card_carries_question_options_and_costs():
    card = nc.owner_card("internal_check.py §13", READY)
    assert card and "Что делаем?" in card
    assert "Варианты:\n• продолжить — нужен час" in card
    assert "• отложить — результат придётся подождать" in card
    assert "Если промолчишь — решение останется ждать тебя" in card
    assert "internal" not in card and "§" not in card



@pytest.mark.parametrize("verdict,why", [
    (NOT_READY, "нет owner_ask вовсе"),
    (ONE_OPTION, "один вариант — это не выбор"),
    (NO_COST, "варианты без цены"),
])
def test_unready_diagnosis_is_not_a_card(verdict, why):
    assert nc.owner_card("любая находка", verdict) is None, why


@pytest.fixture
def env(tmp_path, monkeypatch):
    integ = tmp_path / "integrity_latest.json"
    integ.write_text(json.dumps({"failures": [], "warnings": [
        # 30.09: нужна находка класса decide. Умолчание стало fix, поэтому берём
        # явно помеченную метку владельца (промоут в клинический канон — его решение).
        ["[health] промоут стоит: 3 строк ждут 61д (порог 30д)",
         "самая старая с 2026-07-31"]]}),
        encoding="utf-8")
    monkeypatch.setenv("HEALTH_INTEGRITY_LATEST", str(integ))
    monkeypatch.setenv("HEALTH_PARKED_DB", str(tmp_path / "parked.json"))
    monkeypatch.setenv("HEALTH_NIGHT_CYCLE_RECEIPT", str(tmp_path / "hb.json"))
    # Третий и четвёртый источники цикла уводятся в пустоту. Пропущено 13.09, когда
    # появился третий: `pd.list_open()[0]` здесь брал ПЕРВУЮ карточку, и ею оказалась
    # застрявшая нить из настоящего docs/handoff — тест покраснел не от кода, а от того,
    # что в проекте за сутки прибавилось молчащих нитей (§20 ровно в свою сторону).
    monkeypatch.setenv("HEALTH_HANDOFF_INDEX", str(tmp_path / "нет-указателя.md"))
    monkeypatch.setattr(__import__("agent_reports_db"), "get_agent_report",
                        lambda name, date_str=None, n=1: [])
    set_test_clock("2026-09-13T08:00")
    try:
        yield monkeypatch
    finally:
        clear_test_clock()


def test_unready_goes_to_engineering_queue_not_to_the_table(env):
    """Недоделанный диагноз не ложится на стол — и НЕ пропадает: он в очереди, помечен
    и посчитан. Без счётчика «не смог сформулировать» стало бы тихим отказом
    эскалировать, то есть ровно тем, против чего §13 и построен."""
    env.setattr(night_investigator, "investigate", lambda f: NOT_READY)
    s = nc.run()
    assert s["warn_unready"] == 1 and s["warn_parked"] == 1
    g = pd.list_open()[0]
    assert g["kind"] == "dev_fix", "нечитаемое уехало на стол владельца"
    assert "ДИАГНОЗ НЕ ГОТОВ" in g["summary"]


def test_ready_goes_to_the_table(env):
    """Негативный контроль: правило не глушит всё подряд — готовый диагноз доезжает."""
    env.setattr(night_investigator, "investigate", lambda f: READY)
    s = nc.run()
    assert s["warn_unready"] == 0 and s["warn_parked"] == 1
    g = pd.list_open()[0]
    assert g["kind"] == "owner_decision" and "Что делаем?" in g["summary"]


@pytest.mark.parametrize("question", ["Открой sample.py", "Исправь sample_check()", "Сверь §13", "pytest упал"])
def test_technical_card_returns_to_engineering(question):
    verdict = dict(READY, owner_ask=dict(READY["owner_ask"], question=question))
    assert nc.owner_card("fictional", verdict) is None


def test_card_frame_uses_requested_language(monkeypatch):
    import i18n
    monkeypatch.setattr(i18n, "lang_of", lambda *a, **k: "en")
    verdict = dict(READY, owner_ask={"subject": "The example report has not been built.", "question": "Continue the example project?", "options": [
        {"label": "Continue", "cost": "one hour"}, {"label": "Wait", "cost": "a later result"}]})
    card = nc.owner_card("fictional", verdict)
    assert "Options:" in card and "If you do not reply" in card
