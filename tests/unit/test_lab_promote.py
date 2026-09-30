"""Unit-тесты промоута staging→канон (lab_promote).

Проверяет правило A1 (заменить лаб / удалить дубли / сохранить не-лаб) и
Гейт-2 полноты (старый аналит без покрытия в новом → lost, не молча удалён).
Канон НЕ трогаем — только dry-run.
"""
import sqlite3
import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    # Схема канона — ПРОДАКШН-функциями (tests/conftest.py::canon_schema). Прежде
    # здесь стоял «минимальный lab_results» руками: второй дом схемы, зеленевший на
    # таблице беднее боевой. 2026-08-08 промоут повёз value_text, и разъезд стал виден.
    from tests.conftest import canon_schema
    canon_schema(health_db)
    with health_db.get_conn() as c:
        # старый канон: лаб (заменить), не-покрытый лаб (lost), два опросника (сохранить)
        c.executemany("INSERT INTO lab_results(date,source,test_name,value) VALUES(?,?,?,?)", [
            ("2025-03-11", "Clinic A", "RDW", 136.0),       # покрыт новым → delete
            ("2025-03-11", "Clinic B", "Cholesterol", 185.0),        # НЕ покрыт → lost
            ("2026-01-20", "instrument:pro12", "pro12_q1", 3.0),     # preserve
            ("2021-06-14", "instrument:isi", "Hemoglobin", 13.9),    # preserve
        ])
        # новый staging (run r1): кровь
        cols = "run_id,extractor_version,source_file,date,panel,canonical_name,raw_name,value,value_agreement,review_status"
        c.executemany(
            f"INSERT INTO lab_results_staging({cols}) VALUES(?,?,?,?,?,?,?,?,?,?)", [
            ("r1", "v", "lab_a.pdf", "2025-03-11", "cbc", "RDW", "RDW", 13.6, "agree", "auto"),
            ("r1", "v", "lab_a.pdf", "2025-03-11", "chemistry", "Glucose", "Glucose", 94.0, "agree", "auto"),
        ])
        c.commit()
    return health_db


def test_dry_run_categories(db):
    import lab_promote
    r = lab_promote.plan("r1", None, execute=False)
    assert r["preserve"] == 2          # два instrument:*
    assert r["delete"] == 1            # только покрытое новым (RDW); Cholesterol не удаляется
    assert r["promote"] == 2          # RDW + Glucose новые
    assert r["lost"] == 1             # Cholesterol старый не покрыт новым → сохраняется


def test_execute_preserves_uncovered(db):
    import lab_promote
    lab_promote.plan("r1", None, execute=True)
    with db.get_conn() as c:
        rows = {(r["test_name"], r["value"]) for r in
                c.execute("SELECT test_name, value FROM lab_results")}
    # старый RDW=136 заменён новым 13.6; Glucose добавлен
    assert ("RDW", 13.6) in rows and ("RDW", 136.0) not in rows
    assert ("Glucose", 94.0) in rows
    # непокрытое старое (Cholesterol) и не-лаб (два опросника) сохранены
    assert ("Cholesterol", 185.0) in rows
    assert ("pro12_q1", 3.0) in rows and ("Hemoglobin", 13.9) in rows
    assert len(rows) == 5


def test_execute_preserves_same_analyte_other_date(db):
    """РЕГРЕСС (2026-07-01): промоут аналита на ОДНОЙ дате не должен стирать его
    историю по ДРУГИМ датам. Баг Гейт-2 (`and on not in new_names`) удалял всю
    кросс-датовую историю аналита, если прогон содержал его хоть на одной дате."""
    import lab_promote
    with db.get_conn() as c:
        # тот же аналит RDW, но на ДРУГОЙ дате, не-preserved источник
        c.execute("INSERT INTO lab_results(date,source,test_name,value) VALUES(?,?,?,?)",
                  ("2024-01-01", "Clinic A", "RDW", 12.8))
        c.commit()
    lab_promote.plan("r1", None, execute=True)   # прогон промоутит RDW@2025 года
    with db.get_conn() as c:
        rows = {(r["date"], r["test_name"], r["value"]) for r in
                c.execute("SELECT date,test_name,value FROM lab_results")}
    assert ("2024-01-01", "RDW", 12.8) in rows      # история другой даты СОХРАНЕНА (баг бы удалил)
    assert ("2025-03-11", "RDW", 13.6) in rows      # покрытая дата заменена новым
    assert ("2025-03-11", "RDW", 136.0) not in rows


# --- ОРАКУЛ обещания реестра `lab_recognizer/staging_then_human_gate` ---------------
# Замер У-1 (2026-07-30) показал: обещание «отклонённое (review_status) не проходит» не
# было защищено НИЧЕМ. Фильтр можно было убрать целиком — отклонённые ЧЕЛОВЕКОМ строки
# поехали бы в канон, и ни один тест, ни один датчик, ни ночной монитор не покраснели бы.
# Реестр при этом продолжал показывать `holds`, потому что проверял ПРИСУТСТВИЕ слов
# `review_status` и `--execute` в файле, а не поведение.
#
# Синтетические rejected-строки проверяют решение ревьюера как рабочий гейт.

_STG_COLS = ("run_id,extractor_version,source_file,date,panel,canonical_name,"
             "raw_name,value,value_agreement,review_status")


def _add_staging(conn, rows):
    conn.executemany(
        f"INSERT INTO lab_results_staging({_STG_COLS}) VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()


def test_human_rejected_never_reaches_canon(db):
    """Отклонённая человеком строка не попадает в канон; соседняя неотклонённая — попадает.

    Два утверждения в одном тесте НАМЕРЕННО. Без второго тест зеленел бы и в случае
    «промоут не работает вообще» — ровно тот ложно-зелёный, против которого он написан.

    Оракул по ЗНАЧЕНИЮ, а не по имени: канонизация (`_canon`/aliases) может переименовать
    аналит, и проверка вида `("Ferritin", …) not in rows` прошла бы, даже если строка
    уехала в канон под другим именем. Значение переименование не меняет.

    Аналит и даты выбраны так, чтобы не спорить с другими гейтами промоута: `Glucose`
    уже проходит Layer-2 в фикстуре выше, отдельные даты исключают дедуп и конфликт
    значений (иначе тест мог бы краснеть по причине, к инварианту не относящейся).

    ЗНАЧЕНИЯ ОБЯЗАНЫ БЫТЬ ФИЗИОЛОГИЧНЫМИ, и это не педантизм. Первая редакция брала
    маркеры 999/888 — оракул правдоподобия заблокировал их как `impossible-value`, и
    позитивный контроль упал по причине, к человек-гейту не относящейся. Тест поймал
    собственную фикстуру, а не механизм: тот же класс, что «мутация, которая молча не
    мутировала». Маркеры уникальны внутри правдоподобного диапазона глюкозы.
    """
    import lab_promote
    with db.get_conn() as c:
        _add_staging(c, [
            ("r1", "v", "lab_a.pdf", "2025-07-07", "chemistry",
             "Glucose", "Glucose", 99.1, "agree", "rejected"),
            ("r1", "v", "lab_a.pdf", "2025-07-08", "chemistry",
             "Glucose", "Glucose", 88.2, "agree", "pending"),
        ])
    lab_promote.plan("r1", None, execute=True)
    with db.get_conn() as c:
        values = {r["value"] for r in c.execute("SELECT value FROM lab_results")}
    assert 99.1 not in values, \
        "строка, отклонённая ЧЕЛОВЕКОМ, попала в канон — человек-гейт не держит"
    assert 88.2 in values, \
        "неотклонённая строка НЕ попала в канон: тест зеленел бы впустую, оракул мёртв"


def test_null_review_status_promotes_like_pending(db):
    """`review_status IS NULL` ведёт себя как `pending`, а не исчезает молча.

    В SQLite NULL != 'rejected' даёт NULL и исключает строку из выборки.
    DEFAULT защищает новые вставки, но не явно записанный NULL; COALESCE
    должен сохранить семантику pending и для него.

    Смысл умолчания берётся из схемы, а не выдумывается: `DEFAULT 'pending'` уже
    объявляет, что неотсмотренная строка — `pending`, а `pending` промоутится.
    """
    import lab_promote
    with db.get_conn() as c:
        _add_staging(c, [
            ("r1", "v", "lab_a.pdf", "2025-07-09", "chemistry",
             "Glucose", "Glucose", 77.3, "agree", None),
        ])
    lab_promote.plan("r1", None, execute=True)
    with db.get_conn() as c:
        values = {r["value"] for r in c.execute("SELECT value FROM lab_results")}
    assert 77.3 in values, \
        "строка с NULL review_status исчезла молча (SQLite: NULL != 'rejected' → NULL)"


def test_приток_считается_наравне_с_потерей(db):
    """Зеркало гейта полноты. «Потеряно 0» и «не пришло лишнего» — РАЗНЫЕ
    утверждения, и второе из первого не следует.

    Без обратной проверки новые имена из диаграмм и прозы могли бы
    незаметно увеличить канон. Контроль считает и потери, и приток."""
    import lab_promote
    with db.get_conn() as c:
        cols = ("run_id,extractor_version,source_file,date,panel,canonical_name,"
                "raw_name,value,value_agreement,review_status")
        c.execute(f"INSERT INTO lab_results_staging({cols}) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  ("r1", "v", "lab_a.pdf", "2025-03-11", "chemistry",
                   "Ferritin", "Ferritin", 61.0, "agree", "auto"))
        c.commit()
    r = lab_promote.plan("r1", None, execute=False)
    # Ferritin в фикстуре канона не было НИКОГДА — обязан быть виден как приток
    assert "Ferritin" in r["fresh_names"], r
    assert r["arrived"] >= 1
    # RDW в каноне уже есть (2025) → это замена, а не приток
    assert "RDW" not in r["fresh_names"], r


def test_известное_имя_на_новой_дате_не_приток(db):
    """РАЗЛИЧЕНИЕ, ради которого предикат и нужен: новая ТОЧКА известного аналита —
    норма (лонгитюд так и растёт), новое ИМЯ — сигнал. Мешать их значит получить
    шумный счётчик, который перестанут читать.

    Тест добавлен после мутации: гашение сверки с `old_names` НЕ роняло прежний
    набор проверок, то есть оракул был прокси. Cholesterol лежит в каноне фикстуры
    на 2025 года; здесь он приходит на другую дату."""
    import lab_promote
    with db.get_conn() as c:
        cols = ("run_id,extractor_version,source_file,date,panel,canonical_name,"
                "raw_name,value,value_agreement,review_status")
        c.execute(f"INSERT INTO lab_results_staging({cols}) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  ("r2", "v", "lab_b.pdf", "2025-10-02", "lipids",
                   "Cholesterol", "Cholesterol", 176.0, "agree", "auto"))
        c.commit()
    import lab_canon
    r = lab_promote.plan("r2", None, execute=False)
    canon = lab_canon.normalize("Cholesterol")
    assert r["arrived"] >= 1, r                # новая ТОЧКА приехала
    assert canon not in r["fresh_names"], r    # но имя не новое


def test_приток_не_считает_замену_притоком(db):
    """НЕГАТИВНЫЙ КОНТРОЛЬ, и он поймал мою собственную неверную ожидаемость с
    первого прогона. Без него предикат зелен и у счётчика, который просто считает
    ВСЕ промоутнутые строки: тот показывал бы приток на каждой замене.

    В фикстуре промоутятся двое: RDW (в каноне уже есть на ту же дату — замена) и
    Glucose (в каноне нет вовсе — приток). Различить их и есть предмет."""
    import lab_promote
    r = lab_promote.plan("r1", None, execute=False)
    assert r["promote"] == 2                  # промоутнуто двое
    assert r["fresh_names"] == ["Glucose"]    # а приток — ровно один


# ── Монотонность частичных записей (2026-08-08, прогон линз) ────────────────

def test_stale_run_cannot_be_promoted_over_a_newer_one(db):
    """Старый прогон не должен откатывать более новое распознавание.
    Гейт монотонности обязан отказать до разрушающей записи."""
    import lab_promote
    cols = ("run_id,extractor_version,source_file,date,panel,canonical_name,raw_name,"
            "value,unit,value_agreement,review_status,created_at")
    with db.get_conn() as c:
        c.executemany(f"INSERT INTO lab_results_staging({cols}) "
                      f"VALUES({','.join('?' * 12)})", [
            # старый прогон — уже промоутнут
            ("old", "v", "form.pdf", "2025-07-01", "chemistry", "Glucose", "Glucose",
             90.0, "mg/dL", "agree", "promoted", "2026-08-08 10:00:00"),
            # новый прогон того же документа — промоутнут позже
            ("new", "v", "form.pdf", "2025-07-01", "chemistry", "Glucose", "Glucose",
             95.0, "mg/dL", "agree", "promoted", "2026-08-08 12:00:00"),
            # и ещё один старый, НЕ промоутнутый — его и пробуем применить
            ("stale", "v", "form.pdf", "2025-07-01", "chemistry", "Glucose", "Glucose",
             80.0, "mg/dL", "agree", "pending", "2026-08-08 09:00:00"),
        ])
        c.commit()
    with pytest.raises(ValueError) as e:
        lab_promote.plan("stale", None, execute=False)
    assert "СТАРЕЕ" in str(e.value)
    assert "form.pdf" in str(e.value), "в отказе не назван источник — человеку нечего чинить"


def test_newest_run_promotes_normally(db):
    """НЕГАТИВНЫЙ КОНТРОЛЬ: свежий прогон обязан проходить, иначе гейт = отказ работать."""
    import lab_promote
    cols = ("run_id,extractor_version,source_file,date,panel,canonical_name,raw_name,"
            "value,unit,value_agreement,review_status,created_at")
    with db.get_conn() as c:
        c.executemany(f"INSERT INTO lab_results_staging({cols}) "
                      f"VALUES({','.join('?' * 12)})", [
            ("old", "v", "form.pdf", "2025-07-01", "chemistry", "Glucose", "Glucose",
             90.0, "mg/dL", "agree", "promoted", "2026-08-08 10:00:00"),
            ("fresh", "v", "form.pdf", "2025-07-01", "chemistry", "Glucose", "Glucose",
             95.0, "mg/dL", "agree", "pending", "2026-08-08 12:00:00"),
        ])
        c.commit()
    lab_promote.plan("fresh", None, execute=False)     # не должно бросить
