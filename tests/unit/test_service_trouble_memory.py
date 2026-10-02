"""Память гипотезы: без исхода механизм неизмерим.

Проверяем не «функция вызвалась», а что запись появляется и что исход её
закрывает — иначе через месяц нельзя посчитать долю ложных тревог, и порог
не откалибруется никогда.
"""
import sqlite3

import pytest

import service_trouble as st


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


def _verdict(action="escalate", conf=0.8):
    return st.Verdict(trouble=True, confidence=conf, why="тест", action=action,
                      signals={"_echoed_system_text": True, "_burst": False})


def test_hypothesis_is_written_and_starts_without_outcome(conn):
    hid = st.remember(_verdict(), "health_partner", conn=conn)
    assert hid
    row = conn.execute("SELECT tenant, action, confidence, outcome FROM service_trouble "
                       "WHERE id=?", (hid,)).fetchone()
    assert row[:3] == ("health_partner", "escalate", 0.8)
    assert row[3] is None, "свежая гипотеза обязана быть открытой"


def test_resolve_closes_it(conn):
    hid = st.remember(_verdict(), "health_partner", conn=conn)
    assert st.resolve_outcome(hid, "false_alarm", conn=conn) is True
    outcome, at = conn.execute("SELECT outcome, outcome_at FROM service_trouble WHERE id=?",
                               (hid,)).fetchone()
    assert outcome == "false_alarm"
    assert at, "время исхода обязано проставиться"


def test_unknown_outcome_is_rejected(conn):
    """НЕГАТИВ: свободный текст в исходе сделал бы статистику невычислимой."""
    hid = st.remember(_verdict(), "health_partner", conn=conn)
    with pytest.raises(ValueError):
        st.resolve_outcome(hid, "наверное да", conn=conn)


def test_resolve_nonexistent_hypothesis_reports_failure(conn):
    hid = st.remember(_verdict(), "fixture_tenant", conn=conn)
    assert st.resolve_outcome(hid + 1, "confirmed", conn=conn) is False
    assert conn.execute("SELECT outcome, outcome_at FROM service_trouble WHERE id=?",
                        (hid,)).fetchone() == (None, None)


def test_user_text_is_not_stored():
    """Переписка тенанта в журнал гипотез не попадает — там только признаки."""
    import inspect
    src = inspect.getsource(st.remember)
    assert "user_text" not in src and "user_msg" not in src
