"""Сигнал возврата: решение «не строить fix_applier» имеет машинное условие пересмотра.

ЧТО ДОКАЗЫВАЕТСЯ. Что две красные ночи подряд поднимают карточку владельцу ровно
один раз; что одна красная ночь — не повод; что ночь считается красной по числу
`regression`, а НЕ по обрезанному пятью списку имён; что нечитаемый отчёт даёт
отказ, а не тихий ноль.

ЧЕГО НЕ ДОКАЗЫВАЕТСЯ. Что применителя пора строить: это решение владельца, у
машины на него оракула нет и быть не может. Сигнал только не даёт отложенному
решению превратиться в забытое.

Чтение отчётов подменяется: предмет теста — маршрут находки, а не SQL к
`agent_reports` (у него свой дом, `agent_reports_db`).
"""
import json

import pytest

# Без маркера ночной слой (`pytest tests/unit/ -m unit`) НЕ берёт файл — замер
# 14.09: 1904 теста из 4267 отсеиваются так каждую ночь. Оракул, зелёный только
# при прямом вызове по пути, ночью не существует (§20 наизнанку).
pytestmark = pytest.mark.unit

import health_db  # noqa: F401 — первым: agent_reports_db кольцуется, если он первый
import agent_reports_db
import night_cycle as nc


def _night(date_str, regression, ids=()):
    return {"date": date_str,
            "findings": json.dumps({"date": date_str, "regression": regression,
                                    "regression_ids": list(ids)}, ensure_ascii=False)}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_PARKED_DB", str(tmp_path / "parked.json"))
    # Свежесть входа — отдельный предмет (test_nightly_suite_liveness.py); здесь
    # вход свеж явно, а не по совпадению дат с живым плистом машины (§20).
    import morning_test_summary
    monkeypatch.setattr(morning_test_summary, "summary_covers_last_run",
                        lambda *a, **k: True)
    yield tmp_path


def _patch(monkeypatch, rows):
    monkeypatch.setattr(agent_reports_db, "get_agent_report",
                        lambda name, date_str=None, n=1: rows[:n])


def _store(env):
    p = env / "parked.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def test_two_red_nights_raise_the_card(env, monkeypatch):
    _patch(monkeypatch, [_night("2026-10-02", 3, ["t::a", "t::b"]),
                         _night("2026-10-01", 1, ["t::a"])])
    out = nc._red_stream_returned()
    assert out == {"red_nights": 2, "red_returned": True}
    assert nc.RED_STREAM_GATE in _store(env)


def test_one_red_night_is_not_a_return(env, monkeypatch):
    """Порог существует ради этого случая: одна ночь — флак или свежий коммит."""
    _patch(monkeypatch, [_night("2026-10-02", 3, ["t::a"]),
                         _night("2026-10-01", 0)])
    out = nc._red_stream_returned()
    assert out["red_returned"] is False
    assert nc.RED_STREAM_GATE not in _store(env)


def test_night_is_red_by_count_not_by_truncated_names(env, monkeypatch):
    """`regression_ids` обрезан пятью в morning_test_summary; счёт идёт по `regression`.

    Ночь без единого имени, но с ненулевым числом — красная. Обратное (считать по
    длине списка) занизило бы поток ровно там, где он самый густой."""
    _patch(monkeypatch, [_night("2026-10-02", 7, []),
                         _night("2026-10-01", 9, [])])
    assert nc._red_stream_returned()["red_returned"] is True
    card = _store(env)[nc.RED_STREAM_GATE]
    assert "2 ночи подряд" in card["summary"]
    assert "Варианты:" in card["summary"] and "Если промолчишь" in card["summary"]
    assert "fix_applier" not in card["summary"] and "§" not in card["summary"]


def test_unreadable_report_is_a_refusal_not_a_zero(env, monkeypatch):
    """§14: нераспознанный вход — отказ инструмента. Тихий ноль здесь означал бы
    «красных нет» и усыпил бы сигнал ровно тогда, когда отчёты сломались."""
    _patch(monkeypatch, [{"date": "2026-10-02", "findings": "{не json"},
                         _night("2026-10-01", 4)])
    out = nc._red_stream_returned()
    assert out["red_returned"] is None
    assert nc.RED_STREAM_GATE not in _store(env)


def test_card_is_raised_once(env, monkeypatch):
    _patch(monkeypatch, [_night("2026-10-02", 3), _night("2026-10-01", 2)])
    nc._red_stream_returned()
    nc._red_stream_returned()
    assert len([k for k in _store(env) if k == nc.RED_STREAM_GATE]) == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
