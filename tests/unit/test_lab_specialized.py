"""Промоут спец-панелей в specialized_lab_results (BL-LAB-CANON-2 B).

Единственный писатель `specialized_lab_results`.

2026-08-01 (работа B): классификатор переехал в `lab_canon.classify_row`, а
решение «куда едет класс» перестало быть его делом — оно живёт в таблице
`lab_domain_verdicts` как решение человека. Поэтому корпус ниже разделён на
два вопроса, которые до сих пор были склеены в один:
  • КАК СТРОКА НАЗЫВАЕТСЯ (класс) — характеризация, меняться не должна;
  • КУДА ОНА ЕДЕТ (дом) — данные, меняются вердиктом человека.
Склейка и была тем механизмом, которым правило разъехалось по двум писателям.
"""
import pytest

import lab_canon
import lab_specialized as ls


# ── класс строки: характеризация, не должна меняться ──

@pytest.mark.parametrize("raw,panel,exp_class", [
    ("Glucose", "chemistry", "chemistry"),
    ("Hemoglobin", "cbc", "cbc"),
    ("TSH", "hormones", "hormones"),
    ("Билирубин", "urine", "urine"),
    ("Bifidobacterium spp.", "microbiome", "microbiome"),
    ("Антитела к париетальным клеткам", "immunoreactivity", "autoantibodies"),
    ("Арахидоновая (AA 20:4n6)", "other", "fatty_acids"),
    ("Омега-3 ЖК", "other", "fatty_acids"),
    ("Гастрин", "other", "gastro_markers"),
    ("Кальпротектин", "other", "stool_markers"),
    ("2-Кетоглутаровая кислота", "other", "metabolomics"),
    ("CD3+ (Т-лимфоциты), %", "other", "immunophenotype"),
    ("Alpha 1", "other", "electrophoresis"),
    ("Стеариновая (SA 18:0)", "other", "fatty_acids"),   # насыщенная N:0
    ("Свинец, Pb", "other", "trace_elements"),
    ("Литий, Li***", "other", "trace_elements"),
    ("Триптофан (TRY)", "other", "amino_acids"),
    ("Триметиламин-N-оксид (TMAO)", "other", "metabolomics"),
    # override: электрофорез-фракции в panel=chemistry
    ("Alpha 1", "chemistry", "electrophoresis"),
    ("Gamma (г/л)", "chemistry", "electrophoresis"),
    # override: мочевые орг.кислоты (panel=urine) → метаболомика
    ("Гиппуровая кислота (N-бензоилглицин)", "urine", "metabolomics"),
    # АЦИЛГЛИЦИНЫ (14.09): в профиле органических кислот есть глицин-конъюгаты,
    # у которых слова «кислота» в имени нет. До правки они уезжали классом `urine`,
    # то есть в канон, мимо решения владельца о доме профильной панели.
    ("3-Метилкротонилглицин", "urine", "metabolomics"),
    ("Изовалерилглицин (N-изопентаноилглицин)", "urine", "metabolomics"),
    # Граница с ДРУГОЙ стороны, без неё предыдущие два зеленели бы и на правиле,
    # утягивающем в метаболомику любую аминокислоту с корнем «глицин».
    ("Глицин", "other", "amino_acids"),
    ("Глицин (GLY)", "other", "amino_acids"),
    # Строка профиля, у которой каноническое имя УЖЕ есть, остаётся в каноне:
    # класс `urine` → дом канон. Это названная граница решения 14.09, а не пропуск.
    ("Urine_Methylmalonic_acid", "urine", "urine"),
    # реальный анализ мочи остаётся панелью мочи
    ("Лейкоциты (колич.)", "urine", "urine"),
    ("Нитриты", "urine", "urine"),
    ("GGT", "chemistry", "chemistry"),   # не спутать с Gamma-фракцией
    # шестая фракция электрофореза: имя совпадает с биохимическим альбумином,
    # разводит суффикс единицы (формат бланка с электрофорезом белков)
    ("Albumin (г/л)", "chemistry", "electrophoresis"),
    # граница с ДРУГОЙ стороны — биохимический альбумин обязан остаться в тренде.
    # Односторонний тест зеленел бы и на правиле, утягивающем в электрофорез ВСЕ
    # альбумины: тогда из тренда молча исчезли бы все бланки с биохимическим альбумином.
    ("Albumin-B", "chemistry", "chemistry"),
    ("Альбумин", "chemistry", "chemistry"),
    ("Albumin/Globulin-B", "chemistry", "chemistry"),
    ("Albumin", "chemistry", "chemistry"),
    ("Тестостерон, связанный с альбумином", "hormones", "hormones"),
    ("Нечто неведомое", "other", "other"),
])
def test_class_naming(raw, panel, exp_class):
    assert lab_canon.classify_row(raw, panel) == exp_class


# ── промоутер ──

@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()  # создаёт specialized_lab_results + вердикты через миграцию
    with health_db.get_conn() as c:
        health_db._ensure_lab_table()  # боевая схема канона (specimen DEFAULT 'blood'), не своя — BL-LAB-SPECIMEN-FIXTURE-1
        cols = ("run_id,extractor_version,source_file,date,panel,canonical_name,raw_name,value,unit,value_agreement,review_status")
        c.executemany(
            f"INSERT INTO lab_results_staging({cols}) VALUES(?,?,?,?,?,?,?,?,?,?,?)", [
            ("r1","v","booklet.pdf","2021-06-14","chemistry","Glucose","Glucose",88.2,"mg/dL","agree","auto"),
            ("r1","v","booklet.pdf","2021-06-14","microbiome",None,"Bifidobacterium spp.",50000.0,"","agree","auto"),
            ("r1","v","booklet.pdf","2021-06-14","microbiome",None,"Candida spp.",None,"","single","pending"),
            ("r1","v","booklet.pdf","2021-06-14","immunoreactivity",None,"Антитела к TG IgA",12.4,"Ед/мл","agree","auto"),
            ("r1","v","booklet.pdf","2021-06-14","other",None,"Омега-3 ЖК",0.21,"ммоль/л","agree","auto"),
        ])
        c.commit()
    return health_db


def test_verdict_decides_the_home_not_the_classifier(db):
    """Дом класса берётся из таблицы человека, а не из литерала в коде."""
    import labs_db
    assert labs_db.domain_home("microbiome") == "specialized"
    assert labs_db.domain_home("trace_elements") == "canon"
    assert labs_db.domain_home("chemistry") == "canon"
    assert labs_db.domain_home("класс_которого_нет") is None


def test_promote_dry_run_no_write(db):
    r = ls.promote_specialized("r1", execute=False)
    assert r["executed"] is False
    # Glucose, Антитела к TG IgA, Омега-3 — дом канон по вердикту владельца
    assert r["plan"]["дом канон, но канон НЕ принял — держим здесь"] == 3
    assert r["plan"]["пропуск (нет ни числа, ни текста)"] == 1   # Candida пуста целиком
    assert r["to_insert"] == 4      # Bifido + три «дом канон, дверь закрыта»
    assert r["no_verdict"] == {}
    with db.get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM specialized_lab_results").fetchone()[0] == 0


def test_promote_execute_and_idempotent(db):
    r = ls.promote_specialized("r1", execute=True)
    assert r["inserted"] == 4
    with db.get_conn() as c:
        rows = c.execute("SELECT panel_type, analyte_raw FROM specialized_lab_results").fetchall()
    # микробиом — свой дом; остальные три держим, потому что канон их не принял
    assert {row[0] for row in rows} == {"microbiome", "chemistry", "autoantibodies",
                                        "fatty_acids"}
    with db.get_conn() as c:
        # Candida (value=None) НЕ хранится; Glucose (дом канон) НЕ в specialized
        assert c.execute("SELECT COUNT(*) FROM specialized_lab_results WHERE analyte_raw='Candida spp.'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM specialized_lab_results WHERE analyte_raw='Glucose'").fetchone()[0] == 1
    # идемпотентность: повторный execute не дублирует (delete-by-source + reinsert)
    r2 = ls.promote_specialized("r1", execute=True)
    assert r2["inserted"] == 0


def test_accepted_here_gets_terminal_status_held_rows_do_not(db):
    """Принятая строка получает терминальный статус specialized.
    Строка, ожидающая приёма в канон, остаётся pending,
    иначе датчик очереди перестанет её видеть."""
    ls.promote_specialized("r1", execute=False)
    with db.get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM lab_results_staging "
                         "WHERE review_status='specialized'").fetchone()[0] == 0
    r = ls.promote_specialized("r1", execute=True)
    assert r["marked_specialized"] >= 1
    with db.get_conn() as c:
        st = dict(c.execute("SELECT raw_name, COALESCE(review_status,'pending') "
                            "FROM lab_results_staging").fetchall())
    assert st["Bifidobacterium spp."] == "specialized"
    assert st["Glucose"] == "auto"             # дом канон — держим, статус не трогаем
    assert st["Candida spp."] == "pending"     # пустая строка сюда не принята — не помечена


def test_class_without_verdict_stays_here_and_is_counted(db):
    """Класс без вердикта не пропадает и не едет в канон — он виден счётчиком.

    Умолчание безопасное (не в биохимию крови), но ОБЪЯВЛЕННОЕ: молчаливое
    умолчание и есть тот способ, которым заводятся дыры.
    """
    with db.get_conn() as c:
        c.execute("INSERT INTO lab_results_staging"
                  "(run_id,extractor_version,source_file,date,panel,raw_name,value,unit) "
                  "VALUES('r1','v','booklet.pdf','2021-06-14','cosmic_rays',"
                  "'Мюоны, µ',7.0,'шт/см2')")
        c.commit()
    r = ls.promote_specialized("r1", execute=False)
    assert r["no_verdict"] == {"cosmic_rays": 1}
    assert r["plan"]["specialized:cosmic_rays"] == 1   # здесь, а не в каноне


# ── материал и роль страницы (нить loinc-name-home, 2026-07-31) ──

@pytest.fixture
def db_specimen(tmp_path, monkeypatch):
    """Три строки одной панели microbiome: прочитанный материал, страница-график
    и строка без прочитанного материала. Синтетика той же формы, что у многостраничного бланка с микробиомом."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "h2"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "h2" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "h2" / "data" / "health.db")
    health_db.init_db()
    with health_db.get_conn() as c:
        health_db._ensure_lab_table()  # боевая схема канона (specimen DEFAULT 'blood'), не своя — BL-LAB-SPECIMEN-FIXTURE-1
        cols = ("run_id,extractor_version,source_file,page,date,panel,raw_name,value,"
                "unit,specimen,specimen_source,page_role")
        c.executemany(
            f"INSERT INTO lab_results_staging({cols}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("r2","v","b.pdf",4,"2021-06-14","microbiome","Bacillus cereus",27.0,"",
             "мазке из зева","read_footer","data"),
            ("r2","v","b.pdf",5,"2021-06-14","microbiome","Bacillus cereus",-27.0,"",
             "мазке из зева","read_footer","derived_chart"),
            ("r2","v","b.pdf",2,"2021-06-14","microbiome","Clostridium perfringens",
             300000.0,"КОЕ/г",None,"unknown","data"),
        ])
        c.commit()
    return health_db


def test_read_material_beats_rule_literal(db_specimen):
    """НЕГАТИВНЫЙ КОНТРОЛЬ. Прежний порядок (`spec or r["specimen"]`) отдавал бы
    `stool` от панели microbiome — материал назначался, а не читался."""
    ls.promote_specialized("r2", execute=True)
    with db_specimen.get_conn() as c:
        got = c.execute("SELECT specimen FROM specialized_lab_results "
                        "WHERE analyte_raw='Bacillus cereus'").fetchall()
    assert [r[0] for r in got] == ["throat_swab"]


def test_derived_chart_page_is_not_imported(db_specimen):
    """Производная диаграмма не должна импортироваться как измерение."""
    r = ls.promote_specialized("r2", execute=False)
    assert r["plan"]["страница-график (пропуск)"] == 1
    ls.promote_specialized("r2", execute=True)
    with db_specimen.get_conn() as c:
        vals = [row[0] for row in c.execute(
            "SELECT value FROM specialized_lab_results WHERE analyte_raw='Bacillus cereus'")]
    assert vals == [27.0]           # отклонение −27 не доехало
    assert -27.0 not in vals


def test_unknown_step_keeps_the_old_rule(db_specimen):
    """Строка без прочитанного материала обязана вести себя как раньше —
    иначе правка сменила бы поведение там, где нового знания нет."""
    ls.promote_specialized("r2", execute=True)
    with db_specimen.get_conn() as c:
        got = c.execute("SELECT specimen FROM specialized_lab_results "
                        "WHERE analyte_raw='Clostridium perfringens'").fetchone()
    assert got[0] is None          # класс microbiome не даёт подсказки материала


# ── решение ревью и качественный результат (2026-08-01) ──

def test_rejected_row_never_travels(db):
    """Решение человека исполняют ОБА писателя, а не один.

    До 2026-08-01 фильтр `rejected` стоял только у промоута крови. Третий
    случай той же формы за нить (первым был page_role, вторым — граница
    доменов). Стоил он ноль строк только по везению.
    """
    with db.get_conn() as c:
        c.execute("UPDATE lab_results_staging SET review_status='rejected' "
                  "WHERE raw_name='Bifidobacterium spp.'")
        c.commit()
    r = ls.promote_specialized("r1", execute=False)
    assert r["to_insert"] == 3      # ушёл только Bifido, три «дверь закрыта» остались


def test_null_review_status_still_travels(db):
    """НЕГАТИВНЫЙ КОНТРОЛЬ к предыдущему. В SQLite `NULL != 'rejected'` даёт
    NULL, и строка без статуса (разбор до появления ревью) выпала бы молча."""
    with db.get_conn() as c:
        c.execute("UPDATE lab_results_staging SET review_status=NULL "
                  "WHERE raw_name='Bifidobacterium spp.'")
        c.commit()
    r = ls.promote_specialized("r1", execute=False)
    assert r["to_insert"] == 4


def test_qualitative_result_is_not_lost(db):
    """Отсутствие числа не означает отсутствие качественного результата."""
    with db.get_conn() as c:
        c.execute("UPDATE lab_results_staging SET value=NULL, "
                  "value_text='не обнаружено' WHERE raw_name='Candida spp.'")
        c.commit()
    r = ls.promote_specialized("r1", execute=True)
    with db.get_conn() as c:
        got = c.execute("SELECT value, value_text FROM specialized_lab_results "
                        "WHERE analyte_raw='Candida spp.'").fetchone()
    assert got is not None and got[0] is None and got[1] == "не обнаружено"


def test_empty_row_is_still_dropped(db):
    """НЕГАТИВНЫЙ КОНТРОЛЬ: строка без числа И без текста — по-прежнему мусор."""
    r = ls.promote_specialized("r1", execute=False)
    assert r["plan"]["пропуск (нет ни числа, ни текста)"] == 1


# ── дверь нового дома (2026-08-01) ──

def test_row_does_not_leave_home_until_canon_took_it(db):
    """Если канон ещё не принял строку, специализированный слой её держит.
    Безусловный пропуск потерял бы строку в обоих хранилищах."""
    r = ls.promote_specialized("r1", execute=False)
    assert r["plan"]["дом канон, но канон НЕ принял — держим здесь"] == 3
    assert "канон принял (пропуск)" not in r["plan"]


def test_row_leaves_as_soon_as_canon_has_it(db):
    """НЕГАТИВНЫЙ КОНТРОЛЬ: как только строка появилась в каноне — уходит отсюда.

    Guard самозалечивается и не превращается в вечный склад: предикат
    спрашивает ФАКТ наличия, а не правила приёма канона.
    """
    with db.get_conn() as c:
        # материал у строки staging не прочитан из бланка, поэтому в ключе он пуст —
        # канонная строка обязана совпасть по ТОМУ ЖЕ ключу, а не по догадке
        c.execute("INSERT INTO lab_results(date,source,test_name,value) "
                  "VALUES('2021-06-14','doc:booklet.pdf','Glucose',88.2)")
        c.commit()
    r = ls.promote_specialized("r1", execute=False)
    assert r["plan"]["канон принял (пропуск)"] == 1
    assert r["plan"]["дом канон, но канон НЕ принял — держим здесь"] == 2


# ── идентичность в guard (2026-08-12, ADR analyte-identity-lives-in-name) ──

def test_row_leaves_when_canon_holds_dimension_suffixed_identity(db):
    """Канон принял строку под СВЕДЁННЫМ именем с суффиксом размерности —
    guard обязан узнать её тем же правилом, что датчик комнаты ожидания
    (`lab_canon.identity_name`, один дом). До правки буквальный ключ звал
    «Незрелые гранулоциты (IG)» и `Immature_granulocytes_abs` разными."""
    with db.get_conn() as c:
        c.execute("INSERT INTO lab_results_staging(run_id,extractor_version,"
                  "source_file,date,panel,canonical_name,raw_name,value,unit,"
                  "value_agreement,review_status) VALUES('r2','v','booklet.pdf',"
                  "'2021-06-14','cbc',NULL,'Незрелые гранулоциты (IG)',0.03,"
                  "'10^9/л','agree','auto')")
        c.execute("INSERT INTO lab_results(date,source,test_name,value,unit) "
                  "VALUES('2021-06-14','doc:booklet.pdf',"
                  "'Immature_granulocytes_abs',0.03,'10^9/л')")
        c.commit()
    r = ls.promote_specialized("r2", execute=False)
    assert r["plan"].get("канон принял (пропуск)") == 1
    assert "дом канон, но канон НЕ принял — держим здесь" not in r["plan"]


def test_identity_on_another_date_or_source_does_not_release(db):
    """НЕГАТИВНЫЙ КОНТРОЛЬ (R3): идентичность БЕЗ даты/источника — «канон видел
    такой аналит», не «это измерение принято». Строка остаётся здесь, иначе
    guard отпускал бы измерение по чужому измерению той же величины."""
    with db.get_conn() as c:
        c.execute("INSERT INTO lab_results_staging(run_id,extractor_version,"
                  "source_file,date,panel,canonical_name,raw_name,value,unit,"
                  "value_agreement,review_status) VALUES('r3','v','booklet.pdf',"
                  "'2021-06-14','cbc',NULL,'Незрелые гранулоциты (IG)',0.03,"
                  "'10^9/л','agree','auto')")
        c.execute("INSERT INTO lab_results(date,source,test_name,value,unit) "
                  "VALUES('2021-06-22','doc:booklet.pdf',"
                  "'Immature_granulocytes_abs',0.03,'10^9/л')")   # другая дата
        c.execute("INSERT INTO lab_results(date,source,test_name,value,unit) "
                  "VALUES('2021-06-14','doc:another.pdf',"
                  "'Immature_granulocytes_abs',0.03,'10^9/л')")   # другой источник
        c.commit()
    r = ls.promote_specialized("r3", execute=False)
    assert r["plan"]["дом канон, но канон НЕ принял — держим здесь"] == 1
    assert "канон принял (пропуск)" not in r["plan"]


# ── Частичный прогон сохраняет остальные строки документа ──

def test_partial_run_does_not_wipe_the_rest_of_the_document(tmp_path, monkeypatch):
    """Замена по ключу сохраняет остальные страницы синтетического документа.
    Краснеет при возврате DELETE по одному source вместо полного ключа."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "h"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "h" / "data").mkdir(parents=True)
    import health_db, lab_specialized
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "h" / "data" / "health.db")
    health_db.init_db()

    SRC = "big.pdf"
    STORED = "doc:" + SRC     # промоут префиксует источник (lab_specialized:148)
    with health_db.get_conn() as c:
        # уже в спец-слое: строка с ДРУГОЙ страницы ТОГО ЖЕ документа. Источник обязан
        # совпасть с тем, что напишет промоут, иначе тест не воспроизводит коллизию —
        # первая редакция этого теста была зелёной именно потому, что источники
        # разъехались («big.pdf» против «doc:big.pdf»).
        c.execute("INSERT INTO specialized_lab_results"
                  "(date, source, panel_type, specimen, analyte_raw, value, unit) "
                  "VALUES('2021-06-12',?,'amino_acids','urine','Аргинин',12.0,'мкмоль/л')", (STORED,))
        cols = ("run_id,extractor_version,source_file,date,panel,canonical_name,raw_name,"
                "value,value_text,unit,value_agreement,review_status,page,page_role")
        c.execute("INSERT INTO lab_results_staging(" + cols + ") "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("rp", "v", SRC, "2021-06-12", "microbiome", None, "Candida spp.",
                   None, "не обнаружено", "", "agree", "pending", 14, "data"))
        c.commit()
        assert c.execute("SELECT COUNT(*) FROM lab_results_staging").fetchone()[0] == 1

    lab_specialized.promote_specialized("rp", execute=True)

    with health_db.get_conn() as c:
        got = sorted(tuple(r) for r in c.execute(
            "SELECT panel_type, analyte_raw FROM specialized_lab_results WHERE source=?", (STORED,)))
    assert ("amino_acids", "Аргинин") in got, f"частичный прогон снёс чужую страницу: {got}"
    assert ("microbiome", "Candida spp.") in got, f"новая строка не доехала: {got}"
