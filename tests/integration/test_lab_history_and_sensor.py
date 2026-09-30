"""История анализов для консилиума + датчик канона (labs_db, integrity_tests).

Закрывает дыры the-end 2026-07-01:
- build_lab_history_context — то, ради чего делалась перепроводка (консилиум
  видит даты и различает ТЕКУЩЕЕ/УСТАРЕЛО), было без теста.
- check_lab_canon_health — датчик регресса распознавания (невозможное значение RDW), unit-aware,
  был без теста: датчик без теста может тихо не ловить.
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
    with health_db.get_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS lab_results(
            id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, source TEXT,
            test_name TEXT, value REAL, unit TEXT, ref_low REAL, ref_high REAL, status TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS lab_monitoring_schedule(
            test_name TEXT PRIMARY KEY, interval_days INTEGER, priority TEXT,
            source TEXT, note TEXT)""")
        c.commit()
    return health_db


# ── build_lab_history_context ──

def _seed_series(db, name, dates_values, unit="g/dL"):
    with db.get_conn() as c:
        c.executemany(
            "INSERT INTO lab_results(date,test_name,value,unit) VALUES(?,?,?,?)",
            [(d, name, v, unit) for d, v in dates_values])
        c.commit()


def test_history_recent_tagged_current(db):
    today = date.today()
    recent = (today - timedelta(days=10)).isoformat()
    d2 = (today - timedelta(days=200)).isoformat()
    d3 = (today - timedelta(days=400)).isoformat()
    _seed_series(db, "HGB", [(d3, 13.0), (d2, 13.5), (recent, 14.0)])
    ctx = db.build_lab_history_context(min_points=3)
    assert "HGB" in ctx
    assert "ТЕКУЩЕЕ" in ctx          # последнее в пределах дефолт-срока 180д
    assert recent in ctx


def test_history_old_tagged_stale(db):
    today = date.today()
    old = (today - timedelta(days=900)).isoformat()
    o2 = (today - timedelta(days=1000)).isoformat()
    o3 = (today - timedelta(days=1100)).isoformat()
    _seed_series(db, "HGB", [(o3, 13.0), (o2, 13.5), (old, 14.0)])
    ctx = db.build_lab_history_context(min_points=3)
    assert "УСТАРЕЛО" in ctx         # >2×180д без свежих данных


def test_history_below_min_points_and_stale_excluded(db):
    # min_points исключает аналит ТОЛЬКО когда точек мало И нет свежего значения
    # (обе точки вне срока). Контракт после fix 31889cb (2026-07-09).
    today = date.today()
    _seed_series(db, "Ferritin", [
        ((today - timedelta(days=400)).isoformat(), 100.0),
        ((today - timedelta(days=500)).isoformat(), 90.0),
    ])  # 2 точки < min_points=3 И обе > 2×180д срока → не current
    ctx = db.build_lab_history_context(min_points=3)
    assert "Ferritin" not in ctx


def test_history_sparse_but_current_shown_without_trend(db):
    # Инвариант fix 31889cb / инцидент 09.07: свежее значение при <min_points НЕ
    # выпадает — иначе консилиум конфабулирует «не измерено» (D/гомоцистеин/B12 → #793/#794).
    today = date.today()
    _seed_series(db, "Ferritin", [
        ((today - timedelta(days=30)).isoformat(), 100.0),
        ((today - timedelta(days=60)).isoformat(), 90.0),
    ])  # 2 точки < min_points, но последняя в пределах срока → ТЕКУЩЕЕ без тренда
    ctx = db.build_lab_history_context(min_points=3)
    assert "Ferritin" in ctx
    assert "ТЕКУЩЕЕ" in ctx


def test_history_dedup_same_date(db):
    today = date.today()
    d = (today - timedelta(days=10)).isoformat()
    with db.get_conn() as c:
        # одна дата дважды (ре-загрузка) → одна точка
        c.executemany("INSERT INTO lab_results(date,test_name,value,unit) VALUES(?,?,?,?)", [
            (d, "PLT", 250.0, ""), (d, "PLT", 250.0, ""),
            ((today - timedelta(days=100)).isoformat(), "PLT", 240.0, ""),
            ((today - timedelta(days=200)).isoformat(), "PLT", 230.0, ""),
        ])
        c.commit()
    ctx = db.build_lab_history_context(min_points=3)
    # 4 строки, но 3 уникальных даты → проходит min_points и не дублирует точку дня
    assert "PLT" in ctx
    assert ctx.count(d) == 1          # продублированная дата встречается ровно раз
    assert ctx.count("250.0") == 1


# ── check_lab_canon_health (датчик) ──

def _run_sensor(db, monkeypatch):
    import integrity_tests
    calls = []
    monkeypatch.setattr(integrity_tests, "warn",
                        lambda label, detail="": calls.append((label, detail)))
    integrity_tests.check_lab_canon_health()
    return calls


def test_sensor_flags_impossible_value(db, monkeypatch):
    with db.get_conn() as c:
        c.execute("INSERT INTO lab_results(date,test_name,value,unit) VALUES(?,?,?,?)",
                  ("2022-02-15", "RDW", 162.0, "%"))  # регресс distorted-распознавания
        c.commit()
    calls = _run_sensor(db, monkeypatch)
    assert any("RDW" in d for _, d in calls)


def test_sensor_unit_aware_no_false_positive(db, monkeypatch):
    # HGB 127 g/L = 12.7 g/dL → в физиологии; датчик НЕ должен ругаться
    with db.get_conn() as c:
        c.execute("INSERT INTO lab_results(date,test_name,value,unit) VALUES(?,?,?,?)",
                  ("2022-02-15", "HGB", 127.0, "g/L"))
        c.commit()
    calls = _run_sensor(db, monkeypatch)
    assert not any("HGB" in d for _, d in calls)


def test_sensor_flags_conflict(db, monkeypatch):
    with db.get_conn() as c:
        c.executemany("INSERT INTO lab_results(date,test_name,value,unit) VALUES(?,?,?,?)", [
            ("2022-02-15", "Glucose", 95.0, "mg/dL"),
            ("2022-02-15", "Glucose", 180.0, "mg/dL"),  # тот же (date,аналит), другое значение
        ])
        c.commit()
    calls = _run_sensor(db, monkeypatch)
    assert any("конфликт" in d.lower() or "Glucose" in d for _, d in calls)


def test_sensor_clean_canon_silent(db, monkeypatch):
    with db.get_conn() as c:
        c.execute("INSERT INTO lab_results(date,test_name,value,unit) VALUES(?,?,?,?)",
                  ("2022-02-15", "HGB", 13.5, "g/dL"))
        c.commit()
    calls = _run_sensor(db, monkeypatch)
    assert calls == []


def test_sensor_blood_bounds_not_applied_to_urine(db, monkeypatch):
    """Границы физиологии кровяные. Калий ~40 ммоль/л в разовой моче — норма, а
    по кровяной линейке это несовместимо с жизнью (значение — синтетика той же
    формы). Повод 31.07: строки такого вида приехали с перепромоутом («Микроэлементы (ИСП-МС)», материал прочитан
    из шапки бланка как «Моча (разовая)»). Пробел ДАТЧИКА, не дефект данных."""
    with db.get_conn() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(lab_results)")}
        if "specimen" not in cols:
            c.execute("ALTER TABLE lab_results ADD COLUMN specimen TEXT")
        c.execute("INSERT INTO lab_results(date,test_name,value,unit,specimen)"
                  " VALUES(?,?,?,?,?)", ("2021-06-14", "Potassium", 41.2, "mmol/L", "urine"))
        c.commit()
    assert _run_sensor(db, monkeypatch) == []


def test_sensor_blood_bounds_still_apply_to_blood(db, monkeypatch):
    """НЕГАТИВНЫЙ КОНТРОЛЬ к предыдущему: послабление касается ТОЛЬКО не-крови.
    Без него датчик замолчал бы и о настоящем регрессе распознавания."""
    with db.get_conn() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(lab_results)")}
        if "specimen" not in cols:
            c.execute("ALTER TABLE lab_results ADD COLUMN specimen TEXT")
        c.execute("INSERT INTO lab_results(date,test_name,value,unit,specimen)"
                  " VALUES(?,?,?,?,?)", ("2021-06-14", "Potassium", 41.2, "mmol/L", "blood"))
        c.commit()
    assert any("Potassium" in d for _, d in _run_sensor(db, monkeypatch))
