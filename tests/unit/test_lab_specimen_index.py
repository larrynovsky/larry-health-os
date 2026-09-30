"""Регресс (2026-07-01): idx_lab_uniq включает specimen.

Баг: индекс был (date,test_name,source) без specimen, а промоут дедупит по
(date,canonical,specimen) → blood+urine одного аналита из одного источника
сталкивались на UNIQUE → промоут падал IntegrityError и молча не применялся
(и на дашборде). Тест сторожит, что specimen — часть уникальности."""
import sqlite3

import pytest


def test_index_allows_blood_and_urine_same_analyte_source(db):
    """Две пробы одного аналита из одного источника — обе проходят (specimen различает)."""
    with db.conn() as c:
        c.execute("INSERT INTO lab_results(date,test_name,value,source,specimen) "
                  "VALUES('2026-01-01','Glucose',90,'doc:x.pdf','blood')")
        c.execute("INSERT INTO lab_results(date,test_name,value,source,specimen) "
                  "VALUES('2026-01-01','Glucose',12,'doc:x.pdf','urine')")
        c.commit()
        n = c.execute("SELECT COUNT(*) FROM lab_results "
                      "WHERE date='2026-01-01' AND test_name='Glucose'").fetchone()[0]
    assert n == 2


def test_index_still_rejects_true_duplicate(db):
    """Истинный дубль (та же проба, дата, источник) по-прежнему отклоняется."""
    with db.conn() as c:
        c.execute("INSERT INTO lab_results(date,test_name,value,source,specimen) "
                  "VALUES('2026-02-02','HGB',14,'doc:y.pdf','blood')")
        c.commit()
    with pytest.raises(sqlite3.IntegrityError):
        with db.conn() as c:
            c.execute("INSERT INTO lab_results(date,test_name,value,source,specimen) "
                      "VALUES('2026-02-02','HGB',15,'doc:y.pdf','blood')")
            c.commit()


def test_index_definition_has_specimen(db):
    """Датчик схемы: индекс реально включает specimen."""
    with db.conn() as c:
        sql = c.execute("SELECT sql FROM sqlite_master WHERE name='idx_lab_uniq'").fetchone()[0]
    assert "specimen" in sql
