"""Оракул границы группировки тренда: что LOINC разрешает слить, а что запрещает.

До этого файла утверждение «схлопывание вариантов, отличающихся ТОЛЬКО методом,
не может сдвинуть тренд» жило аргументом в докстринге `loinc_match._generic_pick`
и не имело механизма, который покраснеет при его нарушении. Аргумент верен,
ТОЛЬКО ЕСЛИ слой трендов группирует по `component`; здесь это закреплено с обеих
сторон — и что сливается, и что не смеет.

Вторая половина файла — про честность пустоты. Неотображённое имя обязано отдать
`points=None`, а не `[]`: пустой список читается потребителем как «анализов не
было», и такая ложь однажды уже приписала стрессу дыру в данных.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _terms(**codes):
    """{код: (component, system, property)} → индекс в форме, которую ждёт читатель."""
    return {k: {"loinc_num": k, "component": v[0], "system": v[1],
                "property": v[2], "loinc_version": "2.82"}
            for k, v in codes.items()}


def _m(our_name, unit, loinc_num, specimen="blood"):
    return {"our_name": our_name, "unit": unit, "specimen": specimen,
            "loinc_num": loinc_num}


# ── что СЛИВАЕТСЯ: различие только в методе ──

def test_method_only_difference_is_one_trend():
    """`by Automated count` и `by Manual count` — один и тот же аналит, измеренный
    по-разному. Ключ тренда `component`, поэтому выбор между ними физически не
    может сдвинуть кривую. Это и есть та автоматика, которую разрешает
    `_generic_pick`; здесь у неё появляется оракул."""
    import health_db  # noqa: F401 — health_db ПЕРВЫМ: иначе циркуляр labs_db↔health_db
    import labs_db
    terms = _terms(**{"auto-1": ("Lymphocytes", "Bld", "NCnc"),
                      "manual-1": ("Lymphocytes", "Bld", "NCnc")})
    mappings = [_m("Lymphocytes_abs", "10*9/l", "auto-1"),
                _m("Lymph_abs_old", "10*9/l", "manual-1")]
    got = labs_db.trend_members(mappings, terms, "Lymphocytes_abs")
    assert got["issue"] is None
    assert {m["our_name"] for m in got["members"]} == {"Lymphocytes_abs", "Lymph_abs_old"}


# ── что НЕ смеет слиться: материал и вещество ──

def test_different_specimen_is_not_one_trend():
    """`Bilirubin.total` в Serum и в Blood — разные измерения. Слить их значит
    построить тренд по двум разным средам и не заметить этого."""
    import health_db  # noqa: F401 — health_db ПЕРВЫМ: иначе циркуляр labs_db↔health_db
    import labs_db
    terms = _terms(**{"ser": ("Bilirubin.total", "Ser/Plas", "SCnc"),
                      "bld": ("Bilirubin.total", "Bld", "SCnc")})
    mappings = [_m("Bilirubin_total", "umol/l", "ser"),
                _m("Bilirubin_whole", "umol/l", "bld")]
    got = labs_db.trend_members(mappings, terms, "Bilirubin_total")
    assert [m["our_name"] for m in got["members"]] == ["Bilirubin_total"]


def test_different_component_is_not_one_trend():
    """Синтетические отображения различают общий и ионизированный компонент. Совпадения имени недостаточно для объединения."""
    import health_db  # noqa: F401 — health_db ПЕРВЫМ: иначе циркуляр labs_db↔health_db
    import labs_db
    terms = _terms(**{"total": ("Calcium", "Bld", "MCnc"),
                      "ion": ("Calcium.ionized", "Bld", "SCnc")})
    mappings = [_m("Calcium", "mg/dl", "total"), _m("Calcium", "mmol/l", "ion")]
    assert labs_db.trend_members(mappings, terms, "Calcium", unit="mg/dl")["group"][0] == "calcium"
    assert labs_db.trend_members(mappings, terms, "Calcium", unit="mmol/l")["group"][0] == "calcium.ionized"


def test_name_without_unit_is_ambiguous_not_guessed():
    """Если имя без единицы попадает в разные группы, читатель сообщает ambiguous вместо догадки."""
    import health_db  # noqa: F401 — health_db ПЕРВЫМ: иначе циркуляр labs_db↔health_db
    import labs_db
    terms = _terms(**{"mass": ("Cobalamins", "Bld", "MCnc"),
                      "subst": ("Cobalamins", "Ser/Plas", "SCnc")})
    mappings = [_m("Vitamin_B12", "pg/ml", "mass"), _m("Vitamin_B12", "pmol/l", "subst")]
    got = labs_db.trend_members(mappings, terms, "Vitamin_B12")
    assert got["issue"] == "ambiguous"
    assert got["group"] is None and got["members"] == []


def test_unmapped_name_says_so():
    import health_db  # noqa: F401 — health_db ПЕРВЫМ: иначе циркуляр labs_db↔health_db
    import labs_db
    got = labs_db.trend_members([], {}, "MPV")
    assert got["issue"] == "unmapped"


def test_decision_pointing_outside_reference_is_loud():
    """Решение ссылается на код, которого в справочнике нет. Такое бывает при
    смене версии LOINC (устаревший код переносится через MapTo.csv) — и молчать
    об этом значит строить тренд по решению, смысл которого мы больше не знаем."""
    import health_db  # noqa: F401 — health_db ПЕРВЫМ: иначе циркуляр labs_db↔health_db
    import labs_db
    got = labs_db.trend_members([_m("Ferritin", "ng/ml", "снятый-код")], {}, "Ferritin")
    assert got["issue"] == "unknown_code"


def test_unit_notation_does_not_split_the_group():
    """`mg/dL` и `mg/dl` — одна единица. Лист выбора однажды уже спросил владельца
    трижды про одно и то же, потому что сравнивал сырые написания."""
    import health_db  # noqa: F401 — health_db ПЕРВЫМ: иначе циркуляр labs_db↔health_db
    import labs_db
    terms = _terms(**{"c": ("Creatinine", "Bld", "MCnc")})
    mappings = [_m("Creatinine", "mg/dl", "c")]
    assert labs_db.trend_members(mappings, terms, "Creatinine", unit="mg/dL")["issue"] is None


# ── вход через БД: канон + присоединённый справочник ──

@pytest.fixture
def db(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    monkeypatch.setattr(health_db, "REFERENCE_DIR", tmp_path / "ref")
    monkeypatch.setattr(health_db, "LOINC_DB_PATH", tmp_path / "ref" / "loinc.db")
    (tmp_path / "ref").mkdir()
    rc = sqlite3.connect(tmp_path / "ref" / "loinc.db")
    rc.execute("CREATE TABLE loinc_terms (loinc_num TEXT PRIMARY KEY, component TEXT, "
               "property TEXT, system TEXT, scale_typ TEXT, long_common_name TEXT, "
               "shortname TEXT, status TEXT, example_units TEXT, in_universal_order INT, "
               "loinc_version TEXT, loaded_at TEXT)")
    rc.execute("INSERT INTO loinc_terms(loinc_num, component, property, system, loinc_version) "
               "VALUES ('16695-9','Cobalamins','MCnc','Bld','2.82')")
    rc.commit()
    rc.close()
    health_db.init_db()
    health_db._ensure_lab_table()
    with health_db.get_conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS lab_name_loinc (our_name TEXT NOT NULL, "
                  "unit TEXT, specimen TEXT NOT NULL DEFAULT 'blood', loinc_num TEXT NOT NULL, "
                  "decided_by TEXT NOT NULL, rule TEXT, decided_at TEXT, "
                  "PRIMARY KEY (our_name, unit, specimen))")
        c.execute("INSERT INTO lab_name_loinc(our_name,unit,specimen,loinc_num,decided_by) "
                  "VALUES ('Vitamin_B12','pg/ml','blood','16695-9','owner')")
        c.executemany("INSERT INTO lab_results(date,test_name,value,unit) VALUES (?,?,?,?)",
                      [("2040-04-09", "Vitamin_B12", 572.0, "pg/ml"),
                       ("2038-02-12", "Vitamin_B12", 386.0, None)])
        c.commit()
    return health_db


def test_unmapped_returns_none_not_empty_list(db):
    """ГЛАВНОЕ свойство читателя. `[]` означало бы «анализов не было» — при том
    что строки есть, просто имя не отображено. `None` ломает наивного
    потребителя громко, и это выбрано намеренно."""
    import health_db  # noqa: F401 — health_db ПЕРВЫМ: иначе циркуляр labs_db↔health_db
    import labs_db
    got = labs_db.get_lab_trend_by_component("MPV")
    assert got["points"] is None
    assert "unmapped" in got["issues"]


def test_tenant_without_mapping_table_is_empty_map_not_crash(db):
    """Легаси-тенант без таблицы отображений получает points=None и no_mapping_table. Отсутствующая карта не должна ронять весь бриф."""
    import health_db  # noqa: F401
    import labs_db
    with health_db.get_conn() as c:
        c.execute("DROP TABLE lab_name_loinc")
        c.commit()
    got = labs_db.get_lab_trend_by_component("Vitamin_B12")
    assert got["points"] is None and "no_mapping_table" in got["issues"]
    assert got["n_rows"] == 2, "число строк за именем считается независимо от карты"
    assert labs_db.get_lab_trend_by_component("Albumin")["n_rows"] == 0


def test_row_without_unit_does_not_join_the_scale(db):
    """Строка канона без единицы вовсе (так приезжают старые бланки). Подмешать
    её к пг/мл значит утверждать шкалу, которой в документе не было."""
    import health_db  # noqa: F401 — health_db ПЕРВЫМ: иначе циркуляр labs_db↔health_db
    import labs_db
    got = labs_db.get_lab_trend_by_component("Vitamin_B12", unit="pg/ml")
    assert [p["value"] for p in got["points"]] == [572.0]
    assert [u["value"] for u in got["unresolved"]] == [386.0]
    assert "unresolved_units" in got["issues"]


def test_point_carries_its_own_unit_and_reference_version(db):
    """Старый `get_lab_trend` не возвращает единицу вовсе — потребитель тренда
    физически не знает шкалу. И версия справочника едет вместе с ответом: без
    неё вопрос «по какому LOINC это решено» не задать."""
    import health_db  # noqa: F401 — health_db ПЕРВЫМ: иначе циркуляр labs_db↔health_db
    import labs_db
    got = labs_db.get_lab_trend_by_component("Vitamin_B12", unit="pg/ml")
    assert got["points"][0]["unit"] == "pg/ml"
    assert got["points"][0]["loinc_num"] == "16695-9"
    assert got["reference_version"] == "2.82"


# ── граница конверсии: где шкала, а где другое вещество ──

def test_same_substance_different_scale_becomes_one_trend():
    """`Creatinine` мг/дл и мкмоль/л — одно вещество, две шкалы: `component` и
    материал совпадают, различие только в `property`. Коэффициент берётся из
    единственного дома (`lab_canon._TO_CONVENTIONAL`), второго не заводим."""
    import health_db  # noqa: F401
    import labs_db
    terms = _terms(**{"mass": ("Creatinine", "Bld", "MCnc"),
                      "subst": ("Creatinine", "Bld", "SCnc")})
    mappings = [_m("Creatinine", "mg/dl", "mass"), _m("Creatinine", "umol/l", "subst")]
    got = labs_db.trend_members(mappings, terms, "Creatinine")
    assert got["issue"] is None, "шкала не повод отказывать в тренде"
    assert got["group"] == ("creatinine", "bld", "mcnc"), "целевая — конвенциональная шкала"


def test_different_substance_is_not_merged_even_when_factors_are_close():
    """ГЛАВНАЯ граница. У `Calcium` мг/дл — общий, ммоль/л — ИОНИЗИРОВАННЫЙ, и
    коэффициенты у них почти совпадают (4.0 и 4.008). Если бы решение о сведении
    принималось по единице, 1.3 ммоль/л превратились бы в 5.2 мг/дл и легли в
    тренд общего кальция как «падение вдвое». Решает `component`, не единица."""
    import health_db  # noqa: F401
    import labs_db
    terms = _terms(**{"total": ("Calcium", "Bld", "MCnc"),
                      "ion": ("Calcium.ionized", "Bld", "SCnc")})
    mappings = [_m("Calcium", "mg/dl", "total"), _m("Calcium", "mmol/l", "ion")]
    got = labs_db.trend_members(mappings, terms, "Calcium")
    assert got["issue"] == "ambiguous"


def test_scale_difference_without_a_factor_says_so_by_name():
    """`Ferritin` нг/мл и пмоль/л — одно вещество в одном материале, но правила
    пересчёта для него в `_TO_CONVENTIONAL` НЕТ. Отказ обязан называться
    `no_factor`, а не `ambiguous`: это разные задачи для человека — «назначь
    коэффициент» против «реши, одно ли это вещество».

    NB: пример менялся дважды — сперва `Vitamin_D`, потом `Vitamin_B12`, и оба раза
    тест ПОКРАСНЕЛ в тот момент, когда коэффициент этому аналиту заводили (§11,
    догфуд на своём же диффе). Красный был верным: устаревал пример, не механизм.
    Урок: писать такой тест на предмете, который чинить не собираешься."""
    import health_db  # noqa: F401
    import labs_db
    terms = _terms(**{"mass": ("Ferritin", "Ser/Plas", "MCnc"),
                      "subst": ("Ferritin", "Ser/Plas", "SCnc")})
    mappings = [_m("Ferritin", "ng/ml", "mass"), _m("Ferritin", "pmol/l", "subst")]
    got = labs_db.trend_members(mappings, terms, "Ferritin")
    assert got["issue"] == "no_factor"


# ── решение в одной шкале, данные в другой ──

def test_mapping_in_si_finds_data_in_conventional(db):
    """В синтетическом примере отображение использует SI, строки — конвенциональную единицу. Обе стороны ключа должны нормализоваться одинаково."""
    import health_db  # noqa: F401
    import labs_db
    with db.get_conn() as c:
        db.attach_reference(c)
        c.execute("INSERT INTO loinc_terms(loinc_num, component, property, system, "
                  "loinc_version) VALUES ('14814-8','Lipoprotein.beta','SCnc','Ser/Plas','2.82')")
        c.execute("INSERT INTO lab_name_loinc(our_name,unit,specimen,loinc_num,decided_by) "
                  "VALUES ('LDL','mmol/l','blood','14814-8','owner')")
        c.executemany("INSERT INTO lab_results(date,test_name,value,unit) VALUES (?,?,?,?)",
                      [("2040-04-09", "LDL", 109.0, "mg/dL"),
                       ("2039-12-06", "LDL", 121.0, "mg/dL")])
        c.commit()
    got = labs_db.get_lab_trend_by_component("LDL")
    assert [p["value"] for p in got["points"]] == [121.0, 109.0]
    assert got["unresolved"] == []


def test_row_in_si_is_converted_into_the_conventional_trend(db):
    """Обратное направление: решение в мг/дл, строка в ммоль/л. Значение обязано
    приехать пересчитанным и ПОМЕЧЕННЫМ, а не тихо."""
    import health_db  # noqa: F401
    import labs_db
    with db.get_conn() as c:
        db.attach_reference(c)
        c.execute("INSERT INTO loinc_terms(loinc_num, component, property, system, "
                  "loinc_version) VALUES ('3043-7','Triglyceride','MCnc','Bld','2.82')")
        c.execute("INSERT INTO lab_name_loinc(our_name,unit,specimen,loinc_num,decided_by) "
                  "VALUES ('Triglycerides','mg/dl','blood','3043-7','owner')")
        c.execute("INSERT INTO lab_results(date,test_name,value,unit) "
                  "VALUES ('2026-06-01','Triglycerides',1.7,'ммоль/л')")
        c.commit()
    got = labs_db.get_lab_trend_by_component("Triglycerides")
    p = got["points"][0]
    assert p["converted"] is True and p["raw_unit"] == "ммоль/л"
    assert 149.0 < p["value"] < 152.0, f"1.7 ммоль/л ≈ 150 мг/дл, получено {p['value']}"
