"""Характеризация каскада сопоставления имён с LOINC.

Главное свойство, которое здесь закреплено, — НЕ «сколько сматчилось», а
**неоднозначность не разрешается автоматически**. Для клинических имён
неверифицируемое совпадение хуже отсутствия: `T4 free` и `T4 total` отличаются
одним словом, и такая ошибка не покраснеет нигде — её увидит только владелец,
возможно, через год в тренде. Поэтому «выбрать наиболее похожего» здесь запрещено
тестом, а не обещанием в докстринге.

Каскад тестируется на фикстурном индексе: он принимает индекс аргументом ровно
затем, чтобы его можно было проверить без канона.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _meta(**over):
    base = {"ordered": 1, "system": "ser/plas", "scale": "qn",
            "property": "mcnc", "units": set(), "component": "X", "long": "X"}
    base.update(over)
    return base


@pytest.fixture()
def index():
    meta = {
        "A-blood-mass": _meta(),
        "A-blood-subst": _meta(property="scnc"),
        "A-urine": _meta(system="urine"),
        "A-qual": _meta(scale="ord"),
        "L-abs": _meta(property="ncnc"),
        "L-pct": _meta(property="nfr"),
    }
    by_name = {
        "analyte": {"A-blood-mass", "A-blood-subst", "A-urine", "A-qual"},
        "lymphocytes": {"L-abs", "L-pct"},
    }
    return meta, by_name


# ── decompose: наши собственные соглашения ──

@pytest.mark.parametrize("name,prop,system", [
    ("Lymphocytes_pct", "nfr", None),
    ("Lymphocytes_abs", "ncnc", None),
    ("Urine_Glucose", None, "urine"),
    ("Ferritin", None, None),
])
def test_our_naming_conventions_are_decoded(name, prop, system):
    """Суффикс и префикс несут информацию, которую иначе пришлось бы спрашивать
    у человека, — а мы её уже записали в имя."""
    import loinc_match
    _keys, p, s = loinc_match.decompose(name)
    assert (p, s) == (prop, system)


def test_synonym_bridge_is_used():
    """Мост через наш словарь даёт больше, чем все имённые источники LOINC
    вместе (замер: ненайденных 88 → 63). Если мост отвалится, сопоставление
    просядет молча — поэтому он под проверкой."""
    import loinc_match
    keys, _p, _s = loinc_match.decompose("HGB")
    assert "hemoglobin" in keys, "словарь lab_canon не подключён как мост"


# ── каскад ──

def test_property_from_suffix_narrows(index):
    meta, by_name = index
    import loinc_match
    assert loinc_match.candidates("Lymphocytes_abs", "10^3/uL", meta, by_name) == {"L-abs"}
    assert loinc_match.candidates("Lymphocytes_pct", "%", meta, by_name) == {"L-pct"}


def test_urine_prefix_selects_urine_system(index):
    meta, by_name = index
    import loinc_match
    assert loinc_match.candidates("Urine_analyte", "mg/dL", meta, by_name) == {"A-urine"}


def test_qualitative_scale_is_dropped_for_numeric_analytes(index):
    meta, by_name = index
    import loinc_match
    assert "A-qual" not in loinc_match.candidates("analyte", "mg/dL", meta, by_name)


def test_ambiguity_is_returned_not_resolved(index):
    """ЦЕНТРАЛЬНОЕ свойство: остались две вещественно разные позиции — отдаём обе.
    Молчаливый выбор «похожего» здесь и есть тот класс ошибки, который не краснеет."""
    meta, by_name = index
    import loinc_match
    got = loinc_match.candidates("analyte", "mg/dL", meta, by_name)
    assert got == {"A-blood-mass", "A-blood-subst"}
    assert len(got) > 1


def test_unit_narrows_when_loinc_declares_examples(index):
    """Фильтр единицы опирается на EXAMPLE_UCUM_UNITS самого LOINC. Где заполнено —
    сужает: замер показал HGB g/dL с 15 кандидатов до одного."""
    meta, by_name = index
    meta["A-blood-mass"]["units"] = {"mg/dl"}
    meta["A-blood-subst"]["units"] = {"mmol/l"}
    import loinc_match
    assert loinc_match.candidates("analyte", "mg/dL", meta, by_name) == {"A-blood-mass"}


def test_filter_never_empties_the_pool(index):
    """Фильтр применяется, только если не обнуляет набор: сузить до пустоты
    хуже, чем оставить выбор человеку — во втором случае решение хотя бы
    возможно."""
    meta, by_name = index
    import loinc_match
    got = loinc_match.candidates("analyte", "совершенно левая единица", meta, by_name)
    assert got, "фильтр единицы обнулил набор вместо того, чтобы отступить"


def test_ordered_set_is_a_tiebreak_not_a_sieve():
    """ПОРЯДОК фильтров, а не их состав. «Заказной набор» — признак частоты, и
    когда он стоял первым в каскаде, он выбрасывал верный код ДО всякой проверки
    смысла: у `Neutrophils_pct` в заказном наборе есть АНТИТЕЛА к нейтрофилам,
    а доля `Neutrophils/Leukocytes` (NFr) — нет. Лист выбора предлагал антитела;
    увидено глазами в самом листе 2026-07-29.

    Тест краснеет ровно на возврате «заказного» вперёд по каскаду: набор кодов
    подобран так, что при раннем применении остаётся антитело, при позднем —
    доля."""
    meta = {
        "AB-ordered": _meta(ordered=1, property="acnc"),
        "RATIO-not-ordered": _meta(ordered=0, property="nfr"),
    }
    by_name = {"neutrophils": {"AB-ordered", "RATIO-not-ordered"}}
    import loinc_match
    got = loinc_match.candidates("Neutrophils_pct", "%", meta, by_name)
    assert got == {"RATIO-not-ordered"}, (
        "«заказной» отсеял верный код до фильтра по смыслу — он обязан быть "
        "последним тай-брейком")


def test_loose_names_are_asked_only_when_precise_found_nothing():
    """Очерёдность индексов, не их состав. RELATEDNAMES2 называет и то, что
    рядом: у `CD19 cells/Lymphocytes` там есть «Lymphocytes», и по широкому
    индексу лейкоформула собирала 33 кандидата вместо 4 (замер 2026-07-29).
    Выбросить широкие тоже нельзя — непокрытых пар 13 → 46. Значит, порядок."""
    import loinc_match
    meta = {"PRECISE": _meta(), "LOOSE": _meta()}
    tight = {"analyte": {"PRECISE"}}
    loose = {"analyte": {"LOOSE"}, "другое": {"LOOSE"}}
    assert loinc_match.resolve_loinc("analyte", "mg/dL", meta, tight, loose) == {"PRECISE"}
    assert loinc_match.resolve_loinc("другое", "mg/dL", meta, tight, loose) == {"LOOSE"}


def test_method_only_variants_collapse_to_the_generic_code():
    """Автоматика законна ровно там, где не может сдвинуть тренд: ключ тренда —
    `component`, и при совпадении component+материал выбор между «by Automated
    count» и «by Manual count» на тренд не влияет."""
    import loinc_match
    meta = {
        "GEN": _meta(component="Lymphocytes/Leukocytes", long="Lymphocytes/Leukocytes in Blood"),
        "AUTO": _meta(component="Lymphocytes/Leukocytes", long="Lymphocytes/Leukocytes in Blood by Automated count"),
        "MAN": _meta(component="Lymphocytes/Leukocytes", long="Lymphocytes/Leukocytes in Blood by Manual count"),
    }
    assert loinc_match._generic_pick({"GEN", "AUTO", "MAN"}, meta) == "GEN"


def test_different_specimen_is_never_collapsed():
    """`Bilirubin.total` в сыворотке и в цельной крови — один component, но
    РАЗНЫЙ материал, и это клинически разные строки. Здесь автоматика обязана
    молчать, даже когда «общий» вариант ровно один."""
    import loinc_match
    meta = {
        "SER": _meta(component="Bilirubin.total", system="ser/plas",
                     long="Bilirubin.total [Mass/volume] in Serum or Plasma"),
        "BLD": _meta(component="Bilirubin.total", system="bld",
                     long="Bilirubin.total [Mass/volume] in Blood by Automated"),
    }
    assert loinc_match._generic_pick({"SER", "BLD"}, meta) is None


def test_different_component_is_never_collapsed():
    import loinc_match
    meta = {"A": _meta(component="T4", long="T4 free"),
            "B": _meta(component="T4.free", long="T4 free by Immunoassay")}
    assert loinc_match._generic_pick({"A", "B"}, meta) is None


# ── отсев по фактам о человеке ──

def test_newborn_material_is_impossible_for_an_adult():
    """`RBC^BldCo` — пуповинная кровь, `RBC^Fetus` — плод. У взрослого таких
    материалов не бывает, и предлагать их — тратить внимание там, где ответ
    известен из факта, а не из догадки."""
    import loinc_match
    meta = {"CORD": _meta(system="rbc^bldco"), "RBC": _meta(system="rbc")}
    facts = {"adult": True}
    assert loinc_match.impossible_for("CORD", meta, facts)
    assert loinc_match.impossible_for("RBC", meta, facts) is None


def test_no_fact_means_no_filter():
    """ГРАНИЦА: правило молчит, пока факта нет. Отсеивать по незнанию хуже, чем
    не отсеять — во втором случае человек видит лишнее, в первом не видит нужного."""
    import loinc_match
    meta = {"CORD": _meta(system="rbc^bldco")}
    assert loinc_match.impossible_for("CORD", meta, {"adult": None}) is None
    assert loinc_match.impossible_for("CORD", meta, {}) is None


@pytest.mark.parametrize("mark", [
    "2h post 75 g glucose po",   # нагрузочная проба
    "1h post meal",              # постпрандиальная
    "12.00 specimen",            # суточный профиль — НЕ содержит «post»
    "8 am specimen",
    "post dialysis",
])
def test_any_draw_condition_is_impossible_when_labs_are_fasting(mark):
    """Правило написано на НАЛИЧИИ метки `^`, а не на слове «post». Первая
    редакция ловила только post/pre — и двенадцать «Глюкоза^N.00 образец»
    (суточный гликемический профиль) доехали до листа; увидено глазами в выдаче
    2026-07-29."""
    import loinc_match
    meta = {"X": _meta(component=f"glucose^{mark}"), "PLAIN": _meta(component="glucose")}
    assert loinc_match.impossible_for("X", meta, {"fasting_labs": True})
    assert loinc_match.impossible_for("PLAIN", meta, {"fasting_labs": True}) is None


def test_the_fasting_variant_itself_survives_the_fasting_filter():
    """Граница правила: «натощак» в LOINC — это ТОЖЕ метка после `^` (`CFst`,
    carbohydrate fast). Отсев по одному лишь `^` выбросил бы ровно тот вариант,
    ради которого факт и сообщён."""
    import loinc_match
    meta = {"A": _meta(component="glucose^post cfst"),
            "B": _meta(component="glucose^post 12h cfst")}
    assert loinc_match.impossible_for("A", meta, {"fasting_labs": True}) is None
    assert loinc_match.impossible_for("B", meta, {"fasting_labs": True}) is None


def test_unknown_name_yields_nothing(index):
    meta, by_name = index
    import loinc_match
    assert loinc_match.candidates("НетТакого", "mg/dL", meta, by_name) == set()


# ── провенанс ──

def test_record_refuses_without_provenance(tmp_path, monkeypatch):
    """Соответствие без провенанса неотличимо от догадки. Отказ громкий."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    import loinc_match
    with pytest.raises(ValueError):
        loinc_match.record_mapping("HGB", "g/dL", "718-7", "")


def test_unit_spelling_does_not_split_the_mapping(tmp_path, monkeypatch):
    """Ключ отображения — имя и НОРМАЛИЗОВАННАЯ единица. `pg/ml`, `pg/mL` и
    `пг/мл` — одна единица; три строки об одном и том же означали бы, что
    документ с четвёртым написанием не найдёт ни одной. Найдено по решениям
    владельца 2026-07-29: он трижды ответил одно и то же на один вопрос."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    import loinc_match
    for u in ("pg/ml", "pg/mL", "пг/мл"):
        loinc_match.record_mapping("Vitamin_B12", u, "16695-9", "owner")
    with health_db.get_conn() as c:
        rows = c.execute("SELECT unit FROM lab_name_loinc "
                         "WHERE our_name='Vitamin_B12'").fetchall()
    assert len(rows) == 1, f"три написания одной единицы завели {len(rows)} строк"


def test_specimen_splits_the_key(tmp_path, monkeypatch):
    """`Калий, мг/л` приезжает и из биохимии, и из мочи — с разными референсами
    и порядками значений (синтетика той же формы). Без материала в ключе одно соответствие подшило бы
    мочу к сыворотке — молча, в тренд. Материал берётся из панели через
    lab_promote.specimen_of, второго дома правила не заводится."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    import lab_promote
    import loinc_match
    assert lab_promote.specimen_of({"panel": "chemistry"}) == "blood"
    assert lab_promote.specimen_of({"panel": "urine"}) == "urine"
    loinc_match.record_mapping("Potassium", "мг/л", "2823-3", "owner", specimen="blood")
    loinc_match.record_mapping("Potassium", "мг/л", "2828-2", "owner", specimen="urine")
    with health_db.get_conn() as c:
        rows = dict(c.execute("SELECT specimen, loinc_num FROM lab_name_loinc "
                              "WHERE our_name='Potassium'").fetchall())
    assert rows == {"blood": "2823-3", "urine": "2828-2"}


def test_record_stores_who_decided(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    import loinc_match
    loinc_match.record_mapping("HGB", "g/dL", "718-7", "owner", rule=None)
    with health_db.get_conn() as c:
        row = c.execute("SELECT loinc_num, decided_by FROM lab_name_loinc "
                        "WHERE our_name='HGB'").fetchone()
    assert tuple(row) == ("718-7", "owner")

# ── провенанс решения меняет корзину ──

@pytest.fixture
def canon(tmp_path, monkeypatch):
    """Минимальный канон + справочник: две решённые пары, разный провенанс."""
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
    rc.execute("CREATE TABLE loinc_synonyms (loinc_num TEXT, synonym TEXT, source TEXT)")
    rc.execute("CREATE TABLE loinc_ru (loinc_num TEXT PRIMARY KEY, component TEXT, "
               "system TEXT, property TEXT, method TEXT)")
    rc.executemany("INSERT INTO loinc_terms(loinc_num, component, property, system, "
                   "long_common_name, loinc_version) VALUES (?,?,?,?,?,'2.82')",
                   [("718-7", "Hemoglobin", "MCnc", "Bld", "Hemoglobin in Blood"),
                    ("2885-2", "Protein", "MCnc", "Ser/Plas", "Protein in Serum")])
    rc.commit(); rc.close()
    health_db.init_db()
    health_db._ensure_lab_table()
    with health_db.get_conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS lab_name_loinc (our_name TEXT NOT NULL, "
                  "unit TEXT, specimen TEXT NOT NULL DEFAULT 'blood', loinc_num TEXT NOT NULL, "
                  "decided_by TEXT NOT NULL, rule TEXT, decided_at TEXT, "
                  "PRIMARY KEY (our_name, unit, specimen))")
        c.executemany("INSERT INTO lab_name_loinc(our_name,unit,specimen,loinc_num,"
                      "decided_by,rule) VALUES (?,?,?,?,?,?)",
                      [("HGB", "g/dl", "blood", "718-7", "owner", None),
                       ("Total_Protein", "g/dl", "blood", "2885-2", "agent_from_blank",
                        "бланк «Protein, total-B» реф 6.4-8.3 g/dL")])
        c.executemany("INSERT INTO lab_results_staging(run_id,extractor_version,"
                      "source_file,date,raw_name,canonical_name,unit,panel,value) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      [("r1", "v1", "a.pdf", "2026-01-01", "Hemoglobin", "HGB",
                        "g/dL", "cbc", 15.0),
                       ("r1", "v1", "a.pdf", "2026-01-01", "Protein, total-B",
                        "Total_Protein", "g/dL", "chemistry", 7.0)])
        c.commit()
    return health_db


def test_agent_decision_goes_to_audit_not_to_decided(canon):
    """Решение агента НЕ смеет попасть в «уже решено»: там лежат вердикты владельца,
    и смешивать их значит выдавать догадку за его слово. Улика едет вместе с
    решением — без неё проверить нечего."""
    import loinc_match
    res = loinc_match.propose_mappings()
    audit = {(d["name"], d["unit"]): d for d in res["audit"]}
    decided = {(d["name"], d["unit"]) for d in res["decided"]}
    assert ("Total_Protein", "g/dl") in audit, "решение агента не предъявлено на проверку"
    assert ("Total_Protein", "g/dl") not in decided
    assert ("HGB", "g/dl") in decided, "вердикт владельца не спрашивают второй раз"
    assert "6.4-8.3" in (audit[("Total_Protein", "g/dl")]["rule"] or ""), "улика потеряна"


def test_answered_pair_is_not_asked_again_in_another_unit(canon):
    """Один вопрос не задаётся дважды в разных единицах.

    Случай 2026-07-30: лист трижды спрашивал про «Калий, мг/л», предлагая
    коды в ммоль/л, и владелец справедливо отвечал «не та размерность» — при том
    что пара (Potassium, ммоль/л) была решена. Ключ пары обязан строиться в
    КОНВЕНЦИОНАЛЬНОЙ единице, как и у читателя тренда.

    Цена ошибки здесь — не лишний клик, а доверие к листу: человек, которому
    третий раз показывают заведомо неотвечаемый вопрос, перестаёт верить
    остальным.
    """
    import loinc_match
    with canon.get_conn() as c:
        c.execute("INSERT INTO lab_name_loinc(our_name,unit,specimen,loinc_num,"
                  "decided_by) VALUES ('Potassium','mmol/l','blood','6298-4','owner')")
        c.execute("INSERT INTO lab_results_staging(run_id,extractor_version,source_file,"
                  "date,raw_name,canonical_name,unit,panel,value) "
                  "VALUES ('r1','v1','a.pdf','2026-01-01','Калий, K','Potassium',"
                  "'мг/л','chemistry',160.0)")
        c.commit()
    res = loinc_match.propose_mappings()
    asked = {(d["name"], d["unit"]) for d in res["choices"]} | \
            {(d["name"], d["unit"]) for d in res["missing"]}
    assert not [k for k in asked if k[0] == "Potassium"], (
        f"калий снова спрашивают: {[k for k in asked if k[0] == 'Potassium']}")
    dec = {d["name"]: d for d in res["decided"]}
    assert "Potassium" in dec, "пара должна считаться решённой"
    assert dec["Potassium"]["unit"] == "mmol/l", "ключ пары — конвенциональная единица"
    assert "мг/л" in dec["Potassium"]["raw_units"], "сырое написание не потеряно"


# ── пустое состояние листа ──

def test_worksheet_says_when_there_is_nothing_to_decide():
    """Пустая страница — это отказ инструмента, а не «работа кончилась».
    2026-07-30 владелец ответил на все вопросы, открыл лист и увидел ПУСТОТУ:
    заголовок «Выбери код (0)» и четыре свёрнутых раздела. Ни ошибки, ни
    объяснения — по виду сломанный файл."""
    import loinc_match
    st = loinc_match.worksheet_state(
        {"choices": [], "audit": [], "missing": [{"name": "MPV"}],
         "decided": [1, 2], "auto": [1]})
    assert st["headline"] == "Выбирать нечего"
    assert "MPV" not in st["hint"], "подсказка про СОСТОЯНИЕ, а не список имён"
    assert "1 имён" in st["hint"] or "1 имен" in st["hint"], "остаток назван числом"
    assert st["open_missing"] is True, "остаток обязан быть РАСКРЫТ, а не спрятан"


def test_worksheet_headline_counts_choices_when_there_is_work():
    import loinc_match
    st = loinc_match.worksheet_state(
        {"choices": [1, 2, 3], "audit": [], "missing": [], "decided": [], "auto": []})
    assert st["headline"] == "Выбери код (3)"
    assert st["hint"] == "" and st["open_missing"] is False


def test_pending_audit_alone_is_still_work():
    """Проверять мои решения — тоже работа: пустым лист называться не смеет."""
    import loinc_match
    st = loinc_match.worksheet_state(
        {"choices": [], "audit": [{"name": "HGB"}], "missing": [], "decided": [],
         "auto": []})
    assert st["headline"] != "Выбирать нечего"


# ── Вес пары: различимые измерения, а не строки таблицы (2026-07-30) ──

def _row(raw, canon, unit, panel="chemistry", src="doc.pdf", date="2024-01-01"):
    return (raw, canon, unit, panel, src, date)


def test_вес_считает_измерения_а_не_переразборы():
    """Повторные разборы одного документа не увеличивают вес измерения.
    Синтетические копии ниже должны считаться одной наблюдаемой точкой."""
    import loinc_match as LM
    rows = [_row("Glucose", "Glucose", "mg/dL")] * 5      # пять прогонов одного разбора
    w, _u = LM._measurement_weight(rows)
    assert sum(w.values()) == 1, "пять копий одного измерения — это одно измерение"


def test_разные_документы_и_даты_считаются_отдельно():
    """Граница с другой стороны: дедуп не имеет права схлопнуть РАЗНЫЕ заборы,
    иначе вес перестанет отражать, как часто аналит вообще сдают."""
    import loinc_match as LM
    rows = [_row("Glucose", "Glucose", "mg/dL", src="a.pdf", date="2024-01-01"),
            _row("Glucose", "Glucose", "mg/dL", src="b.pdf", date="2024-01-01"),
            _row("Glucose", "Glucose", "mg/dL", src="a.pdf", date="2024-06-01")]
    w, _u = LM._measurement_weight(rows)
    assert sum(w.values()) == 3


def test_написания_единицы_копятся_по_всем_строкам():
    """Написания — предмет отдельного вопроса («одна единица или разные»), поэтому
    собираются по ВСЕМ строкам, даже если измерение засчитано один раз."""
    import loinc_match as LM
    rows = [_row("Glucose", "Glucose", "mg/dL"), _row("Glucose", "Glucose", "мг/дл")]
    w, u = LM._measurement_weight(rows)
    key = next(iter(u))
    assert u[key] == {"mg/dL", "мг/дл"}


def test_у_безразмерного_единица_не_часть_ключа():
    """Бланк формата Synevo печатает `mg/dL` напротив `Atherogenic index`
    (формат бланка, референс вида «< N»). Распознаватель читает
    бланк верно — но лист не должен спрашивать владельца про единицу отношения:
    правильного ответа у такого вопроса нет."""
    import loinc_match as LM
    assert LM._conv_unit("Atherogenic_index", "mg/dL") == ""
    assert LM._conv_unit("Chol_HDL_ratio", "") == ""
    # Граница: у РАЗМЕРНОГО аналита единица обязана остаться в ключе, иначе
    # мг/дл и ммоль/л слились бы в одну пару и вопрос потерял бы смысл.
    assert LM._conv_unit("Glucose", "mg/dL") != ""
    assert LM._conv_unit("HGB", "g/L") == LM._conv_unit("HGB", "g/dL")
