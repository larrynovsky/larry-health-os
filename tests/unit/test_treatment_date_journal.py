"""Дата лечения меняется только с записью в журнале (07.10, нить treatment-date-journal).

Повод: 30.08 дату курса в periods поменяли прямой правкой — без журнала, а source строки
остался прежним, и защита класса источника этого не видит. Судится МЕХАНИЗМ боевой схемы:
init_db на временной базе ставит те же триггеры, что миграция (§20). Данные выдуманы.
"""
from __future__ import annotations

import sqlite3

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def hdb(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    return health_db


def _period(conn, type_="immunotherapy", end="2021-04-30"):
    return conn.execute("INSERT INTO periods (name, type, start_date, end_date, source) "
                        "VALUES ('Example course', ?, '2020-10-01', ?, 'manual')",
                        (type_, end)).lastrowid


def test_прямая_правка_даты_курса_отбивается(hdb):
    with hdb.get_conn() as c:
        pid = _period(c)
    with hdb.get_conn() as c, pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE periods SET end_date='2021-10-31' WHERE id=?", (pid,))


def test_правка_через_журнал_проходит_и_откатывается(hdb):
    with hdb.get_conn() as c:
        pid = _period(c)
    with hdb.get_conn() as c:
        hdb.log_repair(c, run_id="ex_run", home="sqlite:periods", entity=str(pid),
                       field="end_date", old_value="2021-04-30", new_value="2021-10-31",
                       reason="example")
        c.execute("UPDATE periods SET end_date='2021-10-31' WHERE id=?", (pid,))
    assert hdb.revert_repairs("ex_run") == 1          # откат не отбит триггером
    with hdb.get_conn() as c:
        assert c.execute("SELECT end_date FROM periods WHERE id=?", (pid,)).fetchone()[0] == "2021-04-30"


def test_поездка_и_неизменная_дата_не_требуют_журнала(hdb):
    with hdb.get_conn() as c:
        tid = _period(c, type_="travel")
        c.execute("UPDATE periods SET end_date='2021-05-05' WHERE id=?", (tid,))   # calendar_sync
        pid = _period(c)
        c.execute("UPDATE periods SET end_date=end_date, notes='x' WHERE id=?", (pid,))


def test_лекарство_прямо_нельзя_а_писатель_из_документа_пишет_журнал(hdb):
    import treatment_db
    mid = treatment_db.upsert_medication("Examplimab", start_date="2020-01-01",
                                         end_date="2020-03-01", source="extractor:1")
    with hdb.get_conn() as c, pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE medications SET end_date='2020-09-01' WHERE id=?", (mid,))
    assert treatment_db.upsert_medication("Examplimab", end_date="2020-06-01",
                                          source="extractor:2") == mid
    with hdb.get_conn() as c:
        assert c.execute("SELECT end_date FROM medications WHERE id=?", (mid,)).fetchone()[0] == "2020-06-01"
        assert c.execute("SELECT COUNT(*) FROM data_repair_log WHERE home='sqlite:medications' "
                         "AND entity=? AND field='end_date'", (str(mid),)).fetchone()[0] == 1


def test_датчик_видит_снятый_триггер(hdb):
    import integrity_tests as it
    with hdb.get_conn() as c:
        assert it.canon_provenance_offenders(c) == []
        c.execute("DROP TRIGGER trg_periods_end_date_journal")
        assert "нет триггера trg_periods_end_date_journal" in it.canon_provenance_offenders(c)


def _fix(hdb, c, pid, old, new, run):
    hdb.log_repair(c, run_id=run, home="sqlite:periods", entity=str(pid), field="end_date",
                   old_value=old, new_value=new, reason="example")
    c.execute("UPDATE periods SET end_date=? WHERE id=?", (new, pid))


def test_возврат_к_прежнему_значению_из_журнала_без_отката_отбивается(hdb):
    # Живой случай 07.10: прежнее значение правки лежало в журнале, и прямая правка обратно прошла.
    with hdb.get_conn() as c:
        pid = _period(c)
        _fix(hdb, c, pid, "2021-04-30", "2021-10-31", "ex_run")
    with hdb.get_conn() as c, pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE periods SET end_date='2021-04-30' WHERE id=?", (pid,))


def test_значение_из_устаревшей_записи_журнала_отбивается(hdb):
    # Живой случай 07.10: неоткаченная запись 04.10 несла ошибочную дату как new_value.
    with hdb.get_conn() as c:
        pid = _period(c, end="2021-01-31")
        _fix(hdb, c, pid, "2021-01-31", "2021-04-30", "run_a")
        _fix(hdb, c, pid, "2021-04-30", "2021-10-31", "run_b")
    with hdb.get_conn() as c, pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE periods SET end_date='2021-04-30' WHERE id=?", (pid,))


def test_откат_прогона_с_двумя_правками_поля_возвращает_исходное(hdb):
    with hdb.get_conn() as c:
        pid = _period(c, end="2021-01-31")
        _fix(hdb, c, pid, "2021-01-31", "2021-04-30", "run_c")
        _fix(hdb, c, pid, "2021-04-30", "2021-10-31", "run_c")
    assert hdb.revert_repairs("run_c") == 2
    with hdb.get_conn() as c:
        assert c.execute("SELECT end_date FROM periods WHERE id=?", (pid,)).fetchone()[0] == "2021-01-31"
