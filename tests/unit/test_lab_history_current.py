"""build_lab_history_context — свежее значение видно даже при 1–2 точках.

Синтетический редкий ряд: min_points ограничивает только тренд.
Свежее текущее значение должно быть видно даже без достаточного числа точек.
PRO-опросники (unit=score*) идут отдельным каналом build_specialized_context.
"""
from datetime import date, timedelta
import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    health_db._ensure_lab_table()
    return health_db


def _ins(db, rows):
    with db.get_conn() as c:
        c.executemany("INSERT INTO lab_results(date,test_name,value,unit) VALUES(?,?,?,?)", rows)
        c.commit()


def _d(n):
    return (date.today() - timedelta(days=n)).isoformat()


def test_recent_two_point_analyte_included(db):
    """Придуманный ряд из двух точек: свежая должна попасть в контекст."""
    import labs_db
    _ins(db, [("2018-02-12", "Vitamin_D", 21.8, "ng/ml"), (_d(25), "Vitamin_D", 38.4, "ng/ml")])
    ctx = labs_db.build_lab_history_context()
    assert "38.4" in ctx and "ТЕКУЩЕЕ" in ctx


def test_single_recent_point_included(db):
    """Один свежий замер (NT-proBNP) тоже виден."""
    import labs_db
    _ins(db, [(_d(30), "NT_proBNP", 41.0, "pg/ml")])
    assert "41.0" in labs_db.build_lab_history_context()


def test_stale_low_point_excluded(db):
    """Старый одиночный замер (вне срока) не показывается."""
    import labs_db
    _ins(db, [("2024-01-01", "BUN", 15.0, "mg/dL")])
    assert labs_db.build_lab_history_context().strip() == ""


def test_pro_score_excluded(db):
    """PRO-опросник (unit=score*) исключён из лаб-секции."""
    import labs_db
    _ins(db, [(_d(20), "pro12_anxiety", 11.11, "score_0_100")])
    assert "11.11" not in labs_db.build_lab_history_context()


def test_self_report_excluded_by_source_even_without_score_unit(db):
    """Опросник исключается по ИСТОЧНИКУ (instrument:*), не только по единице: решение
    владельца 27.09 — самоотчёт в конституцию не идёт. Ряд длиной от года, единица не score."""
    import labs_db
    with db.get_conn() as c:
        c.executemany("INSERT INTO lab_results(date,test_name,value,unit,source) VALUES(?,?,?,?,?)",
                      [(_d(700), "isi_total", 41.0, "points", "instrument:isi"),
                       (_d(400), "isi_total", 42.0, "points", "instrument:isi"),
                       (_d(30), "isi_total", 43.0, "points", "instrument:isi")])
        c.commit()
    assert "isi_total" not in labs_db.build_lab_history_context(horizon_days=365)
    assert "isi_total" not in labs_db.build_lab_history_context()


def test_trend_still_gated_by_min_points(db):
    """≥3 точки → показывается тренд."""
    import labs_db
    _ins(db, [(_d(300), "Glucose", 90.0, "mg/dL"), (_d(200), "Glucose", 95.0, "mg/dL"),
              (_d(40), "Glucose", 114.0, "mg/dL")])
    ctx = labs_db.build_lab_history_context()
    assert "тренд:" in ctx and "114" in ctx
