"""Упорно красный тест → карточка ночному ремонту (решение владельца 06.10).

ЧТО ДОКАЗЫВАЕТСЯ. Тест, красный две ночи подряд, становится ОДНОЙ карточкой `dev_fix`
с селектором pytest (его ремонт может прогнать оракулом), а вопроса владельцу больше
нет; одна красная ночь — не повод; позеленевший тест снимает свою карточку, но только
по полному списку; несвежий или нечитаемый отчёт — отказ, а не тихий ноль.

Чтение отчётов подменяется: предмет — маршрут находки, а не SQL к `agent_reports`.
"""
import json

import pytest

pytestmark = pytest.mark.unit

import health_db  # noqa: F401 — первым: agent_reports_db кольцуется, если он первый
import agent_reports_db
import night_cycle as nc

A = "unit/tests.unit.test_x::test_a[True]"
B = "integration/tests.integration.test_y::test_b"
SEL_A = "test:tests/unit/test_x.py::test_a[True]"
SEL_B = "test:tests/integration/test_y.py::test_b"


def _night(date_str, regression, ids=()):
    return {"date": date_str,
            "findings": json.dumps({"date": date_str, "regression": regression,
                                    "regression_ids": list(ids)}, ensure_ascii=False)}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_PARKED_DB", str(tmp_path / "parked.json"))
    import morning_test_summary
    monkeypatch.setattr(morning_test_summary, "summary_covers_last_run", lambda *a, **k: True)
    yield tmp_path


def _patch(monkeypatch, rows):
    monkeypatch.setattr(agent_reports_db, "get_agent_report",
                        lambda name, date_str=None, n=1: rows[:n])


def _store(env):
    p = env / "parked.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def test_two_red_nights_make_a_repair_card_not_an_owner_question(env, monkeypatch):
    _patch(monkeypatch, [_night("2026-10-06", 2, [A, B]), _night("2026-10-05", 1, [A])])
    assert nc._red_tests_to_repair() == {"red_parked": 1, "red_retired": 0}
    st = _store(env)
    assert set(st) == {SEL_A}, "B красный одну ночь — не карточка"
    assert st[SEL_A]["kind"] == "dev_fix"
    assert "tests/unit/test_x.py::test_a[True]" in st[SEL_A]["summary"]
    assert not [g for g in st.values() if g["kind"] == "owner_decision"]


def test_selector_is_runnable_by_repair_oracle(env):
    """Ночной ремонт принимает оракулом только `tests/...` — id отчёта он бы отверг."""
    import night_repair
    sel = SEL_A[len(nc.RED_TEST_PREFIX):]
    assert night_repair._oracle_selectors(sel) == [sel]


def test_card_is_raised_once(env, monkeypatch):
    _patch(monkeypatch, [_night("2026-10-06", 1, [A]), _night("2026-10-05", 1, [A])])
    nc._red_tests_to_repair()
    nc._red_tests_to_repair()
    assert list(_store(env)) == [SEL_A]


def test_green_test_retires_its_card(env, monkeypatch):
    rows = [_night("2026-10-05", 1, [A]), _night("2026-10-04", 1, [A])]
    _patch(monkeypatch, rows)
    nc._red_tests_to_repair()
    rows[:] = [_night("2026-10-06", 0, []), _night("2026-10-05", 1, [A])]
    assert nc._red_tests_to_repair() == {"red_parked": 0, "red_retired": 1}
    rec = _store(env)[SEL_A]
    assert rec["status"] == "resolved" and rec["decided_by"] == "not_on_desk"


def test_truncated_list_retires_nothing(env, monkeypatch):
    """Старый отчёт: имён меньше, чем красных — живой красный мог не попасть в список."""
    rows = [_night("2026-10-05", 1, [A]), _night("2026-10-04", 1, [A])]
    _patch(monkeypatch, rows)
    nc._red_tests_to_repair()
    rows[:] = [_night("2026-10-06", 7, [B]), _night("2026-10-05", 1, [A])]
    assert nc._red_tests_to_repair()["red_retired"] is None
    assert _store(env)[SEL_A]["status"] == "open"


def test_unreadable_report_is_a_refusal_not_a_zero(env, monkeypatch):
    _patch(monkeypatch, [{"date": "2026-10-06", "findings": "{не json"}, _night("2026-10-05", 4, [A])])
    assert nc._red_tests_to_repair() == {"red_parked": None, "red_retired": None}
    assert _store(env) == {}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
