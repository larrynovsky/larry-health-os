"""Инвариант единственного писателя канона lab_results (BL-LAB-CANON-1).

Primary-Based Protocol (Таненбаум §7.5): канон принадлежит vision-пайплайну
(lab_backfill→lab_promote, source=doc:). Старый biochemical-JSON writer заглушён,
иначе он воскрешает мусор (потеря точки + дубли _2/_594) при каждом прогоне —
ровно инцидент 2026-07-04 05:30, когда 315 строк мусора легли поверх чистого канона.

Три уровня:
- фенс: import_biochemical_json не пишет lab_results (escape-hatch работает);
- датчик: check_lab_single_writer краснеет на source *_chemistry.json;
- СЦЕНАРНЫЙ: чистый канон → старый путь → канон не тронут (ловит 05:30).
"""
import json

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    monkeypatch.delenv("HEALTH_ALLOW_BIOCHEMICAL_LABWRITE", raising=False)
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    with health_db.get_conn() as c:
        # created_at добавлен 2026-07-29: без него фикстура расходилась с боевой
        # схемой по КОЛОНКЕ, и новый датчик рецидива ретайрнутого писателя падал на
        # ней «no such column». Это ровно известная мина проекта — сторож дрейфа
        # фикстуры сравнивает список ТАБЛИЦ и колонок не видит.
        c.execute("""CREATE TABLE IF NOT EXISTS lab_results(
            id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, source TEXT,
            test_name TEXT, value REAL, value_text TEXT, unit TEXT,
            ref_low REAL, ref_high REAL, status TEXT,
            created_at TEXT DEFAULT (datetime('now')))""")
        c.commit()
    return health_db


def _canon_count(db):
    with db.get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM lab_results").fetchone()[0]


def _bio_file(tmp_path, date, results):
    d = tmp_path / "health" / "data" / "biochemical" / date[:7]
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{date}_CBC_chemistry.json"
    f.write_text(json.dumps({"date": date, "results": results}), encoding="utf-8")
    return f


# ── фенс на единственной точке вставки ──

def test_fence_hard_blocks_biochemical_write(db, tmp_path):
    # ЖЁСТКИЙ БЛОК (design-принцип #1: без escape-hatch — хатч был бы вектором
    # воскрешения). Ре-импорт лаб-данных только через lab_backfill→lab_promote.
    f = _bio_file(tmp_path, "2020-10-14", {"RDW": {"value": 138.0}})  # decimal-loss мусор
    with pytest.raises(RuntimeError, match="РЕТАЙРНУТ|lab_promote"):
        db.import_biochemical_json(str(f))
    assert _canon_count(db) == 0        # канон не тронут


def test_no_env_hatch_reopens_writer(db, tmp_path, monkeypatch):
    # никакой env-переменной не должно снова открывать старый путь (в т.ч. если
    # глобальный env её выставит, как HEALTH_MULTITENANT)
    monkeypatch.setenv("HEALTH_ALLOW_BIOCHEMICAL_LABWRITE", "1")
    f = _bio_file(tmp_path, "2020-10-14", {"RDW": {"value": 13.8}})
    with pytest.raises(RuntimeError):
        db.import_biochemical_json(str(f))
    assert _canon_count(db) == 0


# ── СЦЕНАРНЫЙ: воспроизводит инцидент 05:30 ──

def test_old_path_cannot_resurrect_garbage(db, tmp_path):
    # чистый канон = только vision-пайплайн (source=doc:)
    with db.get_conn() as c:
        c.execute("INSERT INTO lab_results(date,source,test_name,value) "
                  "VALUES('2020-10-14','doc:lab 14.10.20.pdf','RDW',13.8)")
        c.commit()
    # старый путь сканирует biochemical/ (полный дублей-мусора) и пытается импортнуть
    _bio_file(tmp_path, "2020-10-14", {"RDW": {"value": 138.0}, "RBC": {"value": 0.0}})
    _bio_file(tmp_path, "2020-08-12", {"MCH": {"value": 296.0}})
    total = db.import_all_biochemical()
    assert total == 0                   # фенс: ничего не импортировано
    # канон нетронут: одна чистая doc:-строка, мусор не воскрес
    with db.get_conn() as c:
        rows = c.execute("SELECT source,value FROM lab_results").fetchall()
    assert len(rows) == 1
    assert rows[0]["source"].startswith("doc:")
    assert rows[0]["value"] == 13.8


# ── датчик single-writer ──

def _run_sensor(db, monkeypatch):
    import integrity_tests
    calls = []
    monkeypatch.setattr(integrity_tests, "warn",
                        lambda label, detail="": calls.append((label, detail)))
    n = integrity_tests.check_lab_single_writer()
    return n, calls


def test_sensor_flags_biochemical_source(db, monkeypatch):
    with db.get_conn() as c:
        c.executemany("INSERT INTO lab_results(date,source,test_name,value) VALUES(?,?,?,?)", [
            ("2020-10-14", "2020-10-14_CBC_chemistry.json", "RDW", 138.0),
            ("2020-10-14", "2020-10-14_CBC_chemistry_2.json", "RDW", 138.0),  # дубль
            ("2021-01-05", "doc:lab.pdf", "CEA", 2.0),                        # чистое — не в счёт
            ("2021-02-10", "instrument:pro12", "pro12_q1", 3.0),               # опросник — не в счёт
        ])
        c.commit()
    n, calls = _run_sensor(db, monkeypatch)
    assert n == 2                       # только два *_chemistry.json
    assert any("single-writer" in lbl for lbl, _ in calls)


def test_sensor_silent_on_clean_canon(db, monkeypatch):
    with db.get_conn() as c:
        c.execute("INSERT INTO lab_results(date,source,test_name,value) "
                  "VALUES('2021-05-20','doc:lab eng 20.05.21.pdf','RDW',14.2)")
        c.commit()
    n, calls = _run_sensor(db, monkeypatch)
    assert n == 0
    assert calls == []
