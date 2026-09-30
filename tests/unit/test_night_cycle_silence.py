"""Умолчание по молчанию в ночном цикле: КОМУ выдаётся тройка, а кому — нет.

Решение владельца 2026-09-13 (вариант В): молчание = делегирование, но НЕ для
медицинского и необратимого. Граница здесь структурная, и она проверяется тем,
что карточка, которой умолчание не положено, уходит в стор БЕЗ срока — то есть
sweep_defaults её не увидит никогда, сколько бы лет ни прошло.

night_investigator замокан: маршрут проверяем детерминированно, без LLM.
"""
import datetime as _dt
import json

import pytest

import night_cycle as nc
import parked_decisions as pd
from _time_inject import set_test_clock, clear_test_clock

TODAY = _dt.date(2026, 8, 2)


@pytest.fixture
def env(tmp_path, monkeypatch):
    integ = tmp_path / "integrity_latest.json"
    integ.write_text(json.dumps({"failures": ["одна находка"]}), encoding="utf-8")
    monkeypatch.setenv("HEALTH_INTEGRITY_LATEST", str(integ))
    # Третий источник цикла (нити без движения) уводится в пустой указатель:
    # иначе run() читал бы настоящий docs/handoff и историю git, и вердикт теста
    # зависел бы от состояния проекта в день прогона (§20).
    monkeypatch.setenv("HEALTH_HANDOFF_INDEX", str(tmp_path / "нет-указателя.md"))
    monkeypatch.setenv("HEALTH_PARKED_DB", str(tmp_path / "parked.json"))
    monkeypatch.setenv("HEALTH_NIGHT_CYCLE_RECEIPT", str(tmp_path / "hb.json"))
    # Четвёртый источник (возврат красного потока) уводится в пустой список отчётов.
    # Иначе run() читал бы НАСТОЯЩУЮ таблицу agent_reports, и две красные ночи в проде
    # завели бы здесь лишнюю карточку — вердикт теста менялся бы от состояния базы (§20).
    monkeypatch.setattr(__import__("agent_reports_db"), "get_agent_report",
                        lambda name, date_str=None, n=1: [])
    # Исполнитель умолчания для категории вердикта (23.09): без него тройка не выдаётся.
    ran = []
    monkeypatch.setattr(nc, "DEFAULT_EXECUTORS", {"ops_config": lambda rec: ran.append(rec["id"]) or True})
    set_test_clock("2026-08-02T09:00")
    try:
        yield ran
    finally:
        clear_test_clock()


def _verdict(**over):
    v = {
        "class": "owner_decision",
        "diagnosis": "d",
        "action": {"category": "ops_config", "writes": ["logs/x.json"],
                   "irreversible": False},
        "owner_ask": {
            "subject": "учебная проверка падает", "question": "чинить или оставить?",
            "options": [{"label": "чинить", "cost": "полчаса"},
                        {"label": "оставить", "cost": "шум остаётся"}],
            "recommended": "чинить",
            "rollback": "git revert коммита фикса",
        },
    }
    v.update(over)
    return v


def test_full_verdict_gets_the_trio(env):
    trio = nc._silence_trio(_verdict(), TODAY)
    assert trio["default"] == "чинить"
    assert trio["rollback"] == "git revert коммита фикса"
    assert trio["auto_after"] == TODAY + _dt.timedelta(days=nc.SILENCE_DAYS)
    assert trio["executor"] == "ops_config"


def test_no_executor_no_trio(env, monkeypatch):
    """23.09: вариант, который нечем исполнить, молчанием не решается."""
    monkeypatch.setattr(nc, "DEFAULT_EXECUTORS", {})
    assert nc._silence_trio(_verdict(), TODAY) == {}


def test_irreversible_never_gets_the_trio(env):
    """«НЕ применяется к необратимому» — слова владельца, записанные кодом."""
    v = _verdict()
    v["action"]["irreversible"] = True
    assert nc._silence_trio(v, TODAY) == {}


def test_owner_domain_write_never_gets_the_trio(env):
    """Канон/порог — его оракул. Молчание не делает систему оракулом."""
    v = _verdict()
    v["action"]["writes"] = ["health.db:lab_results"]
    assert nc._silence_trio(v, TODAY) == {}


def test_missing_rollback_blocks_the_trio(env):
    """Нет отката — нет умолчания: применять то, что нечем отменить, нельзя."""
    v = _verdict()
    v["owner_ask"]["rollback"] = None
    assert nc._silence_trio(v, TODAY) == {}


def test_recommended_must_be_one_of_the_options(env):
    """Рекомендация мимо списка — признак, что судья сочинял, а не выбирал."""
    v = _verdict()
    v["owner_ask"]["recommended"] = "третий путь"
    assert nc._silence_trio(v, TODAY) == {}


def test_unknown_category_blocks_the_trio(env):
    """«Не знаем, что это» ≠ «знаем, что безобидно»."""
    v = _verdict()
    v["action"] = {}
    assert nc._silence_trio(v, TODAY) == {}


def test_card_without_trio_is_unsweepable_forever(env, monkeypatch):
    """Сквозной: карточка без отката паркуется и НЕ закрывается через годы.
    Это и есть проверка границы в точке потребления, а не в предикате."""
    v = _verdict()
    v["owner_ask"]["rollback"] = None
    monkeypatch.setattr(nc.night_investigator, "investigate", lambda f: v)
    nc.run()
    assert [r["id"] for r in pd.list_open()], "карточка должна лежать на столе"
    assert pd.sweep_defaults(today=_dt.date(2030, 1, 1), execute=lambda r: True) == []


def test_card_with_trio_closes_itself_and_owner_is_told(env, monkeypatch):
    """Сквозной: полная карточка через SILENCE_DAYS закрывается, и владельцу
    об этом СКАЗАНО — молча применённое умолчание было бы ящиком в стол."""
    monkeypatch.setattr(nc.night_investigator, "investigate", lambda f: _verdict())
    nc.run()
    told = []
    monkeypatch.setattr(nc, "_announce_defaults", lambda s: told.extend(s))
    set_test_clock("2026-08-16T09:00")
    hb = nc.run()
    assert hb["defaults_applied"] == 1
    assert env, "вариант обязан быть ИСПОЛНЕН, а не только записан"
    assert told and "решено умолчанием 16.08.2026" in told[0]["decision"]
    assert "git revert коммита фикса" in told[0]["decision"]



def test_old_trio_without_executor_is_dropped(env, monkeypatch):
    """Карточки с тройкой до 23.09 (без исполнителя) теряют «решится само» при проходе
    цикла и остаются на столе — обещание, которое нечем исполнить, снимается."""
    import parked_decisions as _pd
    data = {"legacy": {"created": "2026-07-30", "status": "open", "kind": "owner_decision",
                       "summary": "s", "decision": None, "resolved_at": None,
                       "defer_until": None, "default": "Заблокировать",
                       "rollback": "разблокировать", "auto_after": "2026-08-13"}}
    _pd._save(data)
    monkeypatch.setattr(nc.night_investigator, "investigate", lambda f: {"class": "transient"})
    hb = nc.run()
    rec = _pd.get("legacy")
    assert hb["defaults_dropped"] == 1 and hb["defaults_applied"] == 0
    assert rec["status"] == "open" and "auto_after" not in rec
