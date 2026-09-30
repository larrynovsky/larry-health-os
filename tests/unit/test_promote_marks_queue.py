"""Очередь перестаёт врать: промоут сам помечает то, что вставил.

После фактического приёма строки в канон флаг очереди должен сниматься,
иначе датчик показывает уже выполненную работу.
"""
import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "h"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "h" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "h" / "data" / "health.db")
    health_db.init_db()
    # Схема канона — ПРОДАКШН-функциями (tests/conftest.py::canon_schema), а не копией
    # DDL здесь: копия разъезжается с продом молча (замер 2026-08-08 — value_text).
    from tests.conftest import canon_schema
    canon_schema(health_db)
    with health_db.get_conn() as c:
        cols = ("run_id,extractor_version,source_file,date,panel,canonical_name,"
                "raw_name,value,unit,value_agreement,review_status")
        c.executemany(
            f"INSERT INTO lab_results_staging({cols}) VALUES(?,?,?,?,?,?,?,?,?,?,?)", [
            # доедет: известный аналит, есть значение
            ("r1", "v", "b.pdf", "2025-01-01", "chemistry", "Glucose", "Glucose",
             93.6, "mg/dL", "agree", "pending"),
            # НЕ доедет: имя не сводится к известному аналиту (Layer-2 гейт)
            ("r1", "v", "b.pdf", "2025-01-01", "chemistry", None, "Procedure",
             1.0, "", "agree", "pending"),
        ])
        c.commit()
    return health_db


def _statuses(db):
    with db.get_conn() as c:
        return dict(c.execute(
            "SELECT raw_name, review_status FROM lab_results_staging").fetchall())


def test_dry_run_does_not_touch_the_queue(db):
    import lab_promote
    lab_promote.plan("r1", None, execute=False)
    assert _statuses(db) == {"Glucose": "pending", "Procedure": "pending"}


def test_promoted_row_is_marked_blocked_row_is_not(db):
    """ПОЗИТИВНЫЙ и НЕГАТИВНЫЙ контроль в одном: помечается ровно вставленное.
    Пометить всё подряд было бы ложью в опасную сторону — «всё доехало»."""
    import lab_promote
    lab_promote.plan("r1", None, execute=True)
    st = _statuses(db)
    assert st["Glucose"] == "promoted"
    assert st["Procedure"] == "pending"      # заблокирован, значит всё ещё ждёт
    with db.get_conn() as c:
        names = [r[0] for r in c.execute("SELECT test_name FROM lab_results")]
    assert names == ["Glucose"]


def test_marking_is_idempotent(db):
    import lab_promote
    lab_promote.plan("r1", None, execute=True)
    lab_promote.plan("r1", None, execute=True)
    assert _statuses(db)["Glucose"] == "promoted"


if __name__ == "__main__":
    print("нужен pytest: фикстура подменяет DB_PATH")
