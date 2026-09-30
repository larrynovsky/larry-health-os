"""build_specialized_context — сводка спец-панелей для консилиума (BL-LAB-CANON-2 T4).

Инвариант: НЕ вываливает все строки (сводка), явный тег давности (историческое),
аномальные вне референса. Устойчива к отсутствию таблицы/данных.
"""
import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    return health_db


def test_empty_returns_blank(db):
    import labs_db
    assert labs_db.build_specialized_context() == ""


def test_summary_recency_and_abnormal(db):
    import labs_db
    with db.get_conn() as c:
        c.executemany(
            "INSERT INTO specialized_lab_results(date,source,panel_type,analyte_raw,value,unit,ref_low,ref_high) "
            "VALUES(?,?,?,?,?,?,?,?)", [
                ("2021-06-14", "doc:b.pdf", "microbiome", "Bifidobacterium spp.", 350000, "", None, None),
                ("2021-06-14", "doc:b.pdf", "microbiome", "Candida spp.", 8000, "", None, None),
                # 2026-08-01: класс с домом «спец-слой», иначе строка уедет
                # в очередь сведения имён и клиническим контекстом не будет
                ("2021-06-14", "doc:b.pdf", "microbiome", "Anti-TG", 42.7, "Ед/мл", 0, 10),  # вне референса
            ])
        c.commit()
    ctx = labs_db.build_specialized_context()
    # сводка, не 3 строки данных
    assert "microbiome×3" in ctx
    # явный тег давности
    assert "ИСТОРИЧЕСКОЕ" in ctx and "2021-06-14" in ctx
    # аномальное вынесено
    assert "Anti-TG" in ctx and "42.7" in ctx


def test_no_abnormal_when_no_refs(db):
    import labs_db
    with db.get_conn() as c:
        c.execute("INSERT INTO specialized_lab_results(date,source,panel_type,analyte_raw,value,unit) "
                  "VALUES('2021-06-14','doc:b.pdf','microbiome','Лактат',1.6,'ммоль/л')")
        c.commit()
    ctx = labs_db.build_specialized_context()
    assert "microbiome×1" in ctx
    assert "Вне референса" not in ctx  # нет ref → нет ложных аномалий


def test_waiting_rows_are_not_clinical_context(db):
    """Ожидающая сведения имени строка канона не входит в сводку спец-панелей.

    Guard удерживает её в старом доме до приёма новым. Такая строка должна
    оставаться видимым долгом, но не готовым клиническим контекстом.
    """
    import labs_db
    with db.get_conn() as c:
        c.executemany(
            "INSERT INTO specialized_lab_results(date,source,panel_type,analyte_raw,value,unit,ref_low,ref_high) "
            "VALUES(?,?,?,?,?,?,?,?)", [
                ("2021-06-14", "doc:b.pdf", "microbiome", "Bifidobacterium spp.", 350000, "", None, None),
                # дом канон → ждёт сведения имени, клиническим контекстом не является
                ("2021-06-14", "doc:b.pdf", "cbc", "Ferritin", 21.3, "ng/ml", 30.0, 400.0),
            ])
        c.commit()
    ctx = labs_db.build_specialized_context()
    assert "microbiome×1" in ctx
    assert "cbc" not in ctx                      # не в сводке спец-панелей
    assert "Ferritin" not in ctx                 # и не в «вне референса»
    assert "ждут сведения имени к канону: 1" in ctx   # но долг ВИДЕН


def test_only_waiting_rows_produce_no_clinical_header(db):
    """НЕГАТИВНЫЙ КОНТРОЛЬ: если спец-панелей нет вовсе, заголовка о них тоже
    нет — иначе консилиум получил бы пустое утверждение о микробиоме."""
    import labs_db
    with db.get_conn() as c:
        c.execute("INSERT INTO specialized_lab_results(date,source,panel_type,analyte_raw,value,unit,ref_low,ref_high) "
                  "VALUES('2021-06-14','doc:b.pdf','cbc','Ferritin',21.3,'ng/ml',30.0,400.0)")
        c.commit()
    ctx = labs_db.build_specialized_context()
    assert "Спец-панели вне биохимии крови" not in ctx
    assert "ждут сведения имени к канону: 1" in ctx


def test_full_output_golden_master(db):
    """Характеризационный пин ВСЕГО вывода — оракул против дрейфа вида консилиума.

    Точечные тесты выше проверяют отдельные подстроки; между ними остаются
    неассертированные области, и ровно там 2026-08-01 спрятался дефект: целые
    панели крови печатались под клиническим заголовком «вне биохимии крови»,
    поймано ГЛАЗАМИ по §11, не тестом. Golden master фиксирует ВСЕ области
    разом — заголовок, счётчик панелей, «вне референса», инженерную строку
    долга, — так что молчаливый сдвиг того, ЧТО получает консилиум, краснеет
    здесь (закрывает долг снимка data-ingestion@6ff106e §5).

    Граница оракула названа вслух (§20, RST check≠test): это детектор ДРЕЙФА,
    не корректности. Он не судит, клинически ли верен текущий вид, — он ловит,
    что вид изменился. Годность самого пина снята глазами 2026-08-04: микробиом
    виден клинически, Ferritin/cbc (дом=канон) в клинику НЕ течёт, долг сведения
    имени виден инженерным счётчиком. Пин заодно закрепляет посев вердиктов
    (microbiome→specialized, cbc→canon): смена дома любого класса тоже покраснеет.
    """
    import labs_db
    with db.get_conn() as c:
        c.executemany(
            "INSERT INTO specialized_lab_results(date,source,panel_type,analyte_raw,value,unit,ref_low,ref_high) "
            "VALUES(?,?,?,?,?,?,?,?)", [
                ("2021-06-14", "doc:b.pdf", "microbiome", "Bifidobacterium spp.", 350000, "", None, None),
                ("2021-06-14", "doc:b.pdf", "microbiome", "Candida spp.", 8000, "", None, None),
                # non-canon, вне референса → выносится в клиническую сводку
                ("2021-06-14", "doc:b.pdf", "microbiome", "Anti-TG", 42.7, "Ед/мл", 0, 10),
                # дом=канон → ждёт сведения имени, в клинику течь НЕ должен
                ("2021-06-14", "doc:b.pdf", "cbc", "Ferritin", 21.3, "ng/ml", 30.0, 400.0),
            ])
        c.commit()
    expected = (
        "Спец-панели вне биохимии крови (2021-06-14..2021-06-14 — ИСТОРИЧЕСКОЕ, не текущее состояние):\n"
        "  microbiome×3\n"
        "  Вне референса (1): Anti-TG=42.7Ед/мл (норма 0.0–10.0)\n"
        "  [не клинический контекст] ждут сведения имени к канону: 1 строк(и) — в лабораторный вывод не входят"
    )
    assert labs_db.build_specialized_context() == expected


def test_unjudged_class_not_clinical(db):
    """Класс БЕЗ вердикта человека — fail-closed: не в клинику (находка 2026-08-05).

    Прежде фильтр `!= "canon"` пускал неверди́кченный класс в клинический
    заголовок «вне биохимии крови» — тот же §16-дефект, что 01.08, но для
    класса, которого человек ещё НЕ судил (кириллич. онкомаркеры, опечатка,
    новый бланк). Клинически показываем ТОЛЬКО дом=specialized; вердикта нет →
    счётчик «ждут вердикта», как canon-ждущие. Промоут §13 уже fail-closed на
    канон — читатель обязан быть fail-closed симметрично.
    """
    import labs_db
    with db.get_conn() as c:
        c.executemany(
            "INSERT INTO specialized_lab_results(date,source,panel_type,analyte_raw,value,unit,ref_low,ref_high) "
            "VALUES(?,?,?,?,?,?,?,?)", [
                ("2021-06-14", "doc:b.pdf", "microbiome", "Bifidobacterium spp.", 350000, "", None, None),
                # класс без вердикта (кириллица — НЕ сущность oncomarkers): в клинику течь НЕ должен
                ("2021-06-14", "doc:b.pdf", "онкомаркеры", "CA 15-3", 47.0, "Ед/мл", 0, 30),
            ])
        c.commit()
    ctx = labs_db.build_specialized_context()
    assert "microbiome×1" in ctx                    # вердикт specialized → клинически
    assert "онкомаркеры" not in ctx                 # нет вердикта → не в клинику
    assert "CA 15-3" not in ctx                     # и не в «вне референса»
    assert "класс без вердикта человека: 1" in ctx  # но долг ВИДЕН
