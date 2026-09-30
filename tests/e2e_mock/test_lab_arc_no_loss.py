"""E2E лаб-дуги: recognizer-результат → staging → promote → канон, БЕЗ ПОТЕРЬ.

Инцидент 2026-07-01: баг lab_promote (Гейт-2) обрушил канон 1426→490 —
массовое удаление непокрытых строк. Восстановлено из бэкапа, unit-пины
добавлены. Здесь — сквозная дуга на РЕАЛИСТИЧНОМ каноне (много аналитов/дат),
проверяющая инвариант «промоут покрывает 2 аналита на 1 дате → остальное
не трогается» на масштабе, где mass-wipe был бы виден.

Vision-ансамбль (lab_recognizer.recognize) замокан: даём готовый res-словарь
и прогоняем РЕАЛЬНЫЕ lab_backfill._write_rows (handoff в staging) +
lab_promote.plan (promote в канон). Слой e2e_mock.
"""
import sqlite3

import pytest

pytestmark = pytest.mark.e2e_mock


# 20 строк канона: 18 из не-preserved источников (кандидаты на delete/lost),
# 2 preserved (два instrument:*). Промоут покроет ТОЛЬКО RDW@2021-05-20.
_CANON_SEED = [
    # (date, source, test_name, value)
    ("2021-05-20", "Clinic Hospital", "RDW", 142.0),        # покрыт новым → replace
    ("2021-05-20", "Clinic Hospital", "Hemoglobin", 13.1),  # НЕ покрыт → survive (lost)
    ("2021-05-20", "Clinic Hospital", "Glucose", 95.0),     # покрыт новым → replace
    ("2024-01-01", "HealthFund", "RDW", 13.0),                 # др. дата → survive
    ("2024-01-01", "HealthFund", "Creatinine", 0.9),           # survive
    ("2023-05-05", "Clinic", "Cholesterol", 170.0),         # survive
    ("2023-05-05", "Clinic", "LDL", 100.0),                 # survive
    ("2023-05-05", "Clinic", "HDL", 55.0),                  # survive
    ("2022-11-11", "Synevo", "TSH", 2.1),                   # survive
    ("2022-11-11", "Synevo", "T4", 1.2),                    # survive
    ("2022-11-11", "Synevo", "Ferritin", 80.0),             # survive
    ("2021-07-07", "HealthFund", "CEA", 1.3),                  # survive
    ("2021-07-07", "HealthFund", "CA19-9", 4.7),               # survive
    ("2020-03-03", "Clinic", "ALT", 25.0),                  # survive
    ("2020-03-03", "Clinic", "AST", 22.0),                  # survive
    ("2019-09-09", "Clinic", "Albumin", 4.5),               # survive
    ("2019-09-09", "Clinic", "Bilirubin", 0.8),             # survive
    ("2018-01-01", "HealthFund", "WBC", 6.2),                  # survive
    ("2021-02-10", "instrument:pro12", "pro12_q1", 3.0),      # preserved
    ("2020-08-12", "instrument:isi", "B12", 400.0),           # preserved
]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH",
                        tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    # Схема канона — ПРОДАКШН-функциями (tests/conftest.py::canon_schema), а не копией
    # DDL здесь: копия разъезжается с продом молча (замер 2026-08-08 — value_text).
    from tests.conftest import canon_schema
    canon_schema(health_db)
    with health_db.get_conn() as c:
        c.executemany(
            "INSERT INTO lab_results(date,source,test_name,value) VALUES(?,?,?,?)",
            _CANON_SEED)
        c.commit()
    return health_db


def _recognizer_result():
    """Мок вывода lab_recognizer.recognize: 2 аналита крови на одну дату,
    оба agree (→ auto). Покрывают RDW и Glucose существующего канона."""
    tests = [
        {"page": 1, "date": "2021-05-20", "panel": "cbc",
         "raw_name": "RDW", "canonical_name": "RDW", "value": 14.2,
         "unit": "%", "ref_low": 11.5, "ref_high": 14.5, "doc_flag": None,
         "pass1_value": 14.2, "pass2_value": 14.2, "parser_value": 14.2,
         "value_agreement": "agree", "confidence": "high", "date_source": "read"},
        {"page": 1, "date": "2021-05-20", "panel": "chemistry",
         "raw_name": "Glucose", "canonical_name": "Glucose", "value": 100.0,
         "unit": "mg/dL", "ref_low": 70, "ref_high": 100, "doc_flag": None,
         "pass1_value": 100.0, "pass2_value": 100.0, "parser_value": 100.0,
         "value_agreement": "agree", "confidence": "high", "date_source": "read"},
    ]
    res = {"tests": tests, "extractor_version": "test-v1",
           "date": "2021-05-20", "stats": {"disagreements": 0, "singles": 0}}
    verdict = {"status": "green", "issues": {}}
    return res, verdict


def _canon_snapshot(health_db):
    with health_db.get_conn() as c:
        return sorted((r["date"], r["test_name"], r["value"])
                      for r in c.execute(
                          "SELECT date,test_name,value FROM lab_results"))


def test_dry_run_leaves_canon_untouched(db):
    """Промоут БЕЗ execute не трогает канон ни на строку (guard против
    случайного разрушения — класс 1426→490)."""
    import lab_backfill, lab_promote
    res, verdict = _recognizer_result()
    lab_backfill._write_rows("run_e2e", "lab_2021-05-20.pdf", res, verdict)

    before = _canon_snapshot(db)
    r = lab_promote.plan("run_e2e", None, execute=False)
    after = _canon_snapshot(db)

    assert before == after, "dry-run изменил канон — разрушающая операция по умолчанию!"
    assert r["promote"] == 2                # RDW + Glucose
    assert r["lost"] >= 15                  # весь непокрытый канон помечен сохраняемым


def test_execute_replaces_covered_preserves_rest(db):
    """Сквозная дуга: 2 покрытых аналита заменены, ВЕСЬ остальной канон выжил.
    Именно этот инвариант нарушал баг 1426→490."""
    import lab_backfill, lab_promote
    res, verdict = _recognizer_result()
    n = lab_backfill._write_rows("run_e2e", "lab_2021-05-20.pdf", res, verdict)
    assert n == 2                            # handoff recognizer→staging сработал

    n_before = len(_CANON_SEED)
    lab_promote.plan("run_e2e", None, execute=True)
    after = _canon_snapshot(db)

    # Покрытые заменены новым значением на той же дате
    assert ("2021-05-20", "RDW", 14.2) in after
    assert ("2021-05-20", "RDW", 142.0) not in after
    assert ("2021-05-20", "Glucose", 100.0) in after
    assert ("2021-05-20", "Glucose", 95.0) not in after

    # НИ ОДНА непокрытая строка не потеряна (ядро no-loss)
    survivors = [
        ("2021-05-20", "Hemoglobin", 13.1), ("2024-01-01", "RDW", 13.0),
        ("2024-01-01", "Creatinine", 0.9), ("2023-05-05", "Cholesterol", 170.0),
        ("2023-05-05", "LDL", 100.0), ("2022-11-11", "TSH", 2.1),
        ("2021-07-07", "CEA", 1.3), ("2021-07-07", "CA19-9", 4.7),
        ("2020-03-03", "ALT", 25.0), ("2019-09-09", "Albumin", 4.5),
        ("2018-01-01", "WBC", 6.2),
        ("2021-02-10", "pro12_q1", 3.0), ("2020-08-12", "B12", 400.0),  # preserved
    ]
    for row in survivors:
        assert row in after, f"ПОТЕРЯНА строка канона: {row} (регресс 1426→490)"

    # Канон не схлопнулся: было N, заменили 2 на 2 → осталось N (не меньше)
    assert len(after) == n_before, (
        f"канон схлопнулся: было {n_before}, стало {len(after)}")
