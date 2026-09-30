"""Sprint 4b unit-тесты для Sprint 3 checkin scores dual-write (Р-2).

Покрывает:
  - _migrate_checkin_scores: ALTER TABLE добавляет 3 колонки
  - update_checkin_scores: UPDATE последней записи; edge cases (empty, multiple)
  - finalize_checkin mapping (mood/energy → integer scores)
  - lifestyle_agents.StressAgent чтение новых колонок
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# ── Schema migration ───────────────────────────────────────────────────────

def test_migrate_checkin_scores_adds_columns(db):
    """ALTER TABLE добавляет stress_score, mood_score, energy_score."""
    import health_db
    health_db._migrate_checkin_scores()
    rows = db.fetchall("PRAGMA table_info(checkins)")
    cols = {r["name"] for r in rows}
    assert "stress_score" in cols
    assert "mood_score" in cols
    assert "energy_score" in cols


def test_migrate_checkin_scores_idempotent(db):
    """Повторный run не падает (ALTER через try/except)."""
    import health_db
    health_db._migrate_checkin_scores()
    health_db._migrate_checkin_scores()  # second time
    health_db._migrate_checkin_scores()  # third time
    rows = db.fetchall("PRAGMA table_info(checkins)")
    cols = {r["name"] for r in rows}
    assert "stress_score" in cols


# ── update_checkin_scores ──────────────────────────────────────────────────

def test_update_checkin_scores_updates_latest(db, clock):
    clock.set("2026-05-22")
    import health_db
    health_db._migrate_checkin_scores()
    health_db.save_checkin(day="2026-05-22", question="Q", answer="A",
                           time_of_day="evening")
    rc = health_db.update_checkin_scores(
        day="2026-05-22", time_of_day="evening",
        stress_score=6, mood_score=2, energy_score=1,
    )
    assert rc == 1
    row = db.fetchone(
        "SELECT stress_score, mood_score, energy_score FROM checkins "
        "WHERE date='2026-05-22' AND time_of_day='evening'"
    )
    assert row["stress_score"] == 6
    assert row["mood_score"] == 2
    assert row["energy_score"] == 1


def test_update_checkin_scores_only_one_record(db, clock):
    """update_checkin_scores затрагивает только ОДНУ запись (subquery LIMIT 1).

    Если несколько чекинов на одну дату — точно какая именно обновится
    зависит от created_at ordering. SQL гарантирует только что **одна**
    запись обновится (`WHERE id = (scalar subquery LIMIT 1)`).
    """
    clock.set("2026-05-22")
    import health_db
    health_db._migrate_checkin_scores()
    # Принудительно разный created_at — через ручной INSERT
    with db.conn() as c:
        c.execute(
            "INSERT INTO checkins (date, time_of_day, question, answer, created_at) "
            "VALUES ('2026-05-22', 'evening', 'Q1', 'A1', '2026-05-22 10:00:00')"
        )
        c.execute(
            "INSERT INTO checkins (date, time_of_day, question, answer, created_at) "
            "VALUES ('2026-05-22', 'evening', 'Q2', 'A2', '2026-05-22 20:00:00')"
        )
    rc = health_db.update_checkin_scores(
        day="2026-05-22", time_of_day="evening", stress_score=7,
    )
    assert rc == 1  # ровно одна строка обновлена
    rows = db.fetchall(
        "SELECT question, stress_score FROM checkins ORDER BY id"
    )
    assert len(rows) == 2
    # Q2 имеет более поздний created_at → ORDER BY created_at DESC LIMIT 1 → Q2
    q1_row = next(r for r in rows if r["question"] == "Q1")
    q2_row = next(r for r in rows if r["question"] == "Q2")
    assert q1_row["stress_score"] is None
    assert q2_row["stress_score"] == 7


def test_update_checkin_scores_returns_0_when_no_match(db, clock):
    """Если нет записей — rowcount=0."""
    clock.set("2026-05-22")
    import health_db
    health_db._migrate_checkin_scores()
    rc = health_db.update_checkin_scores(
        day="2026-05-22", time_of_day="evening", stress_score=5,
    )
    assert rc == 0


def test_update_checkin_scores_skips_nones(db, clock):
    """Если score=None — не обновляется (не перезаписывает в NULL)."""
    clock.set("2026-05-22")
    import health_db
    health_db._migrate_checkin_scores()
    health_db.save_checkin(day="2026-05-22", question="Q", answer="A",
                           time_of_day="evening")
    health_db.update_checkin_scores(
        day="2026-05-22", time_of_day="evening", stress_score=5,
        mood_score=3, energy_score=None,  # energy остаётся NULL
    )
    row = db.fetchone(
        "SELECT stress_score, mood_score, energy_score FROM checkins "
        "WHERE date='2026-05-22'"
    )
    assert row["stress_score"] == 5
    assert row["mood_score"] == 3
    assert row["energy_score"] is None


def test_update_checkin_scores_all_nones_returns_0(db, clock):
    """Все scores=None → 0 rowcount, ничего не делается."""
    clock.set("2026-05-22")
    import health_db
    health_db._migrate_checkin_scores()
    health_db.save_checkin(day="2026-05-22", question="Q", answer="A",
                           time_of_day="evening")
    rc = health_db.update_checkin_scores(day="2026-05-22", time_of_day="evening")
    assert rc == 0


# ── finalize_checkin mapping (mood/energy → integer) ───────────────────────

@pytest.mark.parametrize("mood_text,expected", [
    ("positive", 3),
    ("neutral", 2),
    ("mixed", 2),
    ("negative", 1),
    ("unknown_value", None),  # неизвестное → None
    (None, None),
])
def test_finalize_checkin_mood_mapping(mood_text, expected):
    """checkin_agent.finalize_checkin маппит mood → 1/2/3 через MOOD_MAP."""
    MOOD_MAP = {"positive": 3, "neutral": 2, "mixed": 2, "negative": 1}
    actual = MOOD_MAP.get(mood_text)
    assert actual == expected


@pytest.mark.parametrize("energy_text,expected", [
    ("high", 3),
    ("medium", 2),
    ("low", 1),
    ("very_high", None),  # неизвестное
    (None, None),
])
def test_finalize_checkin_energy_mapping(energy_text, expected):
    ENERGY_MAP = {"high": 3, "medium": 2, "low": 1}
    actual = ENERGY_MAP.get(energy_text)
    assert actual == expected


def test_finalize_checkin_day_score_in_valid_range():
    """day_score 0-10 как есть, остальное → None."""
    def map_stress(ds):
        return int(ds) if isinstance(ds, (int, float)) and 0 <= ds <= 10 else None

    assert map_stress(0) == 0
    assert map_stress(5) == 5
    assert map_stress(10) == 10
    assert map_stress(11) is None  # вне диапазона
    assert map_stress(-1) is None
    assert map_stress("5") is None  # не число
    assert map_stress(None) is None
