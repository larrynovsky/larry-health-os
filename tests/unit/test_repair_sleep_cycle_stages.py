"""Характеризация репарации Sleep Cycle: что она чинит и, главное, чего НЕ трогает.

Миграция пишет в боевую базу, поэтому по §15 (влияние на канон снимает освобождение)
с неё спрашивается характеризационный тест наравне с обычным модулем.

Цена ошибки здесь несимметрична: пропустить фальшивую запись — потерять немного точности;
занулить настоящее измерение — уничтожить данные о сне. Поэтому негативных проверок
(«не тронул») здесь больше, чем позитивных.
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.unit

from migrations import repair_sleep_cycle_stages_20260731 as mig
RUN = "test-repair-run"


def _daily_metrics_ddl() -> str:
    """DDL берётся из НАСТОЯЩЕЙ схемы проекта, а не пишется руками.

    Первая редакция этого теста (2026-07-31) объявляла фикстуру из четырёх колонок —
    ровно тех, которые чинила миграция. В боевой схеме колонок стадий четыре, а не две,
    поэтому тест физически не мог заметить недочиненные `sleep_core`/`sleep_awake`:
    фикстура моделировала мою МОДЕЛЬ схемы, а не схему. Оракул, построенный по той же
    памяти, что и код, проверяет согласие автора с самим собой.
    """
    import re
    import sqlite3
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "health_schema.sql"
           ).read_text(encoding="utf-8")
    # Тот же приём, что у соседнего гарда `test_fixture_schema_complete`: схема
    # ИСПОЛНЯЕТСЯ, а DDL читается из sqlite_master. Регекс по тексту здесь хрупок —
    # первая попытка захватила четыре таблицы подряд и упала на sqlite_sequence.
    sql = re.sub(r"^\s*CREATE\s+TABLE\s+sqlite_sequence\s*\([^)]*\)\s*;\s*$",
                 "", sql, flags=re.IGNORECASE | re.MULTILINE)
    mem = sqlite3.connect(":memory:")
    mem.executescript(sql)
    row = mem.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='daily_metrics'").fetchone()
    assert row, "в фикстуре схемы нет daily_metrics — тест пошёл бы на выдуманной таблице"
    ddl = row[0] + ";"
    for col in ("sleep_core", "sleep_awake"):
        assert col in ddl, (
            f"{col} исчез из схемы daily_metrics — фикстура перестала быть настоящей")
    return ddl


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import health_db as hdb

    monkeypatch.setattr(hdb, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(hdb, "METRICS_DIR", tmp_path / "daily_metrics")
    hdb.METRICS_DIR.mkdir(parents=True, exist_ok=True)
    with hdb.get_conn() as conn:
        conn.executescript(_daily_metrics_ddl())
    hdb._ensure_data_repair_log()
    return hdb


def _add(hdb, day, sleep, deep, rem, core=None, awake=None):
    """Пишет запись во ВСЕ три дома сразу — как это происходит в бою."""
    (hdb.METRICS_DIR / f"{day}.json").write_text(json.dumps({"sleep": sleep}, ensure_ascii=False))
    raw = json.dumps({"sleep": sleep}, ensure_ascii=False)
    core = sleep.get("core") if core is None else core
    awake = sleep.get("awake") if awake is None else awake
    with hdb.get_conn() as conn:
        conn.execute(
            "INSERT INTO daily_metrics(date, sleep_total, sleep_deep, sleep_rem,"
            " sleep_core, sleep_awake, raw) VALUES (?,?,?,?,?,?,?)",
            (day, sleep.get("totalSleep"), deep, rem, core, awake, raw))


FAKE = {"totalSleep": 6.25, "deep": 0, "rem": 0, "core": 0, "awake": 0, "source": "Sleep Cycle"}
MEASURED_SC = {"totalSleep": 5.5, "deep": 0.75, "rem": 1.25, "core": 3.5, "awake": 0.25,
           "source": "Sleep Cycle"}
OURA = {"totalSleep": 7.0, "deep": 1.0, "rem": 1.5, "core": 4.5, "awake": 0.5, "source": "Oura"}


# ── Предикат ───────────────────────────────────────────────────────────────

def test_predicate_matches_only_fake_records(env):
    assert mig.is_fake_stage_record(FAKE) is True
    m = mig
    assert m.is_fake_stage_record(MEASURED_SC) is False, "запись Sleep Cycle СО стадиями — настоящая"
    assert m.is_fake_stage_record(OURA) is False, "у Oura нулей не бывает, но проверяем явно"
    assert m.is_fake_stage_record({}) is False
    assert m.is_fake_stage_record(None) is False


def test_partial_zero_is_a_measurement_not_a_hole(env):
    """ЧАСТЬ стадий ноль — это измерение, а не дыра, и предикат обязан требовать ВСЕ четыре.

    «Все четыре нуля» означает «прибор стадии не считает». «Один ноль» означает «в эту
    ночь такой фазы не было» — законное наблюдение.

    Независимо придуманный частичный ноль проверяет различие all и any:
    замена all на any стёрла бы измеренную стадию.
    """
    m = mig
    for holed in (
        dict(OURA, rem=0),                       # ночь без REM у Oura
        dict(MEASURED_SC, awake=0),                  # не просыпался
        dict(FAKE, deep=1.0),                    # Sleep Cycle, но глубокий измерен
    ):
        assert m.is_fake_stage_record(holed) is False, (
            f"частичный ноль принят за дыру: {holed} — репарация занулит измерение")


def test_oura_zero_would_not_be_touched(env):
    """Гипотетический ноль от Oura — НЕ наш случай: у Oura ноль означал бы измерение."""
    m = mig
    oura_zero = dict(OURA, deep=0, rem=0, core=0, awake=0)
    assert m.is_fake_stage_record(oura_zero) is False, (
        "предикат сработал по значению, а не по провенансу — тогда он занулит чужие измерения")


# ── Что делает и чего не делает ────────────────────────────────────────────

def test_dry_run_writes_nothing(env):
    m = mig
    _add(env, "2018-03-01", FAKE, 0.0, 0.0)

    res = m.apply(RUN, write=False)
    assert res["written"] is False
    # 4 стадии в файле · 4 колонки в таблице · 4 ключа внутри raw.
    # До 31.07 здесь стояло `sqlite == 2` — ожидание было списано с литерала в коде,
    # а не выведено из схемы, и потому подтверждало недочинку вместо того, чтобы её ловить.
    assert (res["json"], res["sqlite"], res["raw"]) == (4, 4, 4), res

    assert json.loads((env.METRICS_DIR / "2018-03-01.json").read_text())["sleep"]["deep"] == 0
    with env.get_conn() as conn:
        assert conn.execute("SELECT sleep_deep FROM daily_metrics").fetchone()[0] == 0.0
        assert conn.execute("SELECT COUNT(*) FROM data_repair_log").fetchone()[0] == 0


def test_apply_repairs_both_homes_and_spares_the_rest(env):
    m = mig
    _add(env, "2018-03-01", FAKE, 0.0, 0.0)        # чинится
    _add(env, "2040-04-09", MEASURED_SC, 0.75, 1.25)  # НЕ трогаем
    _add(env, "2020-06-01", OURA, 1.0, 1.5)         # НЕ трогаем

    m.apply(RUN, write=True)

    fixed = json.loads((env.METRICS_DIR / "2018-03-01.json").read_text())["sleep"]
    assert [fixed[k] for k in ("deep", "rem", "core", "awake")] == [None] * 4
    assert fixed["totalSleep"] == 6.25, "репарация тронула измеренное поле"
    assert fixed["source"] == "Sleep Cycle", "репарация потеряла провенанс"

    with env.get_conn() as conn:
        rows = dict((r[0], (r[1], r[2])) for r in conn.execute(
            "SELECT date, sleep_deep, sleep_rem FROM daily_metrics"))
    assert rows["2018-03-01"] == (None, None)
    assert rows["2040-04-09"] == (0.75, 1.25), "затёрты настоящие стадии Sleep Cycle"
    assert rows["2020-06-01"] == (1.0, 1.5), "затёрты измерения Oura"

    untouched = json.loads((env.METRICS_DIR / "2020-06-01.json").read_text())["sleep"]
    assert untouched["deep"] == 1.0


def test_all_three_homes_are_covered(env):
    """Дом, о котором забыли, — это дом, который вернёт нули.

    Домов ТРИ: файл на диске, плоские колонки таблицы и JSON внутри колонки `raw`.
    Третий нашёлся 31.07 уже ПОСЛЕ того, как репарация была объявлена исполненной;
    `longitudinal_analysis` читает именно его, то есть нули оттуда попадают в веру.
    """
    _add(env, "2018-03-01", FAKE, 0.0, 0.0)
    mig.apply(RUN, write=True)

    assert json.loads((env.METRICS_DIR / "2018-03-01.json").read_text())["sleep"]["core"] is None
    with env.get_conn() as conn:
        row = conn.execute(
            "SELECT sleep_deep, sleep_rem, sleep_core, sleep_awake, raw"
            " FROM daily_metrics WHERE date='2018-03-01'").fetchone()
    assert tuple(row)[:4] == (None, None, None, None), "плоские колонки починены не все"
    in_raw = json.loads(row[4])["sleep"]
    assert [in_raw[k] for k in ("deep", "rem", "core", "awake")] == [None] * 4, (
        "колонка raw осталась с нулями — longitudinal возьмёт их в корреляции")
    assert in_raw["totalSleep"] == 6.25 and in_raw["source"] == "Sleep Cycle", (
        "починка raw затёрла измеренные поля или провенанс")


def test_stage_columns_come_from_live_schema(env):
    """Соответствие «стадия → колонка» обязано выводиться из схемы, а не быть литералом.

    Литерал в первой редакции знал две колонки из четырёх, и репарация прошла по половине.
    """
    with env.get_conn() as conn:
        cols = mig.stage_columns(conn)
    assert set(cols) == {"deep", "rem", "core", "awake"}, (
        f"стадии потерялись при выводе из схемы: {cols}")
    assert set(cols.values()) == {"sleep_deep", "sleep_rem", "sleep_core", "sleep_awake"}


def test_every_change_is_logged(env):
    """Незалогированное изменение = необратимое изменение. Счётчики обязаны сойтись."""
    m = mig
    _add(env, "2018-03-01", FAKE, 0.0, 0.0)
    res = m.apply(RUN, write=True)

    with env.get_conn() as conn:
        by_home = dict(conn.execute(
            "SELECT home, COUNT(*) FROM data_repair_log WHERE run_id=? GROUP BY home", (RUN,)))
    assert by_home == {"json:daily_metrics": res["json"],
                       "sqlite:daily_metrics": res["sqlite"],
                       "sqlite:daily_metrics.raw": res["raw"]}, (
        "счётчики по домам не сошлись — какой-то дом чинится без записи в журнал "
        "либо не чинится вовсе")


def test_repair_is_reversible_end_to_end(env):
    """Главное свойство: после отката состояние совпадает с исходным во ВСЕХ ТРЁХ домах."""
    m = mig
    _add(env, "2018-03-01", FAKE, 0.0, 0.0)
    before_json = (env.METRICS_DIR / "2018-03-01.json").read_text()
    with env.get_conn() as conn:
        before_raw = conn.execute(
            "SELECT raw FROM daily_metrics WHERE date='2018-03-01'").fetchone()[0]

    m.apply(RUN, write=True)
    env.revert_repairs(RUN)

    after = json.loads((env.METRICS_DIR / "2018-03-01.json").read_text())["sleep"]
    assert after == json.loads(before_json)["sleep"], "откат не вернул файл в прежний вид"
    with env.get_conn() as conn:
        row = conn.execute(
            "SELECT sleep_deep, sleep_rem, sleep_core, sleep_awake, raw"
            " FROM daily_metrics").fetchone()
    assert tuple(row)[:4] == (0.0, 0.0, 0.0, 0.0), "откат вернул не все колонки"
    assert json.loads(row[4])["sleep"] == json.loads(before_raw)["sleep"], (
        "откат не вернул колонку raw в прежний вид")


def test_rerun_after_apply_finds_nothing(env):
    """Повторный прогон не должен находить работу: иначе журнал распухнет дублями."""
    m = mig
    _add(env, "2018-03-01", FAKE, 0.0, 0.0)
    m.apply(RUN, write=True)

    again = m.apply(RUN + "-2", write=False)
    assert (again["json"], again["sqlite"], again["raw"]) == (0, 0, 0), (
        "репарация не идемпотентна — какой-то дом остался недочиненным")
