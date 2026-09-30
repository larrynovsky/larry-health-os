"""Единицы СИ→conventional и маршрутизация материала (lab_canon, lab_promote).

Закрывает дыру the-end 2026-07-01: to_conventional ТРАНСФОРМИРУЕТ медзначения
(HGB 135 g/L→13.5) — без теста молча искажает канон. _specimen маршрутизирует
серологию (immunoreactivity→кровь). Обе были задеплоены без единого теста.
"""
import pytest

import lab_canon
import lab_promote


# ── to_conventional: реальные конверсии из _TO_CONVENTIONAL ──

@pytest.mark.parametrize("canon,value,unit,exp_v,exp_u", [
    ("HGB", 135, "g/L", 13.5, "g/dL"),          # риск 1.5 (синтетика)
    ("HGB", 135, "г/л", 13.5, "g/dL"),          # кириллическая единица
    ("MCHC", 330, "g/L", 33.0, "g/dL"),
    ("Glucose", 5.5, "mmol/L", round(5.5 * 18.016, 4), "mg/dL"),
    ("Creatinine", 88.4, "umol/L", 1.0, "mg/dL"),
    ("Creatinine", 88.4, "µmol/L", 1.0, "mg/dL"),  # µ→u нормализация
    ("Uric_acid", 59.48, "umol/L", 1.0, "mg/dL"),
    ("Cholesterol_Total", 5.0, "ммоль/л", round(5.0 * 38.67, 4), "mg/dL"),
    ("Urea", 5.0, "ммоль/л", round(5.0 * 6.006, 4), "mg/dL"),  # мочевина СИ: 5.0→30, не ложный флаг <5
    # Клинический якорь R-2 (2026-07-30): строка магния без единицы (синтетика той же формы).
    # 0.90 ммоль/л = 2.19 мг/дл — НОРМА; приписать ей мг/дл значило бы объявить дефицит.
    ("Magnesium", 0.90, "ммоль/л", round(0.90 * 2.4305, 4), "mg/dL"),
    ("Transferrin", 2.5, "г/л", 250.0, "mg/dL"),   # 2.5 г/л = 250 мг/дл, референс 200–360
])
def test_to_conventional_converts(canon, value, unit, exp_v, exp_u):
    v, u = lab_canon.to_conventional(canon, value, unit)
    assert v == exp_v
    assert u == exp_u


def test_already_conventional_unchanged():
    # g/dL НЕ должен матчиться на правило "g/l" (g/l не подстрока g/dl)
    assert lab_canon.to_conventional("HGB", 12.7, "g/dL") == (12.7, "g/dL")


def test_unknown_analyte_unchanged():
    assert lab_canon.to_conventional("TSH", 2.5, "mIU/L") == (2.5, "mIU/L")


@pytest.mark.parametrize("value,unit", [(None, "g/L"), (5.0, ""), (5.0, None)])
def test_missing_inputs_unchanged(value, unit):
    assert lab_canon.to_conventional("HGB", value, unit) == (value, unit)


def test_nonnumeric_value_unchanged():
    assert lab_canon.to_conventional("HGB", "н/д", "g/L") == ("н/д", "g/L")


# ── _norm_unit: кириллица/латиница к канону ──

@pytest.mark.parametrize("raw,norm_sub", [
    ("г/л", "g/l"), ("ммоль/л", "mmol/l"), ("мкмоль/л", "umol/l"),
    ("µmol/L", "umol/l"), ("gr/l", "g/l"), ("МГ/ДЛ", "mg/dl"),
    # Письменность единицы не должна создавать новую шкалу.
    ("сек.", "sec"), ("СЕК", "sec"), ("мМЕ/л", "miu/l"),
])
def test_norm_unit(raw, norm_sub):
    assert norm_sub in lab_canon._norm_unit(raw)


# ── Транслитерация: одна единица, две письменности → ОДИН ключ ──
# Пара, записанная разными алфавитами, должна давать один ключ.
# Пара, а не подстрока: тест обязан покраснеть при откате карты, а подстрочная
# проверка «g/l in ...» зеленеет и без транслитерации соседнего написания.

@pytest.mark.parametrize("cyr,lat", [
    ("Ед/л", "U/L"),          # ALP/ALT/AST/Amylase/CPK/GGT/LDH — семь имён
    ("Ед/мл", "U/mL"),        # CA19-9
    ("мм/ч", "mm/hr"),        # ESR
    ("пг", "pg"),             # MCH
    ("фл", "fL"),             # MCV, MPV
    ("пмоль/л", "pmol/L"),    # T3_free, T4_free
    ("пг/мл", "pg/mL"),       # NT_proBNP
    ("мкМЕ/мл", "uIU/mL"),    # TSH
    ("нмоль/л", "nmol/L"),
    ("10^12/л", "10^12/L"),   # RBC
    ("мМЕ/мл", "mIU/ml"),     # эквивалентные записи единицы
    ("мм/ч", "mm/h"),         # ESR: «mm/h» и «mm/hr» — одна величина
    ("мкмоль/л", "mkmol/l"),  # креатинин: транслит вместо µ
])
def test_cyrillic_and_latin_converge(cyr, lat):
    assert lab_canon._norm_unit(cyr) == lab_canon._norm_unit(lat)


def test_worklist_2026_08_31_converges():
    """Сконструированные пары единиц сходятся к одной цели."""
    by = {
        "Aldosterone": {"пг/мл", "нг/мл"},
        "TIBC": {"мкмоль/л", "µg/dL"},
        "Testosterone": {"нмоль/л", "ng/dl"},
        "Methylmalonic_acid": {"µg/l", "nmol/L"},
        "ESR": {"мм/ч", "mm/hr", "mm/h"},
        "FSH": {"мМЕ/мл", "mIU/ml"},
    }
    assert lab_canon.unit_convergence_gaps(by) == []
    # Магнитуда, не косметика: 50.0 µmol/L железосвязывающей ≈ 279 µg/dL (ref 251–419).
    v, u = lab_canon.to_conventional("Aldosterone", 50.0, "пг/мл")
    assert (round(v, 2), u) == (5.0, "ng/dL")
    v, u = lab_canon.to_conventional("Aldosterone", 0.12, "нг/мл")
    assert (round(v, 2), u) == (12.0, "ng/dL")
    v, u = lab_canon.to_conventional("TIBC", 50.0, "мкмоль/л")
    assert (round(v), u) == (279, "ug/dL")
    v, u = lab_canon.to_conventional("Testosterone", 15.0, "нмоль/л")
    assert (round(v), u) == (433, "ng/dL")
    v, u = lab_canon.to_conventional("Methylmalonic_acid", 20.0, "µg/l")
    assert (round(v), u) == (169, "nmol/L")
    # Free/bioavailable — свои каноны, правило общего их не трогает.
    assert lab_canon.to_conventional("Testosterone_free", 250.0, "пмоль/л") == (250.0, "пмоль/л")


def test_nrbc_percent_and_count_are_two_names():
    """NRBC «%» и «10⁹/л» — две величины (идентичность по размерности, ADR 2026-08-12),
    а не несходимость единиц. Датчик обязан группировать по уточнённому имени."""
    assert lab_canon.dimension_key("NRBC", "%") == "NRBC_pct"
    assert lab_canon.dimension_key("NRBC", "10^9/л") == "NRBC_abs"
    assert {"NRBC_pct", "NRBC_abs"} <= lab_canon.CANONICALS
    by = {}
    for unit in ("%", "10^9/л"):
        by.setdefault(lab_canon.dimension_key("NRBC", unit), set()).add(unit)
    assert lab_canon.unit_convergence_gaps(by) == []


def test_rbc_notations_are_one_magnitude():
    """10^12/L ≡ 10^6/µL — та же величина. RBC печатается обеими."""
    assert lab_canon._norm_unit("10^12/L") == lab_canon._norm_unit("10^6/µL")


def test_cells_per_ul_stays_apart():
    """Граница транслитерации: «кл/мкл» отличается от 10^3/µL МАГНИТУДОЙ.
    Сведение их в _norm_unit скрыло бы расхождение в тысячу раз — это работа
    правила конверсии, а не карты письменностей. Тест держит границу."""
    assert lab_canon._norm_unit("кл/мкл") != lab_canon._norm_unit("10^3/µL")


def test_longer_pattern_wins_over_shorter():
    """Порядок в карте значим: «пг/мл» обязано сработать раньше «пг»,
    иначе получится «pg/мл» — молчаливый мусор, а не единица."""
    assert lab_canon._norm_unit("пг/мл") == "pg/ml"
    assert "мл" not in lab_canon._norm_unit("пг/мл")


# ── _specimen: маршрутизация материала (серология в кровь) ──

@pytest.mark.parametrize("row,expected", [
    ({"panel": "immunoreactivity"}, "blood"),   # серология Helicobacter IgG, Anti-Hu
    ({"panel": "cbc"}, "blood"),
    ({"panel": "tumor_markers"}, "blood"),
    ({"panel": "urine"}, "urine"),
    ({"panel": "microbiome"}, "stool"),
    ({"panel": "", "canonical_name": "Urine_pH"}, "urine"),  # имя → urine
    ({"panel": "неизвестно"}, "other"),
    ({"panel": None}, "other"),
])
def test_specimen_routing(row, expected):
    assert lab_promote._specimen(row) == expected


# ── Электролиты/минералы мг/л, мкг/л (BL-LAB-CANON-1: панель в мг/л) ──

import lab_oracles as _lo


@pytest.mark.parametrize("canon,value,unit,exp_v,exp_u", [
    ("Sodium", 3250.0, "mg/L", round(3250.0 / 22.99, 4), "mmol/L"),    # = 141.4 ммоль/л
    ("Potassium", 160.0, "mg/L", round(160.0 / 39.10, 4), "mmol/L"),  # = 4.09
    ("Chloride", 3600.0, "mg/L", round(3600.0 / 35.45, 4), "mmol/L"),
    ("Calcium", 95.0, "mg/L", round(95.0 * 0.1, 4), "mg/dL"),       # = 9.5
    ("Magnesium", 22.0, "mg/L", round(22.0 * 0.1, 4), "mg/dL"),       # = 2.2
    ("Iron", 800.0, "ug/L", round(800.0 * 0.1, 4), "ug/dL"),        # = 80.0
    ("Sodium", 3250.0, "мг/л", round(3250.0 / 22.99, 4), "mmol/L"),     # кириллица
    ("Iron", 800.0, "мкг/л", round(800.0 * 0.1, 4), "ug/dL"),
])
def test_electrolyte_mgL_conversion(canon, value, unit, exp_v, exp_u):
    assert lab_canon.to_conventional(canon, value, unit) == (exp_v, exp_u)


def test_clean_electrolytes_pass_bounds_after_conversion():
    # синтетика той же формы: чистые значения в мг/л → в границах после конверсии
    for canon, value, unit in [("Sodium", 3250.0, "mg/L"), ("Potassium", 160.0, "mg/L"),
                               ("Calcium", 95.0, "mg/L"), ("Iron", 800.0, "ug/L")]:
        assert not _lo._o_bounds({"canonical_name": canon, "value": value, "unit": unit})


def test_garbage_electrolytes_still_flagged_after_conversion():
    # настоящий брак распознавания остаётся вне границ даже после конверсии → ревью
    assert _lo._o_bounds({"canonical_name": "Potassium", "value": 1500.0, "unit": "mg/L"})  # →38.4
    assert _lo._o_bounds({"canonical_name": "Phosphorus", "value": 250.0, "unit": "mg/L"})    # →25



def test_tsh_notation_uU_equals_uIU():
    # µU/mL ≡ µIU/mL (для TSH/инсулина «I» в IU опускают) — унификация нотации,
    # не конверсия значения. worklist канонизации не должен ругаться на TSH.
    assert lab_canon._norm_unit("µU/mL") == lab_canon._norm_unit("uIU/mL")
    assert lab_canon.unit_convergence_gaps({"TSH": {"uIU/mL", "µU/mL"}}) == []


def test_mma_serum_vs_urine_NOT_merged():
    # Methylmalonic_acid: µg/l (сыворотка) vs ммоль/моль креатинина (моча) — РАЗНЫЕ
    # измерения/материал, НЕ единицы одного аналита. Слить = тихая мед-ошибка
    # (серум+урина на одном тренде). worklist ОБЯЗАН флагать — это не баг, а вердикт.
    gaps = lab_canon.unit_convergence_gaps(
        {"Methylmalonic_acid": {"µg/l", "ммоль/моль креатинина"}})
    assert gaps and "Methylmalonic_acid" in gaps[0]



def test_specimen_key_routes_mma_by_unit():
    # MMA — легитимно двухматериальный: материал выводится из единицы
    assert lab_canon.specimen_key("Methylmalonic_acid", "µg/l") == "Methylmalonic_acid_serum"
    assert lab_canon.specimen_key("Methylmalonic_acid", "ммоль/моль креатинина") == "Methylmalonic_acid_urine"
    assert lab_canon.specimen_key("Methylmalonic_acid", "mmol/mol creatinine") == "Methylmalonic_acid_urine"


def test_specimen_key_leaves_single_specimen_untouched():
    assert lab_canon.specimen_key("HGB", "g/l") == "HGB"
    assert lab_canon.specimen_key("TSH", "uIU/mL") == "TSH"


def test_worklist_clears_mma_after_specimen_split():
    # Материал разделяет разные величины: после specimen_key каждый ряд
    # имеет одну единицу. Без разделения расхождение должно сохраняться.
    by = {}
    for tn, u in [("Methylmalonic_acid", "µg/l"), ("Methylmalonic_acid", "ммоль/моль креатинина")]:
        k = lab_canon.specimen_key(lab_canon.normalize(tn), u)
        by.setdefault(k, set()).add(u)
    assert lab_canon.unit_convergence_gaps(by) == []


def test_vitamin_d_nmol_becomes_ng_per_ml():
    """Пересчёт использует молекулярную массу: 1 нг/мл = 2.496 нмоль/л.

    Проверяется не только число, но и КЛИНИЧЕСКИЙ СМЫСЛ пересчёта: 60.0 нмоль/л —
    это ~24.0 нг/мл, то есть НИЖЕ порога достаточности 30 нг/мл. Ошибись
    коэффициент в 2.5 раза (типичная ошибка «перевернул дробь») — значение стало
    бы ~150 нг/мл, и недостаток витамина D читался бы как избыток.
    """
    import lab_canon
    v, u = lab_canon.to_conventional("Vitamin_D", 60.0, "nmol/L")
    assert u == "ng/mL"
    assert 23.5 < v < 24.5, f"60.0 нмоль/л ≈ 24.0 нг/мл, получено {v}"
    assert v < 30.0, "ниже порога достаточности — пересчёт не смеет это скрыть"
    # кириллица идёт тем же путём
    assert lab_canon.to_conventional("Vitamin_D", 60.0, "нмоль/л")[1] == "ng/mL"


def test_vitamin_b12_pmol_becomes_pg_per_ml():
    """Правило добавлено 2026-07-29, ПОСЛЕ того как из-под имени `Vitamin_B12`
    вывели активный B12 (голотранскобаламин): до разделения пересчёт склеил бы
    два разных анализа. 1 пг/мл = 0.738 пмоль/л (MW 1355).

    Якорь выбран клинический, а не круглый: 148 пмоль/л — классический порог
    дефицита, и в массовых единицах это ровно ~200 пг/мл, тоже порог. Перевёрнутая
    дробь дала бы 109 пг/мл, то есть «глубокий дефицит» на границе нормы —
    именно та ошибка, которую тест обязан поймать (мутация прогнана, краснеет).
    """
    import lab_canon
    v, u = lab_canon.to_conventional("Vitamin_B12", 148.0, "pmol/L")
    assert u == "pg/mL"
    assert 198.0 < v < 202.0, f"148 пмоль/л ≈ 200 пг/мл, получено {v}"
    assert lab_canon.to_conventional("Vitamin_B12", 148.0, "пмоль/л")[1] == "pg/mL"
    # ГРАНИЦА: активный B12 — другое имя, и правила у него нет; иначе 90.0 пмоль/л
    # голотранскобаламина превратились бы в ~122 «пг/мл общего B12».
    assert lab_canon.to_conventional("Holotranscobalamin", 90.0, "pmol/L") == (90.0, "pmol/L")


# ── Границы правдоподобия у нового имени (шаг E нити loinc-name-home) ──

def test_bun_имеет_границы_правдоподобия():
    """Отдельное каноническое имя должно иметь свои границы правдоподобия."""
    import lab_oracles
    assert "BUN" in lab_oracles._HARD
    assert lab_oracles._o_bounds({"canonical_name": "BUN", "value": 15, "unit": "mg/dL"}) == []
    # Мочевина, ошибочно подписанная как BUN: 280 мг/дл невозможно для азота мочевины.
    assert lab_oracles._o_bounds({"canonical_name": "BUN", "value": 280, "unit": "mg/dL"})


def test_bun_наследует_каденцию_мочевины():
    """BUN и Urea — одна величина в двух формах. Аналит, дорастающий до порога
    автодобавления в расписание, обязан получить каденцию мочевины, а не
    консервативный CATEGORY_DEFAULT: тихая разница 180 против 120 дней."""
    import lab_freshness_twotier as tt
    assert tt.stable_for("BUN") == tt.stable_for("Urea") == 120
    assert tt.stable_for("BUN") != tt.CATEGORY_DEFAULT


def test_материал_берётся_из_колонки_а_не_из_единицы(tmp_path, monkeypatch):
    """Шаг 8: у материала ОДИН дом — колонка lab_results.specimen. Раньше их было
    два, с разными словарями: колонка говорила `blood`, имя — `serum`.

    Здесь же второй оракул, важнее первого: читатель трендов обязан дедуплицировать
    ТЕМ ЖЕ ключом, что канон. Без метода в ключе он молча выбрасывал бы второе
    измерение того же дня — тестостерон иммуноанализом при уже взятом
    масс-спектрометрией."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "h"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "h" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "h" / "data" / "health.db")
    health_db.init_db()
    import labs_db
    with health_db.get_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS lab_results(
            id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, source TEXT,
            test_name TEXT, value REAL, unit TEXT, ref_low REAL, ref_high REAL,
            status TEXT, specimen TEXT, method TEXT)""")
        c.executemany(
            "INSERT INTO lab_results(date,test_name,value,unit,specimen,method,source)"
            " VALUES(?,?,?,?,?,?,?)", [
                ("2021-02-15", "Testosterone", 12.0, "нмоль/л", "blood",
                 "Стероидный профиль (ЖХ-МС/МС)", "doc:b.pdf"),
                ("2021-02-15", "Testosterone", 15.0, "нмоль/л", "blood",
                 "Андрогенный статус", "doc:b.pdf"),
                ("2021-02-15", "Methylmalonic_acid", 20.0, "µg/l", "blood", None, "doc:b.pdf"),
                ("2021-02-15", "Methylmalonic_acid", 0.0, "ммоль/моль креатинина",
                 "urine", None, "doc:b.pdf"),
            ])
        c.commit()
    txt = labs_db.build_lab_history_context(min_points=1)
    # оба прибора дошли до потребителя, ни один не потерян дедупом. Ожидание —
    # через тот же to_conventional, что и читатель: с 2026-08-31 у тестостерона есть
    # правило нмоль/л→ng/dL, и литералы значений проверяли бы отсутствие
    # конверсии, а не присутствие обоих измерений.
    shown = [str(lab_canon.to_conventional("Testosterone", v, "нмоль/л")[0]) for v in (12.0, 15.0)]
    assert all(x in txt for x in shown), (shown, txt)
    # многоматериальный аналит разведён ПО КОЛОНКЕ, а не по единице
    assert "[urine]" in txt and "[blood]" in txt, txt
