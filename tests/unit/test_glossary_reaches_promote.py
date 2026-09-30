"""F-14: подтверждение человека доходит до промоута.

До 31.07 промоут спрашивал глоссарий с литералом `format_id=0`. Формата с таким
id не существует, поэтому 66 подтверждённых человеком имён не влияли ни на одну
строку канона — и это было ВИДНО только по xfail-тестам, не по данным.
"""
import sqlite3

import pytest

import health_db  # noqa: F401 — ПЕРВЫМ: labs_db в одиночку падает циркуляром
import labs_db


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE lab_name_aliases (format_id INT, raw_name TEXT, "
              "canonical TEXT, confirmed INT)")
    yield c
    c.close()


def _seed(c, rows):
    c.executemany("INSERT INTO lab_name_aliases VALUES (?,?,?,?)", rows)


def test_алиасы_разных_форматов_попадают_в_один_словарь(conn):
    _seed(conn, [(1, "Erythrocytes", "RBC", 1),
                 (4242, "Kreatinin i.S.", "Creatinine", 1)])
    d = labs_db.get_confirmed_aliases_all(conn=conn)
    assert d == {"erythrocytes": "RBC", "kreatinin i.s.": "Creatinine"}


def test_неподтверждённый_алиас_в_словарь_не_идёт(conn):
    """Негативный контроль: без него словарь пускал бы догадку распознавателя
    наравне с решением человека, а вся ценность глоссария в том, что он —
    решение человека."""
    _seed(conn, [(1, "Erythrocytes", "RBC", 0)])
    assert labs_db.get_confirmed_aliases_all(conn=conn) == {}


def test_конфликт_форматов_выбрасывается_а_не_разрешается(conn):
    """Два разных ответа человека на один вопрос машина выбрать не может.
    Имя обязано исчезнуть из словаря, а не достаться одному из форматов."""
    _seed(conn, [(1, "Total Protein", "Protein_total", 1),
                 (4242, "Total Protein", "TP", 1)])
    assert labs_db.get_confirmed_aliases_all(conn=conn) == {}
    assert labs_db.confirmed_alias_conflicts(conn=conn)


def test_согласные_форматы_конфликтом_не_считаются(conn):
    _seed(conn, [(1, "Erythrocytes", "RBC", 1),
                 (4242, "erythrocytes", "RBC", 1)])
    assert labs_db.confirmed_alias_conflicts(conn=conn) == []
    assert labs_db.get_confirmed_aliases_all(conn=conn) == {"erythrocytes": "RBC"}
