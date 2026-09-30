"""Оракул границы двух домов на СТОРОНЕ ПИСАТЕЛЕЙ (работа B, Ш1–Ш2, 2026-08-01).

Датчик `check_domain_boundary` краснеет ПОСЛЕ того, как строка уже уехала не
туда. Эти проверки краснеют ДО: они смотрят на решение маршрутизатора.

Проверяется три утверждения, каждое отдельно:
  1. класс строки называется одинаково для обоих писателей (один дом словаря);
  2. промоут в канон применяет вердикт человека НА САМОМ ДЕЛЕ, а не в docstring;
  3. класс без вердикта в канон не въезжает (fail-closed, §13 ступень 2).
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


def _row(raw, panel, cname=None, value=1.0):
    return {"raw_name": raw, "panel": panel, "canonical_name": cname or raw,
            "value": value, "unit": "mg/L", "date": "2021-06-14",
            "source_file": "x.pdf", "page_role": "data"}


# ── 1. Один дом словаря ───────────────────────────────────────────────────────

def test_class_named_once_for_both_writers():
    """Класс называет `lab_canon`, и обоим писателям он отвечает одинаково.

    До 2026-08-01 копий словаря было три: мёртвая в `lab_promote`, живая
    в `lab_specialized` и третья в `lab_staging_summary`. Первые две уже
    разошлись по `immunology` — расхождение меняло МАРШРУТ живых строк.
    """
    import lab_canon
    assert lab_canon.classify_row("Ртуть, Hg", "other") == "trace_elements"
    assert lab_canon.classify_row("Bifidobacterium spp.", "microbiome") == "microbiome"
    assert lab_canon.classify_row("Glucose", "chemistry") == "chemistry"
    assert lab_canon.classify_row("Alpha 1 (г/л)", "chemistry") == "electrophoresis"
    # panel — артефакт СТРАНИЦЫ: та же таблица микроэлементов на стр. 61
    assert lab_canon.classify_row("Селен, Se", "urine") == "metabolomics"


def test_no_second_home_of_the_dictionary():
    """Негатив на возврат копии: у промоута своего словаря панелей больше нет."""
    import lab_promote
    assert not hasattr(lab_promote, "BLOOD")
    import lab_staging_summary
    assert not hasattr(lab_staging_summary, "BLOOD")
    assert not hasattr(lab_staging_summary, "NONBLOOD")


def test_specimen_class_has_one_implementation():
    """Переезд `specimen_class` в общий предок — ссылка, не копия."""
    import lab_promote
    import lab_canon
    assert lab_promote.specimen_class is lab_canon.specimen_class


# ── 2. Фильтр применён на самом деле ──────────────────────────────────────────

def test_promote_blocks_row_whose_home_is_specialized(db):
    """Позитив: строка микробиома в канон НЕ идёт."""
    import lab_promote
    rows = [_row("Bifidobacterium spp.", "microbiome")]
    kept, blocked = lab_promote.prepare(rows, {})
    assert kept == []
    assert blocked[0][1].startswith("domain:microbiome→specialized")


def test_promote_keeps_row_whose_home_is_canon(db):
    """Негатив к предыдущему: фильтр не косит всё подряд."""
    import lab_promote
    rows = [_row("Glucose", "chemistry", value=90.0)]
    kept, blocked = lab_promote.prepare(rows, {})
    assert len(kept) == 1, blocked


def test_removing_the_verdict_reopens_the_gate(db):
    """ИСПОЛНЕННЫЙ негативный контроль: сними вердикт — микробиом поедет в канон.

    Не «фильтр есть», а «фильтр держит ИМЕННО ЭТУ строку». Мутация — не правка
    кода, а удаление данных, на которых код стоит: вердикт человека уходит
    из таблицы, и строка мгновенно перестаёт блокироваться по домену.
    """
    import lab_promote
    with db.get_conn() as c:
        c.execute("UPDATE lab_domain_verdicts SET home='canon' WHERE panel_type='microbiome'")
        c.commit()
    kept, blocked = lab_promote.prepare([_row("Bifidobacterium spp.", "microbiome")], {})
    assert not any(r for _r, r in blocked if r.startswith("domain:")), \
        "вердикт снят, а доменный блок остался — значит он не читает таблицу"


# ── 3. Класс без вердикта ─────────────────────────────────────────────────────

def test_class_without_verdict_never_enters_canon(db):
    """Новый класс обязан краснеть, а не проваливаться в умолчание."""
    import lab_promote
    rows = [_row("Нечто невиданное", "cosmic_rays")]
    kept, blocked = lab_promote.prepare(rows, {})
    assert kept == []
    assert "БЕЗ ВЕРДИКТА" in blocked[0][1]


def test_verdict_table_rejects_third_home(db):
    """CHECK-констрейнт вместо проверки в Python (ступень 3 лестницы).

    Домов ровно два. Третий обязан отвергаться у ЛЮБОГО писателя, включая
    ручной sqlite3 мимо всего кода проекта.
    """
    import sqlite3
    with db.get_conn() as c:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO lab_domain_verdicts"
                      "(panel_type,home,decided_on,oracle,rationale) "
                      "VALUES('x','limbo','2026-08-01','я','потому что')")


def test_specialized_skips_what_canon_already_took(db):
    """Симметрия: второй писатель тоже читает вердикт, а не свой словарь.

    Пропуск условный: строка уходит только после фактического приёма
    каноном. Иначе она исчезла бы из обеих таблиц.
    """
    import health_db
    import lab_specialized
    cols = ("run_id,extractor_version,source_file,date,panel,raw_name,"
            "canonical_name,value,unit")
    with db.get_conn() as c:
        c.executemany(
            f"INSERT INTO lab_results_staging({cols}) VALUES(?,?,?,?,?,?,?,?,?)", [
                ("r9", "v", "x.pdf", "2021-06-14", "microbiome",
                 "Bifidobacterium spp.", None, 5.0, "КОЕ/г"),
                ("r9", "v", "x.pdf", "2021-06-14", "chemistry",
                 "Glucose", "Glucose", 90.0, "mg/dL"),
            ])
        c.commit(); health_db._ensure_lab_table()  # боевая схема канона (specimen DEFAULT 'blood'), не своя — BL-LAB-SPECIMEN-FIXTURE-1
        c.execute("INSERT INTO lab_results(date,source,test_name,value) "
                  "VALUES('2021-06-14','doc:x.pdf','Glucose',90.0)")
        c.commit()
    res = lab_specialized.promote_specialized("r9", execute=False)
    assert res["plan"].get("канон принял (пропуск)") == 1
    assert res["plan"].get("specialized:microbiome") == 1
