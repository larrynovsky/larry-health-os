"""Провенанс строки канона (C-173): класс источника обязателен на записи.

Повод (07.10): две разовые пачки сессии записали канон мимо путей, оставляющих источник,
— дубли медкарты с ярлыками не из документов и строки анализов с неверным значением.
Ни один датчик этого не видел. Судится МЕХАНИЗМ боевой схемы: init_db на временной
базе (тот же DDL, что ставит миграция), а не пересказ триггера в фикстуре (§20).
Все данные ниже выдуманы.
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
    from tests.conftest import canon_schema
    canon_schema(health_db)          # таблицу анализов init_db не заводит — боевыми функциями
    return health_db


def _lab(conn, source):
    conn.execute("INSERT INTO lab_results (date, source, test_name, value, unit) "
                 "VALUES ('2021-01-01', ?, 'Example_Analyte', 1.0, 'mg/dL')", (source,))


def _problem(conn, source, pid="EX1"):
    conn.execute("INSERT INTO problem_list (problem_id, title, status, first_seen, last_updated, source) "
                 "VALUES (?, 'Example condition', 'active', '2021-01-01', '2021-01-01', ?)", (pid, source))


# «example_CBC_chemistry.json» и «бланк анализов.pdf» — формы source двух ретайрнутых
# писателей (biochemical-json и coordinate_import); их рецидив раньше ловил датчик
# check_lab_single_writer, теперь — отказ на записи. Откат из бэкапа без триггеров — ниже,
# test_датчик_видит_снятый_триггер_и_обход.
@pytest.mark.parametrize("source", [None, "", "Some Lab", "report.pdf",
                                    "example_CBC_chemistry.json", "бланк анализов.pdf"])
def test_строка_анализа_без_класса_отбивается(hdb, source):
    with hdb.get_conn() as c, pytest.raises(sqlite3.IntegrityError):
        _lab(c, source)


@pytest.mark.parametrize("source", ["doc:a.pdf", "instrument:isi", "manual:правка по бланку"])
def test_строка_анализа_с_классом_проходит(hdb, source):
    with hdb.get_conn() as c:
        _lab(c, source)


@pytest.mark.parametrize("source", [None, "gp_weekly", "seed"])
def test_запись_медкарты_без_класса_отбивается(hdb, source):
    with hdb.get_conn() as c, pytest.raises(sqlite3.IntegrityError):
        _problem(c, source)


def test_смена_источника_на_бесклассовый_отбивается(hdb):
    with hdb.get_conn() as c:
        _lab(c, "doc:a.pdf")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE lab_results SET source='a.pdf'")


def test_наследство_помечается_а_не_теряется(hdb):
    """Строки, записанные до защиты без класса, получают «manual:legacy:<было>» — исходный
    текст сохраняется. Миграция идемпотентна и возвращает триггеры."""
    with hdb.get_conn() as c:
        for n in hdb.canon_provenance_trigger_names():
            c.execute(f"DROP TRIGGER {n}")
        _lab(c, "Some Lab")
        _problem(c, None)
    hdb._migrate_canon_provenance()
    hdb._migrate_canon_provenance()
    with hdb.get_conn() as c:
        assert c.execute("SELECT source FROM lab_results").fetchone()[0] == "manual:legacy:Some Lab"
        assert c.execute("SELECT source FROM problem_list").fetchone()[0].startswith("manual:legacy:")
        import integrity_tests as it
        assert it.canon_provenance_offenders(c) == []


def test_датчик_видит_снятый_триггер_и_обход(hdb):
    import integrity_tests as it
    with hdb.get_conn() as c:
        assert it.canon_provenance_offenders(c) == []
        c.execute("DROP TRIGGER trg_lab_results_source_class_ins")
        _lab(c, "Some Lab")
        found = it.canon_provenance_offenders(c)
    assert any("trg_lab_results_source_class_ins" in f for f in found)
    assert any("lab_results: 1 строк" in f for f in found)


def test_одобренное_предложение_пишет_свой_номер(hdb):
    """Путь от предложения до строки медкарты (C-48): источник — номер предложения."""
    import problems_db as pdb
    import proposals_db as prdb
    pid = pdb.save_problem_proposal("gp_weekly", [{
        "action": "add", "title": "Example condition", "description": "example",
        "plain_summary": "пример"}])
    prdb.apply_proposal(pid)
    with hdb.get_conn() as c:
        assert c.execute("SELECT source FROM problem_list").fetchone()[0] == f"proposal:{pid}"


def test_upsert_без_источника_не_пишет(hdb):
    import problems_db as pdb
    with pytest.raises(sqlite3.IntegrityError):
        pdb.upsert_problem("EX2", "Example condition")
    pdb.upsert_problem("EX3", "Example condition", source="manual:seed")
