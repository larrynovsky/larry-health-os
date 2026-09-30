"""Триаж pending staging (BL-LAB-CANON-2 #66, T1).

Low-confidence строки → rejected (мусор) / review (человек) / gold (узкий авто).
Ключевой инвариант: low-confidence single НЕ становится gold (не авто-промоут).
"""
import pytest

import lab_triage as lt


CANON = {("2021-05-10", "HGB"), ("2021-05-10", "RDW")}   # уже в каноне (RDW — в %)


@pytest.mark.parametrize("row,exp", [
    ({"raw_name": "DRUG_X", "canonical_name": None, "unit": "€", "value": 3317, "value_agreement": "single", "date": "x", "date_source": "read"}, "rejected"),
    ({"raw_name": "RDW", "canonical_name": "RDW", "unit": "%", "value": 155, "value_agreement": "agree", "date": "x", "date_source": "read"}, "rejected"),   # невозможно
    # Идентичность по размерности (2026-08-31): 42 фл — RDW_SD, норма 39–46. До правки
    # судился границами RDW-CV (9–30 %) и уходил в rejected как «невозможно» (синтетика
    # той же формы). Ожидание gold: agree+канон+ново+read.
    ({"raw_name": "Стандартного сдвига ширины RBC", "canonical_name": "RDW", "unit": "fl", "value": 42.0, "value_agreement": "agree", "date": "x", "date_source": "read"}, "gold"),
    # Транслит единицы (2026-08-31): «mkmol/l» = мкмоль/л → 80 = 0.90 mg/dL, норма.
    # Без алиаса 80 читалось как mg/dL и тоже уходило в «невозможно». Та же строка —
    # негативный контроль двойной конверсии: до 2026-08-31 `_impossible` пересчитывал
    # значение сам и отдавал оракулу с исходной единицей, тот пересчитывал второй раз
    # (÷88.4 → 0.009 < 0.1) — ЛЮБОЙ креатинин в мкмоль/л уходил в rejected.
    ({"raw_name": "Креатинин", "canonical_name": "Creatinine", "unit": "мкмоль/л", "value": 80.0, "value_agreement": "agree", "date": "x", "date_source": "read"}, "gold"),
    ({"raw_name": "Креатинин", "canonical_name": "Creatinine", "unit": "mkmol/l", "value": 80.0, "value_agreement": "agree", "date": "x", "date_source": "read"}, "gold"),
    ({"raw_name": "Hemoglobin", "canonical_name": "HGB", "unit": "g/dL", "value": 13, "value_agreement": "agree", "date": "2021-05-10", "date_source": "read"}, "rejected"),  # дубль в каноне
    ({"raw_name": "Неведомый маркер", "canonical_name": None, "unit": "", "value": 5, "value_agreement": "agree", "date": "x", "date_source": "read"}, "review"),  # unmapped
    # Немаппированный БЕЗ результата — группа 2 how-to (2026-08-31): человеку решать не о чем.
    ({"raw_name": "MİKROSKOPİ", "canonical_name": None, "unit": "", "value": None, "value_text": None, "value_agreement": "single", "date": "x", "date_source": "read"}, "rejected"),
    # …а качественный текст — остаётся человеку.
    ({"raw_name": "Прозрачность", "canonical_name": None, "unit": "", "value": None, "value_text": "мутная", "value_agreement": "single", "date": "x", "date_source": "read"}, "review"),
    # Дескрипторы мочи (решение владельца 2026-08-31): описание, не измерение → reject без человека.
    ({"raw_name": "Прозрачность", "canonical_name": None, "panel": "urine", "unit": "", "value": None, "value_text": "прозрачная", "value_agreement": "single", "date": "x", "date_source": "read"}, "rejected"),
    ({"raw_name": "GÖRÜNÜM", "canonical_name": None, "panel": "urine", "unit": "", "value": None, "value_text": "BERRAK", "value_agreement": "single", "date": "x", "date_source": "read"}, "rejected"),
    # …но то же слово вне панели мочи правилом не судится (граница названа).
    ({"raw_name": "Цвет", "canonical_name": None, "panel": "other", "unit": "", "value": None, "value_text": "жёлтый", "value_agreement": "single", "date": "x", "date_source": "read"}, "review"),
    # Композит микроскопии без клетки — дубль строк-клеток (2026-08-31).
    ({"raw_name": "MİKROSKOPİ", "canonical_name": None, "panel": "urine", "unit": "", "value": None, "value_text": "1-2 LÖKOSİT GÖRÜLDÜ", "value_agreement": "single", "date": "x", "date_source": "read"}, "rejected"),
    # Идентичность в ключе коллизии: RDW_SD в фл при RDW-CV в % в каноне того же дня — не коллизия.
    ({"raw_name": "RDW", "canonical_name": "RDW", "unit": "fl", "value": 42.0, "value_agreement": "agree", "date": "2021-05-10", "date_source": "read"}, "gold"),
    ({"raw_name": "Glucose", "canonical_name": "Glucose", "unit": "mg/dL", "value": 95, "value_agreement": "single", "date": "x", "date_source": "read"}, "review"),  # single → НЕ gold
    ({"raw_name": "Glucose", "canonical_name": "Glucose", "unit": "mg/dL", "value": 95, "value_agreement": "agree", "date": "x", "date_source": "inherited"}, "review"),  # дата не read
    ({"raw_name": "Glucose", "canonical_name": "Glucose", "unit": "mg/dL", "value": 95, "value_agreement": "agree", "date": "x", "date_source": "read"}, "gold"),   # золото
])
def test_classify_buckets(row, exp):
    bucket, _ = lt._classify(row, CANON)
    assert bucket == exp


def test_low_confidence_single_not_gold():
    # инвариант v2: single никогда не авто-промоутится
    row = {"raw_name": "Ferritin", "canonical_name": "Ferritin", "unit": "ng/ml",
           "value": 100, "value_agreement": "single", "date": "2024-01-01", "date_source": "read"}
    assert lt._classify(row, set())[0] == "review"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    with health_db.get_conn() as c:
        # `unit` добавлена 2026-08-08: детектор коллизии сравнивает значения в
        # ОДНОЙ шкале, поэтому единица канона ему нужна. Колонка есть в боевой
        # схеме (её стережёт check_lab_unit_present) — фикстура просто отставала
        # от реальности, а тест на урезанной схеме зелен по неверной причине (§20).
        c.execute("CREATE TABLE IF NOT EXISTS lab_results(id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, source TEXT, test_name TEXT, value REAL, unit TEXT)")
        cols = "run_id,extractor_version,source_file,date,panel,canonical_name,raw_name,value,unit,value_agreement,date_source,review_status"
        c.executemany(f"INSERT INTO lab_results_staging({cols}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("full2","v","a.pdf","2024-01-01","chemistry","Glucose","Glucose",95.0,"mg/dL","agree","read","pending"),   # gold
            ("full2","v","a.pdf","2024-01-01","chemistry","RDW","RDW",155.0,"%","agree","read","pending"),             # impossible→reject
            ("backlog","v","b.pdf","2024-02-02","chemistry","Glucose","Glucose",90.0,"mg/dL","single","read","pending"),  # single→review
            # кросс-ран конфликт: тот же (date,cn) в двух прогонах, разные значения → review
            ("full2","v","a.pdf","2024-03-03","cbc","PLT","PLT",300.0,"","agree","read","pending"),
            ("backlog","v","b.pdf","2024-03-03","cbc","PLT","PLT",250.0,"","agree","read","pending"),
        ])
        c.commit()
    return health_db


def test_triage_marks_and_writewrite(db):
    r = lt.triage(execute=True)
    assert r["buckets"].get("rejected") == 1     # RDW
    assert r["buckets"].get("gold") == 1         # Glucose full2
    # single + 2×PLT-конфликт → review = 3
    assert r["buckets"].get("review") == 3
    with db.get_conn() as c:
        st = dict(c.execute("SELECT raw_name||'/'||run_id, review_status FROM lab_results_staging").fetchall())
    assert st["PLT/full2"] == "review" and st["PLT/backlog"] == "review"   # Write-Write оба в review
    assert st["Glucose/full2"] == "gold"


def test_triage_execute_stamps_status_changed_at(tmp_path, monkeypatch):
    """Смена статуса несёт момент смены: датчик очереди меряет от него, а не от
    разбора документа (init_db добавляет колонку идемпотентно)."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "h"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "h" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "h" / "data" / "health.db")
    monkeypatch.setattr(lt, "_hdb", health_db)
    health_db.init_db()
    from tests.conftest import canon_schema
    canon_schema(health_db)          # lab_results нужен триажу для ключей дедупа
    with health_db.get_conn() as c:
        c.execute("INSERT INTO lab_results_staging(run_id, extractor_version, source_file, "
                  "date, panel, raw_name, canonical_name, value, unit, value_agreement, "
                  "review_status, date_source) VALUES ('r1','v','b.pdf','2026-01-01','urine', "
                  "'Неведомое нечто', NULL, 5.0, '', 'agree', 'pending', 'read')")
        c.commit()
    lt.triage(execute=True)
    with health_db.get_conn() as c:
        st, ts = c.execute("SELECT review_status, status_changed_at "
                           "FROM lab_results_staging").fetchone()
    assert st == "review" and ts, (st, ts)
