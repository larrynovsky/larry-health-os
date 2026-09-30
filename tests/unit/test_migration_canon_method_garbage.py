"""Оракул чистки мусорных методов канона.

Главный контроль здесь НЕГАТИВНЫЙ: настоящий метод «по Вестергрену» тоже
начинается со строчной буквы, и без проверки происхождения миграция стёрла бы
его вместе с мусором. Именно этот случай, а не позитивный, оправдывает
существование предиката по данным вместо списка литералов.
"""
import sqlite3

import pytest

from migrations import canon_method_garbage_20260731 as mig


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE lab_results (date TEXT, test_name TEXT, source TEXT, "
              "specimen TEXT, method TEXT)")
    c.execute("CREATE TABLE lab_results_staging (method TEXT, method_source TEXT)")
    yield c
    c.close()


def _seed(c, canon, staging):
    c.executemany("INSERT INTO lab_results VALUES (?,?,?,?,?)", canon)
    c.executemany("INSERT INTO lab_results_staging VALUES (?,?)", staging)


def test_слово_результата_из_секции_это_мусор(conn):
    _seed(conn, [("2026-01-01", "Giardia", "doc:x", "stool", "не обнаружено")],
          [("не обнаружено", "read_section")])
    assert mig.plan_updates(conn)[0] == ["не обнаружено"]


def test_настоящий_метод_из_имени_строки_НЕ_мусор(conn):
    """Без этой проверки миграция стёрла бы «по Вестергрену»: строчная буква
    у него такая же, как у «не обнаружено», и отличает их только происхождение."""
    _seed(conn, [("2026-01-01", "ESR", "doc:x", "blood", "по Вестергрену")],
          [("по Вестергрену", "read_name")])
    assert mig.plan_updates(conn)[0] == []


def test_значение_без_происхождения_в_staging_не_трогаем(conn):
    _seed(conn, [("2026-01-01", "X", "doc:x", "blood", "непонятное")], [])
    garbage, diag = mig.plan_updates(conn)
    assert garbage == []
    assert diag["нет происхождения в staging — НЕ трогаем"] == ["непонятное"]


def test_название_раздела_с_прописной_не_мусор(conn):
    _seed(conn, [("2026-01-01", "CEA", "doc:x", "blood", "Онкомаркеры")],
          [("Онкомаркеры", "read_section")])
    assert mig.plan_updates(conn)[0] == []


def test_радиус_считается_до_мутации_слипание_видно(conn):
    """Обнуление метода схлопывает ключ канона. Проверка обязана увидеть это
    ЗАРАНЕЕ, а не получить UNIQUE-ошибку посреди транзакции."""
    _seed(conn, [("2026-01-01", "X", "doc:x", "blood", "не обнаружено"),
                 ("2026-01-01", "X", "doc:x", "blood", None)],
          [("не обнаружено", "read_section")])
    assert mig.collisions_after_null(conn, ["не обнаружено"])


def test_радиуса_нет_когда_строки_различаются_не_методом(conn):
    _seed(conn, [("2026-01-01", "X", "doc:x", "blood", "не обнаружено"),
                 ("2026-01-02", "X", "doc:x", "blood", None)],
          [("не обнаружено", "read_section")])
    assert mig.collisions_after_null(conn, ["не обнаружено"]) == []
