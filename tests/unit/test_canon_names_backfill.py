"""Критерий бэкфилла канонических имён: кого переименовывает, кого не сливает.

Акт мутирует ИМЯ строки канона — то самое поле, по которому её находят все
читатели. Ошибка здесь не теряет строку, а уводит её в чужой тренд, что хуже:
потерю видно, подмену нет. Поэтому проверяется критерий и, главное, отказ
сливать два измерения молча.
"""
from __future__ import annotations

import pytest

from migrations import canon_names_backfill_20260914 as act

pytestmark = pytest.mark.unit


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    from tests.conftest import canon_schema
    canon_schema(health_db)
    return health_db


def _add(conn, name, date="2021-06-14", source="doc:x.pdf"):
    conn.execute(
        "INSERT INTO lab_results (date, source, specimen, test_name, value, unit) "
        "VALUES (?, ?, 'stool', ?, 1.0, 'мкг/г')", (date, source, name))


def test_renames_a_name_that_now_resolves(db):
    """Имя, которое словарь научился сводить, получает канонический вид."""
    with db.get_conn() as c:
        _add(c, "Кальпротектин")
        c.commit()
        renames, conflicts = act.plan_rename(c)
    assert [(r["test_name"], r["canon"]) for r in renames] == [("Кальпротектин", "Calprotectin")]
    assert conflicts == []


def test_canonical_and_unknown_names_are_left_alone(db):
    """Граница с обеих сторон: уже каноническое имя и несводимое — не трогаются."""
    with db.get_conn() as c:
        _add(c, "Calprotectin")
        # Несводимое имя. Прежде здесь стоял другой аналит — 14.09 он
        # получил статью в словаре и сводиться начал, то есть пример перестал быть
        # примером и тест покраснел правильно. Нынешний выбран так, чтобы не
        # устареть тем же способом: это не аналит вовсе, а строка-шапка бланка.
        _add(c, "Комментарий лаборатории")
        c.commit()
        renames, conflicts = act.plan_rename(c)
    assert renames == [] and conflicts == []


def test_collision_is_a_conflict_not_a_silent_merge(db):
    """⭐ Если каноническое имя в тот же день/источник уже занято — переименование
    ОТМЕНЯЕТСЯ и печатается конфликтом. Слить два измерения молча нельзя: это уже
    не переименование, а решение о том, какое из них настоящее."""
    with db.get_conn() as c:
        _add(c, "Calprotectin")
        _add(c, "Кальпротектин")
        c.commit()
        renames, conflicts = act.plan_rename(c)
    assert renames == []
    assert [r["test_name"] for r in conflicts] == ["Кальпротектин"]


def test_same_name_other_day_is_not_a_conflict(db):
    """Позитивный контроль к предыдущему: занятость судится в пределах дня и
    источника, иначе один давний замер заморозил бы переименование навсегда."""
    with db.get_conn() as c:
        _add(c, "Calprotectin", date="2020-01-01")
        _add(c, "Кальпротектин")
        c.commit()
        renames, conflicts = act.plan_rename(c)
    assert [r["canon"] for r in renames] == ["Calprotectin"] and conflicts == []
