"""Живость ночной задачи тестов и несвежий вход красной серии (нить nightly-liveness, 23.09).

ЧТО ДОКАЗЫВАЕТСЯ. (1) Последний плановый запуск выводится из живого плиста, а не
из литерала: ежедневная и еженедельная формы; неподдержанные формы — None, не
«свежо». (2) Датчик живости звенит, когда итог ночи не покрывает последний запуск,
и звенит «не судимо», когда расписание не выводится. (3) Датчик красной серии на
несвежем входе отвечает «не знаю», а не пересматривает старые зелёные ночи.

ЧЕГО НЕ ДОКАЗЫВАЕТСЯ. Что launchd действительно запустит задачу по плисту — это
вопрос launchd; судится только след, который задача обязана оставить.
"""
from __future__ import annotations

import json
import plistlib
from datetime import datetime

import pytest

pytestmark = pytest.mark.unit

import plist_env_liveness as pl

import infra_config as _ic
_PRIMARY = _ic.PRIMARY_HOST  # основная машина установки, не литерал владельца

LABEL = "com.larry.health.test-suite"
MORNING = datetime(2026, 9, 23, 7, 50)      # среда, монитор 07:50


def _plist(d, cal=None, **extra):
    body = {"Label": LABEL, **extra}
    if cal is not None:
        body["StartCalendarInterval"] = cal
    (d / f"{LABEL}.plist").write_bytes(plistlib.dumps(body))
    return d


# ── (1) последний плановый запуск ─────────────────────────────────────────────

def test_ежедневный_запуск_сегодня_в_полночь(tmp_path):
    d = _plist(tmp_path, {"Hour": 0, "Minute": 0})
    assert pl.last_scheduled_fire(LABEL, MORNING, d) == datetime(2026, 9, 23, 0, 0)


def test_ежедневный_запуск_позже_текущего_времени_это_вчера(tmp_path):
    d = _plist(tmp_path, {"Hour": 23, "Minute": 0})
    assert pl.last_scheduled_fire(LABEL, MORNING, d) == datetime(2026, 9, 22, 23, 0)


@pytest.mark.parametrize("launchd_sunday", [0, 7])
def test_еженедельный_запуск_воскресенье_оба_кода(tmp_path, launchd_sunday):
    d = _plist(tmp_path, {"Weekday": launchd_sunday, "Hour": 8, "Minute": 0})
    assert pl.last_scheduled_fire(LABEL, MORNING, d) == datetime(2026, 9, 20, 8, 0)


def test_еженедельный_в_тот_же_день_но_позже_это_неделю_назад(tmp_path):
    d = _plist(tmp_path, {"Weekday": 3, "Hour": 9, "Minute": 0})   # среда 09:00
    assert pl.last_scheduled_fire(LABEL, MORNING, d) == datetime(2026, 9, 16, 9, 0)


@pytest.mark.parametrize("cal,extra", [
    (None, {"StartInterval": 3600}),
    ([{"Hour": 0, "Minute": 0}, {"Hour": 12}], {}),   # список, где один триггер не выводится
    ({"Hour": 0}, {}),                    # launchd: без Minute = каждую минуту часа
    ({"Month": 1, "Day": 1, "Hour": 0, "Minute": 0}, {}),
])
def test_неподдержанная_форма_не_судима(tmp_path, cal, extra):
    d = _plist(tmp_path, cal, **extra)
    assert pl.last_scheduled_fire(LABEL, MORNING, d) is None
    assert pl.artifact_covers_last_fire(LABEL, "2026-09-23", MORNING, d) is None


def test_список_триггеров_берёт_самый_поздний(tmp_path):
    """Колокол: пять раз в день. В 07:50 последний запуск — вчера 20:00."""
    d = _plist(tmp_path, [{"Hour": h, "Minute": 0} for h in (8, 11, 14, 17, 20)])
    assert pl.last_scheduled_fire(LABEL, MORNING, d) == datetime(2026, 9, 22, 20, 0)
    assert pl.last_scheduled_fire(LABEL, datetime(2026, 9, 23, 15, 0), d) == \
        datetime(2026, 9, 23, 14, 0)


@pytest.mark.parametrize("artifact,expected", [
    ("2026-09-23T07:00:00", False),          # квитанция РАНЬШЕ запуска 08:00 того же дня
    ("2026-09-23T08:00:42", True),
    ("2026-09-23T08:00:42+03:00", None),     # с поясом — сверяется в местном времени
])
def test_момент_сверяется_с_запуском_а_не_с_днём(tmp_path, artifact, expected):
    d = _plist(tmp_path, {"Hour": 8, "Minute": 0})
    now = datetime(2026, 9, 23, 9, 0)
    got = pl.artifact_covers_last_fire(LABEL, artifact, now, d)
    if expected is None:   # ответ зависит от пояса машины — проверяем согласие с ручным переводом
        from datetime import datetime as _dt
        local = _dt.fromisoformat(artifact).astimezone().replace(tzinfo=None)
        assert got is (local >= datetime(2026, 9, 23, 8, 0))
    else:
        assert got is expected


def test_нет_плиста_не_судимо(tmp_path):
    assert pl.artifact_covers_last_fire(LABEL, "2026-09-23", MORNING, tmp_path) is None


@pytest.mark.parametrize("artifact,expected", [
    ("2026-09-23", True), ("2026-09-22", False), (None, False)])
def test_артефакт_покрывает_запуск(tmp_path, artifact, expected):
    d = _plist(tmp_path, {"Hour": 0, "Minute": 0})
    assert pl.artifact_covers_last_fire(LABEL, artifact, MORNING, d) is expected


# ── (2) датчик живости ночи ───────────────────────────────────────────────────

def _sensor(monkeypatch, tmp_path, rows, cal={"Hour": 0, "Minute": 0}, host=_PRIMARY):
    import integrity_tests as I
    cap = []
    monkeypatch.setattr(I, "warn", lambda n, d="": cap.append((n, d)))
    la = _plist(tmp_path, cal) if cal is not None else tmp_path
    res = I.check_nightly_suite_liveness(rows=rows, now=MORNING, la_dir=la, host=host)
    return res, cap


def test_ночь_состоялась_молчание(monkeypatch, tmp_path):
    res, cap = _sensor(monkeypatch, tmp_path, [{"date": "2026-09-23"}])
    assert cap == [] and "2026-09-23" in res


def test_ночь_пропущена_названа(monkeypatch, tmp_path):
    res, cap = _sensor(monkeypatch, tmp_path, [{"date": "2026-09-22"}])
    assert len(cap) == 1 and "не состоялся" in cap[0][0] and "2026-09-22" in cap[0][0]


def test_итога_нет_вовсе_назван(monkeypatch, tmp_path):
    res, cap = _sensor(monkeypatch, tmp_path, [])
    assert len(cap) == 1 and "нет вовсе" in cap[0][0]


def test_не_судимо_не_молчит(monkeypatch, tmp_path):
    """Плиста нет — «не знаю» обязано звучать, а не читаться как «прогон был»."""
    res, cap = _sensor(monkeypatch, tmp_path, [{"date": "2026-09-23"}], cal=None)
    assert len(cap) == 1 and "не судима" in cap[0][0]


def test_warn_доходит_до_владельца():
    """Доставка, а не только детект. Утренний отчёт `morning_report.py` выведен из
    расписания 13.07 — его «блок тестов» до владельца не доезжает вовсе (замер 23.09).
    Единственный канал этого факта к человеку — триаж warn-уровня. Расширят MUTE так,
    что он заглушит пропуск ночи, — этот тест покраснеет."""
    ta = pytest.importorskip("triage_agent")
    labels = ["ночной прогон тестов не состоялся (последний итог: 2026-09-22)",
              "ночной прогон тестов: живость не судима"]
    assert len(ta.classify_warnings([(l, "d") for l in labels])) == len(labels)
    # Решение владельца 30.09: это инженерная находка (перезапустить прогон может только
    # сессия), класс fix. Слепых ночей это не добавляет: ночной цикл читает тот же
    # integrity_latest.json КАЖДУЮ ночь и паркует fix в инженерную очередь сразу, а
    # каденция триажа с 28.09 до человека технику не везёт вовсе.
    assert {ta.warn_class(l) for l in labels} == {"fix"}


def test_не_studio_пропуск(monkeypatch, tmp_path):
    res, cap = _sensor(monkeypatch, tmp_path, [], host="MacBook.local")
    assert res is None and cap == []


# ── (3) красная серия на несвежем входе ───────────────────────────────────────

def _night(date_str, regression):
    return {"date": date_str,
            "findings": json.dumps({"date": date_str, "regression": regression,
                                    "regression_ids": []})}


def test_красная_серия_на_несвежем_входе_не_знает(tmp_path, monkeypatch):
    """Задача умерла после двух зелёных ночей: раньше — вечное «красных нет»."""
    monkeypatch.setenv("HEALTH_PARKED_DB", str(tmp_path / "parked.json"))
    import health_db  # noqa: F401 — первым: agent_reports_db кольцуется, если он первый
    import agent_reports_db
    import morning_test_summary as mts
    import night_cycle as nc
    rows = [_night("2026-09-20", 0), _night("2026-09-19", 0)]
    monkeypatch.setattr(agent_reports_db, "get_agent_report",
                        lambda name, date_str=None, n=1: rows[:n])
    la = _plist(tmp_path, {"Hour": 0, "Minute": 0})
    real = mts.summary_covers_last_run
    monkeypatch.setattr(mts, "summary_covers_last_run",
                        lambda d, now=None, la_dir=None: real(d, now=MORNING, la_dir=la))
    assert nc._red_tests_to_repair() == {"red_parked": None, "red_retired": None}
    rows[:] = [_night("2026-09-23", 0), _night("2026-09-22", 0)]
    assert nc._red_tests_to_repair() == {"red_parked": 0, "red_retired": 0}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# ── Ежемесячная форма (23.09, нить cadence-thresholds: консилиум 1-го числа) ─────
@pytest.mark.parametrize("cal,want", [
    ({"Day": 1, "Hour": 4, "Minute": 0}, datetime(2026, 9, 1, 4, 0)),
    ({"Day": 23, "Hour": 9, "Minute": 0}, datetime(2026, 8, 23, 9, 0)),   # сегодня, но позже
    ({"Day": 31, "Hour": 4, "Minute": 0}, datetime(2026, 8, 31, 4, 0)),   # в сентябре 31-го нет
])
def test_ежемесячный_запуск(tmp_path, cal, want):
    d = _plist(tmp_path, cal)
    assert pl.last_scheduled_fire(LABEL, MORNING, d) == want
