"""Порядок ночного ремонта: первое место — самой вредной карточке, остальные — старейшим
(нить repair-order, решение владельца 02.10)."""
import ast
from pathlib import Path

import pytest

import night_repair as nr

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]

HARM = {"check_reco_repeats_fresh_lab": "person", "check_calendar_freshness": "data",
        "check_producer_registry": "system"}
INTEGRITY = {
    "warnings": [["реестр производителей протух", "9 ключей"],
                 ["календарь устарел", "кэш 48ч"],
                 ["совет сдать уже сданное", "B12 сдан 77д назад"],
                 ["сбой: необработанная ошибка бота", "3 раз за сутки у .health_secrets; последний: x"],
                 ["сбой: scheduled.discover_pgs_monthly", "1 раз за сутки у .health_secrets; последний: y"]],
    "sensor_of": {"реестр производителей протух": "check_producer_registry",
                  "календарь устарел": "check_calendar_freshness",
                  "совет сдать уже сданное": "check_reco_repeats_fresh_lab",
                  "сбой: необработанная ошибка бота": "check_fault_journal",
                  "сбой: scheduled.discover_pgs_monthly": "check_fault_journal"},
}


def _card(label, created):
    import finding_identity
    return ({"id": "warn:" + finding_identity.finding_key(label), "created": created}, "улики")


def test_most_harmful_first_then_oldest():
    todo = [_card("реестр производителей протух", "2026-09-24"),   # старейшая, система
            _card("календарь устарел", "2026-10-01"),               # данные
            _card("совет сдать уже сданное", "2026-10-01")]         # человек
    picked = [c["id"] for c, _ in nr.pick_cards(todo, INTEGRITY, HARM, n=2)]
    assert picked == [todo[2][0]["id"], todo[0][0]["id"]]           # по старшинству было бы [0, 1]


def test_bot_fault_is_person_and_repeats_break_ties():
    todo = [_card("сбой: scheduled.discover_pgs_monthly", "2026-09-30"),
            _card("совет сдать уже сданное", "2026-10-01"),
            _card("сбой: необработанная ошибка бота", "2026-10-02")]
    first = nr.pick_cards(todo, INTEGRITY, HARM, n=2)[0][0]["id"]
    assert first == todo[2][0]["id"]                                # человек, и 3 раза за сутки против 0


def test_unknown_card_is_system_and_short_queue_untouched():
    todo = [_card("находки этой больше нет", "2026-09-01"), _card("календарь устарел", "2026-10-01")]
    assert nr.pick_cards(todo, INTEGRITY, HARM, n=2) == todo
    assert nr._card_harm(todo[0][0], INTEGRITY, HARM) == (2, 0)


def test_monitor_records_which_sensor_raised_each_finding():
    """integrity_tests.check/warn пишут sensor_of — без него порядок не знает датчика."""
    src = (ROOT / "integrity_tests.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    tree.body = [n for n in tree.body if getattr(n, "name", None) in {"check", "warn"}]
    env = {"PASS": 0, "FAIL": 0, "WARN": 0, "_failures": [], "_warnings": [], "_code_failures": [],
           "_critical": [], "_sensor_of": {}, "_current_sensor": "", "JSON_OUTPUT": True,
           "MACHINE_SCOPE": False, "_not_judged_here": [], "_host_judged_here": lambda: True}
    exec(compile(tree, "integrity_tests.py", "exec"), env)

    def check_example():
        env["warn"]("пример находки", "деталь")

    def check_failing():
        raise AssertionError("упало")

    env["check"]("пример", check_example)
    env["check"]("падение", check_failing)
    assert env["_sensor_of"] == {"пример находки": "check_example", "падение": "check_failing"}
