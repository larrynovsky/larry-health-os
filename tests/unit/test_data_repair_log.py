"""Журнал репарации: обратимость исполнима, а не заявлена.

Ошибочные записи исправляются через журнал старых значений. Обоснование — §13:
если уникальная информация доказуемо не теряется, операция перестаёт быть необратимой
и становится ступенью 1 (авто-ремонт с логом) вместо ступени 3 (эскалация к человеку).

Всё это держится на ОДНОМ свойстве: по журналу можно вернуть прежнее состояние.
Поэтому оракул здесь — полный цикл repair → revert → сверка с исходным, а не «в журнале
появились строки». Проверять наличие записей значило бы проверять прокси вместо предмета.

Домов ДВА (таблица и JSON-файлы), и откат обязан работать в обоих: чинить один дом,
пока второй возвращает нули через `migrate_all_json`, — это работа, которую стирает
следующий утренний бриф.
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.unit

RUN = "test-run-2026-07-31"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Изолированная БД + изолированный каталог JSON-дома."""
    import health_db as hdb

    monkeypatch.setattr(hdb, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(hdb, "METRICS_DIR", tmp_path / "daily_metrics")
    hdb.METRICS_DIR.mkdir(parents=True, exist_ok=True)
    with hdb.get_conn() as conn:
        conn.executescript("""
            CREATE TABLE daily_metrics (
                date TEXT PRIMARY KEY, sleep_total REAL, sleep_deep REAL, sleep_rem REAL
            );
        """)
    hdb._ensure_data_repair_log()
    return hdb


# ── Предмет: обратимость ───────────────────────────────────────────────────

def test_sqlite_repair_is_reversible(db):
    """Полный цикл в доме-таблице: ноль → NULL → обратно ровно ноль."""
    with db.get_conn() as conn:
        conn.execute("INSERT INTO daily_metrics VALUES ('2018-03-01', 7.15, 0.0, 0.0)")

    with db.get_conn() as conn:
        for field in ("sleep_deep", "sleep_rem"):
            db.log_repair(conn, run_id=RUN, home="sqlite:daily_metrics",
                          entity="2018-03-01", field=field,
                          old_value=0.0, new_value=None,
                          reason="sleep_cycle_no_stages")
        conn.execute("UPDATE daily_metrics SET sleep_deep=NULL, sleep_rem=NULL WHERE date='2018-03-01'")

    with db.get_conn() as conn:
        row = conn.execute("SELECT sleep_deep, sleep_rem, sleep_total FROM daily_metrics").fetchone()
    assert tuple(row) == (None, None, 7.15), "репарация не сработала или задела чужое поле"

    assert db.revert_repairs(RUN) == 2

    with db.get_conn() as conn:
        row = conn.execute("SELECT sleep_deep, sleep_rem, sleep_total FROM daily_metrics").fetchone()
    assert tuple(row) == (0.0, 0.0, 7.15), "откат не восстановил прежнее состояние — обратимость мнимая"


def test_json_repair_is_reversible(db):
    """Полный цикл во втором доме — файле. Без него откат неполон."""
    path = db.METRICS_DIR / "2018-03-01.json"
    path.write_text(json.dumps({
        "sleep": {"totalSleep": 7.15, "deep": 0, "rem": 0, "source": "Sleep Cycle"}
    }, ensure_ascii=False))

    with db.get_conn() as conn:
        db.log_repair(conn, run_id=RUN, home="json:daily_metrics",
                      entity="2018-03-01", field="deep",
                      old_value=0, new_value=None, reason="sleep_cycle_no_stages")
    doc = json.loads(path.read_text())
    doc["sleep"]["deep"] = None
    path.write_text(json.dumps(doc, ensure_ascii=False))

    assert json.loads(path.read_text())["sleep"]["deep"] is None

    assert db.revert_repairs(RUN) == 1
    after = json.loads(path.read_text())
    assert after["sleep"]["deep"] == 0, "откат в JSON-доме не восстановил значение"
    assert after["sleep"]["totalSleep"] == 7.15, "откат затёр соседние поля файла"
    assert after["sleep"]["source"] == "Sleep Cycle", "откат потерял провенанс"


def test_revert_is_idempotent(db):
    """Повторный откат не должен «откатывать откат»."""
    with db.get_conn() as conn:
        conn.execute("INSERT INTO daily_metrics VALUES ('2018-03-02', 6.0, 0.0, 0.0)")
        db.log_repair(conn, run_id=RUN, home="sqlite:daily_metrics", entity="2018-03-02",
                      field="sleep_deep", old_value=0.0, new_value=None,
                      reason="sleep_cycle_no_stages")
        conn.execute("UPDATE daily_metrics SET sleep_deep=NULL WHERE date='2018-03-02'")

    assert db.revert_repairs(RUN) == 1
    assert db.revert_repairs(RUN) == 0, "второй вызов снова тронул данные — откат не идемпотентен"


# ── Различие, ради которого всё затевалось ─────────────────────────────────

def test_zero_and_absent_are_distinguishable_in_log(db):
    """«Было 0.0» и «поля не было» обязаны различаться в журнале.

    Хранение сырым текстом схлопнуло бы их, и откат вернул бы не то, что было.
    """
    with db.get_conn() as conn:
        conn.execute("INSERT INTO daily_metrics VALUES ('2018-03-03', 6.0, 0.0, NULL)")
        db.log_repair(conn, run_id=RUN, home="sqlite:daily_metrics", entity="2018-03-03",
                      field="sleep_deep", old_value=0.0, new_value=None, reason="r")
        db.log_repair(conn, run_id=RUN, home="sqlite:daily_metrics", entity="2018-03-03",
                      field="sleep_rem", old_value=None, new_value=1.5, reason="r")
        stored = [r[0] for r in conn.execute(
            "SELECT old_value FROM data_repair_log ORDER BY id")]
    assert stored == ["0.0", "null"], f"ноль и отсутствие неразличимы в журнале: {stored}"


# ── Отказы на границе ──────────────────────────────────────────────────────

def test_noop_repair_is_rejected(db):
    """Запись «изменил X на X» — шум, который делает журнал недоверяемым."""
    with db.get_conn() as conn:
        with pytest.raises(ValueError, match="совпадают"):
            db.log_repair(conn, run_id=RUN, home="sqlite:daily_metrics", entity="d",
                          field="sleep_deep", old_value=0.0, new_value=0.0, reason="r")


def test_empty_required_field_is_rejected_by_schema(db):
    """CHECK-констрейнты — нативная защита SQLite, а не проверка в Python."""
    import sqlite3
    with db.get_conn() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO data_repair_log(run_id, home, entity, field, old_value, new_value, reason)"
                " VALUES ('r','','d','f','0','null','')"
            )


def test_unknown_home_refuses_instead_of_skipping(db):
    """Незнакомый дом обязан валить откат, а не молча пропускать его часть."""
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO data_repair_log(run_id, home, entity, field, old_value, new_value, reason)"
            " VALUES (?,?,?,?,?,?,?)",
            (RUN, "redis:whatever", "2018-03-04", "sleep_deep", "0.0", "null", "r"))
    with pytest.raises(ValueError, match="неизвестные дома"):
        db.revert_repairs(RUN)


def test_fixture_and_code_define_the_same_columns(db, tmp_path):
    """Определение таблицы живёт в ДВУХ домах — `health_db` и фикстуре тестов.

    Дубль заведён осознанно (так устроен ручной слой фикстур в проекте), но гард дрейфа
    `test_fixture_schema_complete` сверяет только ИМЕНА таблиц. Колонки разъехались бы молча,
    и характеризация шла бы на схеме, которой нет в бою. Раз дубль мой — ратчет тоже мой.
    """
    import re
    import sqlite3
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    sql = (root / "tests" / "fixtures" / "health_schema.sql").read_text(encoding="utf-8")
    m = re.search(r"CREATE TABLE IF NOT EXISTS data_repair_log\s*\((.+?)\n\);", sql, re.S)
    assert m, "в фикстуре нет data_repair_log — гард дрейфа покраснеет ночью после деплоя"

    mem = sqlite3.connect(":memory:")
    mem.execute(f"CREATE TABLE data_repair_log ({m.group(1)})")
    from_fixture = {r[1] for r in mem.execute("PRAGMA table_info(data_repair_log)")}

    with db.get_conn() as conn:
        from_code = {r[1] for r in conn.execute("PRAGMA table_info(data_repair_log)")}

    assert from_fixture == from_code, (
        f"дома схемы разъехались: только в фикстуре {sorted(from_fixture - from_code)}, "
        f"только в коде {sorted(from_code - from_fixture)}"
    )


def test_field_outside_schema_refuses(db):
    """Имя поля идёт в SQL идентификатором — оно обязано сверяться с живой схемой."""
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO data_repair_log(run_id, home, entity, field, old_value, new_value, reason)"
            " VALUES (?,?,?,?,?,?,?)",
            (RUN, "sqlite:daily_metrics", "2018-03-05",
             "sleep_deep = 0 WHERE 1=1 --", "0.0", "null", "r"))
    with pytest.raises(ValueError, match="outside table schema"):
        db.revert_repairs(RUN)


def test_memory_and_tasks_rows_are_reversible(db):
    """05.10.2026: ремонт следов потока теста — строка memory и задача по номеру строки."""
    with db.get_conn() as conn:
        conn.executescript("""
            CREATE TABLE memory (id INTEGER PRIMARY KEY, category TEXT, value TEXT);
            CREATE TABLE tasks (id INTEGER PRIMARY KEY, status TEXT, resolved_text TEXT);
            INSERT INTO memory VALUES (1, 'profile_update', 'junk');
            INSERT INTO tasks VALUES (7, 'open', NULL);
        """)
        for home, ent, field, old, new in (("sqlite:memory", "1", "value", "junk", "orig"),
                                           ("sqlite:tasks", "7", "status", "open", "dismissed"),
                                           ("sqlite:tasks", "7", "resolved_text", None, "made by a test")):
            db.log_repair(conn, run_id=RUN, home=home, entity=ent, field=field,
                          old_value=old, new_value=new, reason="test thread leak")
        conn.execute("UPDATE memory SET value='orig' WHERE id=1")
        conn.execute("UPDATE tasks SET status='dismissed', resolved_text='made by a test' WHERE id=7")

    assert db.revert_repairs(RUN) == 3
    with db.get_conn() as conn:
        assert conn.execute("SELECT value FROM memory WHERE id=1").fetchone()[0] == "junk"
        assert tuple(conn.execute("SELECT status, resolved_text FROM tasks WHERE id=7").fetchone()) == ("open", None)

