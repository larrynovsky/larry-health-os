"""Характеризация загрузчика LOINC.

Фиксирует «верно» для модуля, который иначе нельзя безопасно переписать: что
грузится ПОДМНОЖЕСТВО (гранулярность — решение, а не деталь), что версия попадает
в каждую строку (без неё смена релиза протухает беззвучно) и что повторный запуск
не плодит дубли.

Отдельно закреплено, что код заказного набора, отсутствующий в ядре, СЧИТАЕТСЯ, а
не теряется молча: тихая потеря строки справочника — дыра, которую потом не найти.
"""
from __future__ import annotations

import csv

import pytest

pytestmark = pytest.mark.unit

CORE = [
    {"LOINC_NUM": "718-7", "COMPONENT": "Hemoglobin", "PROPERTY": "MCnc",
     "SYSTEM": "Bld", "SCALE_TYP": "Qn", "LONG_COMMON_NAME": "Hemoglobin [Mass/volume] in Blood",
     "SHORTNAME": "Hgb Bld-mCnc", "STATUS": "ACTIVE", "CLASSTYPE": "1"},
    {"LOINC_NUM": "2132-9", "COMPONENT": "Cobalamin", "PROPERTY": "MCnc",
     "SYSTEM": "Ser/Plas", "SCALE_TYP": "Qn", "LONG_COMMON_NAME": "Cobalamin [Mass/volume]",
     "SHORTNAME": "B12 SerPl-mCnc", "STATUS": "ACTIVE", "CLASSTYPE": "1"},
    {"LOINC_NUM": "16695-9", "COMPONENT": "Cobalamin", "PROPERTY": "SCnc",
     "SYSTEM": "Ser/Plas", "SCALE_TYP": "Qn", "LONG_COMMON_NAME": "Cobalamin [Moles/volume]",
     "SHORTNAME": "B12 SerPl-sCnc", "STATUS": "ACTIVE", "CLASSTYPE": "1"},
    {"LOINC_NUM": "100000-9", "COMPONENT": "Not a lab term", "PROPERTY": "Hx",
     "SYSTEM": "^Patient", "SCALE_TYP": "Nar", "LONG_COMMON_NAME": "анкета, не лаборатория",
     "SHORTNAME": "x", "STATUS": "ACTIVE", "CLASSTYPE": "2"},
    {"LOINC_NUM": "100001-7", "COMPONENT": "Lymphocytes/Leukocytes", "PROPERTY": "NFr",
     "SYSTEM": "Bld", "SCALE_TYP": "Qn", "LONG_COMMON_NAME": "устаревший код",
     "SHORTNAME": "x", "STATUS": "DEPRECATED", "CLASSTYPE": "1"},
]
# 16695-9 и 100001-7 НАМЕРЕННО вне заказного набора. Первый — действующий
# лабораторный вариант того же Cobalamin: он обязан доехать, потому что «заказной
# набор» — список заказываемых ТЕСТОВ, а не веществ, и лейкоформулы в нём нет вовсе.
# Второй — DEPRECATED: он обязан НЕ доехать.
# 99999-0 — есть в наборе, нет в ядре: проверяет, что потеря считается, а не молчит.
ORDERS = ["718-7", "2132-9", "99999-0"]


def test_full_table_is_preferred_over_core(tmp_path, db, ref):
    """Синонимы RELATEDNAMES2/CONSUMER_NAME есть ТОЛЬКО в полной Loinc.csv.
    Загрузка из ядра дала бы en:related = 0 — индекс сопоставителя оказался бы
    слабее того, на котором мерился потолок. Поймано проверкой РЕЗУЛЬТАТА."""
    import csv as _csv
    import loinc_loader
    rows = [dict(CORE[0], RELATEDNAMES2="Hgb;Haemoglobin", CONSUMER_NAME="гемоглобин")]
    with (tmp_path / "Loinc.csv").open("w", encoding="utf-8", newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with (tmp_path / "LoincUniversalLabOrdersValueSet.csv").open(
            "w", encoding="utf-8", newline="") as fh:
        fh.write("LOINC_NUM,LONG_COMMON_NAME,ORDER_OBS\n718-7,x,Both\n")
    loinc_loader.load_loinc(tmp_path, "2.82")
    with ref() as c:
        srcs = {r[0] for r in c.execute("SELECT DISTINCT source FROM loinc_synonyms")}
    assert "en:related" in srcs, "официальные синонимы не загружены"


@pytest.fixture()
def csv_dir(tmp_path):
    with (tmp_path / "LoincTableCore.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(CORE[0]))
        w.writeheader()
        w.writerows(CORE)
    with (tmp_path / "LoincUniversalLabOrdersValueSet.csv").open("w", encoding="utf-8", newline="") as fh:
        fh.write("LOINC_NUM,LONG_COMMON_NAME,ORDER_OBS\n")
        for c in ORDERS:
            fh.write(f"{c},x,Both\n")
    return tmp_path


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    # Справочник — ОТДЕЛЬНЫЙ общий файл. В тесте он тоже обязан быть отдельным и
    # временным: иначе прогон писал бы в реальный ~/health_reference.
    monkeypatch.setattr(health_db, "REFERENCE_DIR", tmp_path / "ref")
    monkeypatch.setattr(health_db, "LOINC_DB_PATH", tmp_path / "ref" / "loinc.db")
    health_db.init_db()
    return health_db


@pytest.fixture()
def ref(db):
    """Соединение с ПОДКЛЮЧЁННЫМ справочником — иначе таблиц loinc_* не видно."""
    import contextlib

    @contextlib.contextmanager
    def _conn():
        with db.get_conn() as c:
            db.attach_reference(c)
            yield c
    return _conn


def test_all_active_lab_terms_are_loaded(csv_dir, db, ref):
    """Гранулярность (замер, дважды пересмотренный): дом имени — ВСЕ действующие
    лабораторные термины. Нелабораторный термин и снятый с употребления — за
    бортом; «заказывают его или нет» больше не решает ничего."""
    import loinc_loader
    res = loinc_loader.load_loinc(csv_dir, "2.82")
    with ref() as c:
        nums = {r[0] for r in c.execute("SELECT loinc_num FROM loinc_terms")}
    assert nums == {"718-7", "2132-9", "16695-9"}
    assert "100000-9" not in nums, "нелабораторный термин не должен тянуться"
    assert "100001-7" not in nums, "снятый с употребления код не должен тянуться"
    assert res["components"] == 2   # Hemoglobin + Cobalamin


def test_unordered_variant_of_an_ordered_substance_is_pulled_in(csv_dir, db, ref):
    """Сердце решения о гранулярности: `SCnc`-вариант вещества доезжает, даже
    если сам он НЕ в заказном наборе. Именно этого не хватало первой загрузке —
    у B12 в заказном наборе обе позиции `MCnc`, а pmol/L (`14685-2`) снаружи."""
    import loinc_loader
    loinc_loader.load_loinc(csv_dir, "2.82")
    with ref() as c:
        row = c.execute("SELECT property, in_universal_order FROM loinc_terms "
                        "WHERE loinc_num='16695-9'").fetchone()
    assert row[0] == "SCnc", "вариант по свойству измерения не загружен"
    assert row[1] == 0, "вариант помечен заказным, хотя его нет в наборе"


def test_ordered_rows_are_flagged(csv_dir, db, ref):
    """Различие «это заказывают» против «это вариант того же вещества» обязано
    сохраниться: слою сопоставления имён нужно знать, что предлагать первым."""
    import loinc_loader
    loinc_loader.load_loinc(csv_dir, "2.82")
    with ref() as c:
        flagged = {r[0] for r in c.execute(
            "SELECT loinc_num FROM loinc_terms WHERE in_universal_order=1")}
    assert flagged == {"718-7", "2132-9"}


def test_version_is_stamped_on_every_row(csv_dir, db, ref):
    """Без версии в строке смена релиза LOINC протухает беззвучно."""
    import loinc_loader
    loinc_loader.load_loinc(csv_dir, "2.82")
    with ref() as c:
        vs = {r[0] for r in c.execute("SELECT loinc_version FROM loinc_terms")}
    assert vs == {"2.82"}


def test_reload_is_idempotent_and_reversions(csv_dir, db, ref):
    """Повторный запуск не плодит дубли, а новая версия перештампует строки."""
    import loinc_loader
    loinc_loader.load_loinc(csv_dir, "2.82")
    loinc_loader.load_loinc(csv_dir, "2.83")
    with ref() as c:
        n = c.execute("SELECT COUNT(*) FROM loinc_terms").fetchone()[0]
        vs = {r[0] for r in c.execute("SELECT loinc_version FROM loinc_terms")}
    assert n == 3
    assert vs == {"2.83"}


def test_rows_from_a_previous_selection_rule_do_not_survive(csv_dir, db, ref):
    """Справочник заменяется целиком. Строка, попавшая по ПРЕЖНЕМУ правилу отбора,
    после смены правила обязана исчезнуть, а не жить молча: она уже не «то, что мы
    решили держать», но кандидатом сопоставителю предлагается наравне."""
    import loinc_loader
    loinc_loader.load_loinc(csv_dir, "2.82")
    with ref() as c:
        c.execute("INSERT INTO loinc_terms (loinc_num, component, loinc_version) "
                  "VALUES ('OLD-1','по старому правилу','2.81')")
        c.execute("INSERT INTO loinc_synonyms VALUES ('OLD-1','старое имя','en:component')")
        c.commit()
    loinc_loader.load_loinc(csv_dir, "2.82")
    with ref() as c:
        assert c.execute("SELECT COUNT(*) FROM loinc_terms "
                         "WHERE loinc_num='OLD-1'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM loinc_synonyms "
                         "WHERE loinc_num='OLD-1'").fetchone()[0] == 0


def test_ordered_code_absent_from_core_is_counted_not_swallowed(csv_dir, db, ref):
    """Тихая потеря строки справочника — дыра, которую потом не найти."""
    import loinc_loader
    res = loinc_loader.load_loinc(csv_dir, "2.82")
    assert res["universal"] == 3
    assert res["missing_in_core"] == 1


def test_attribution_document_exists():
    """Лицензия LOINC требует атрибуции; владелец проверил условия лично.
    Файл — носитель этого обязательства, и его исчезновение должно краснеть."""
    from pathlib import Path
    p = Path(__file__).resolve().parents[2] / "docs/reference/loinc_attribution.md"
    assert p.exists(), "нет docs/reference/loinc_attribution.md"
    assert "LOINC" in p.read_text(encoding="utf-8")


def test_ratio_numerator_is_indexed_as_a_name():
    """Проценты форменных элементов LOINC кодирует ОТДЕЛЬНЫМ компонентом со
    слэшем (`Neutrophils/Leukocytes`, NFr), а не свойством при том же компоненте.
    Аналит здесь — ЧИСЛИТЕЛЬ, и без отдельного ключа на него имя «Neutrophils»
    до этого кода не достаёт. Найдено взглядом в результат, не в замысел."""
    import loinc_loader
    keys = {k for _n, k, src in loinc_loader._synonyms_of(
        "770-8", {"COMPONENT": "Neutrophils/Leukocytes"})
        if src.endswith("numerator")}
    assert "neutrophils" in keys


def test_only_active_lab_terms_are_loaded():
    """Отбор больше НЕ опирается на заказной набор: `LoincUniversalLabOrders` —
    список заказываемых ТЕСТОВ, а ОАК заказывается одной панелью, поэтому его
    аналитов там нет вовсе (ни `lymphocyt`, ни `monocyt`). Дом имени структурно
    не вмещал лейкоформулу; замер: непокрытых пар 54 → 13."""
    import loinc_loader
    assert loinc_loader._is_lab_term({"CLASSTYPE": "1", "STATUS": "ACTIVE"})
    assert not loinc_loader._is_lab_term({"CLASSTYPE": "2", "STATUS": "ACTIVE"})
    assert not loinc_loader._is_lab_term({"CLASSTYPE": "1", "STATUS": "DEPRECATED"})
    assert not loinc_loader._is_lab_term({})
