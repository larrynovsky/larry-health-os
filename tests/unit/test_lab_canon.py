"""Unit-тесты нормализатора имён аналитов. Сконструированные варианты проверяют синонимы, разделение величин и сохранность неизвестных имён."""
import lab_canon as C


def test_english_synonyms():
    assert C.normalize("Hemoglobin") == "HGB"
    assert C.normalize("HGB") == "HGB"
    assert C.normalize("Cholesterol") == "Cholesterol_Total"
    assert C.normalize("Cholesterol, total-B") == "Cholesterol_Total"
    # BUN — НЕ мочевина: азот мочевины против молекулы, отношение 2.14, единица у
    # обеих mg/dL. До 2026-07-29 здесь стояло `== "Urea"`, и это была склейка,
    # невидимая для датчиков по единицам: бланки дают референс 5–25 против 16.6–48.5.
    assert C.normalize("BUN") == "BUN"
    assert C.normalize("BUN (Urea nitrogen) blood") == "BUN"
    assert C.normalize("UREA-B") == "Urea"
    assert C.normalize("FT4") == "T4_free"


def test_russian_synonyms():
    assert C.normalize("Железо") == "Iron"
    assert C.normalize("ЛПНП") == "LDL"
    assert C.normalize("Протромбин") == "PT"
    assert C.normalize("МНО") == "INR"
    assert C.normalize("АЧТВ") == "aPTT"


def test_format_variants_pct_abs():
    assert C.normalize("Neutrophils%") == "Neutrophils_pct"
    assert C.normalize("NEUTRO_pct") == "Neutrophils_pct"
    assert C.normalize("Monocytes") == "Monocytes_abs"   # bare = abs в старом каноне
    assert C.normalize("Protein_total") == "Total_Protein"
    assert C.normalize("Bilirubin") == "Bilirubin_total"


def test_parentheses_stripped():
    assert C.normalize("Тромбоциты (PLT)") == "PLT"
    assert C.normalize("Гемоглобин (Hb)") == "HGB"


def test_unknown_passthrough():
    # неизвестное имя не теряется, возвращается очищенным
    assert C.normalize("Holotranscobalamin") == "Holotranscobalamin"
    assert C.normalize("IgG1") == "IgG1"


def test_empty():
    assert C.normalize("") == ""
    assert C.normalize(None) is None


def test_percent_with_space_maps_to_pct():
    """РЕГРЕСС (ревью full2 2026-07-02): "Neutrophils %" (с пробелом) и
    "% Neutrophils" схлопывались в Neutrophils_abs через голый base → ложный
    конфликт с настоящим abs. Процентный вариант должен быть приоритетнее."""
    assert C.normalize("Neutrophils %") == "Neutrophils_pct"
    assert C.normalize("% Neutrophils") == "Neutrophils_pct"
    assert C.normalize("Lymphocytes %") == "Lymphocytes_pct"
    assert C.normalize("neutrophils%") == "Neutrophils_pct"   # без пробела не сломан
    assert C.normalize("Neutrophils_abs") == "Neutrophils_abs"  # bare abs не задет


def test_added_analytes_full2_review():
    """Аналиты, добавленные при ревью full2 — иначе Layer-2 блокировал промоут."""
    assert C.normalize("Albumin/Globulin-B") == "Albumin_Globulin_ratio"
    assert C.normalize("PT,sec") == "PT"
    assert C.normalize("PT-INR") == "INR"
    assert C.normalize("AFP") == "AFP"
    assert C.normalize("CA 72-4") == "CA72-4"
    assert C.normalize("Transferrin") == "Transferrin"


def test_blcanon2_hospital_panel_aliases():
    """Сконструированный набор вариантов регистра, пробелов и пояснений."""
    import lab_canon as lc
    cases = {
        " albumin (учебная форма) ": "Albumin",
        " FERRITIN ": "Ferritin",
        " Гемоглобин (учебная подпись) ": "HGB",
        " Железо (учебная подпись) ": "Iron",
        " FT4 ": "T4_free",
    }
    for raw, exp in cases.items():
        assert lc.normalize(raw) == exp, f"{raw} → {lc.normalize(raw)} != {exp}"


def test_ionized_calcium_separate_from_total():
    """Ca2+ (ионизированный) — отдельный аналит, не общий Calcium."""
    import lab_canon as lc
    assert lc.normalize("Кальций (Ca2+)") == "Calcium_ionized"
    assert lc.normalize("ионизированный кальций") == "Calcium_ionized"
    assert lc.normalize("Кальций") == "Calcium"  # общий не задет
    # конверсия ммоль/л→mg/dL и норма ионизированного (1.3 ммоль/л = 5.2 mg/dL)
    v, u = lc.to_conventional("Calcium_ionized", 1.3, "ммоль/л")
    assert round(v, 1) == 5.2 and u == "mg/dL"


def test_unit_convergence_gaps_flags_magnitude():
    # синтетический аналит БЕЗ правила: mg/l vs mg/dl — разные величины → находка (red-path).
    # robust: не привязан к реальному аналиту, который может получить правило (как CRP).
    gaps = C.unit_convergence_gaps({"ZZ_no_rule_analyte": {"mg/l", "mg/dl"}})
    assert len(gaps) == 1 and "ZZ_no_rule_analyte" in gaps[0]


def test_unit_convergence_gaps_silent_on_single_unit():
    assert C.unit_convergence_gaps({"Ferritin": {"ng/mL"}}) == []


def test_unit_convergence_gaps_silent_on_convertible():
    # Glucose имеет правило mmol/l→mg/dL → все сводятся к mg/dl → молчит
    assert C.unit_convergence_gaps({"Glucose": {"mmol/l", "mg/dl"}}) == []


def test_unit_convergence_gaps_silent_on_cyrillic_case():
    # кириллица/регистр схлопывается _norm_unit → молчит
    assert C.unit_convergence_gaps({"Sodium": {"mmol/L", "ммоль/л"}}) == []


def test_norm_unit_greek_mu_equals_micro():
    # µ (микро U+00B5) == μ (мю U+03BC)
    assert C._norm_unit("10^3/µL") == C._norm_unit("10^3/μL")


def test_norm_unit_collapses_cell_count_notations():
    canon = C._norm_unit("10^3/µL")
    assert C._norm_unit("10^9/L") == canon
    assert C._norm_unit("10^3/mm^3") == canon
    assert C._norm_unit("10e3/µL") == canon


def test_norm_unit_folate_cyrillic():
    assert C._norm_unit("нг/мл") == C._norm_unit("ng/ml")


def test_convergence_cleared_for_counts_and_folate():
    assert C.unit_convergence_gaps({"WBC": {"10^3/µL", "10^9/L", "10^3/mm^3", "10e3/µL"}}) == []
    assert C.unit_convergence_gaps({"Folate": {"ng/ml", "нг/мл"}}) == []


def test_to_conventional_crp_to_mgl():
    # CRP mg/dl → mg/L (там ref [0-5]); 0.13 mg/dL = 1.3 mg/L
    v, u = C.to_conventional("CRP", 0.13, "mg/dl")
    assert round(v, 2) == 1.3 and u == "mg/L"


def test_to_conventional_phosphorus_mmol():
    # Phosphorus mmol/l → mg/dL (MW 30.97); 1.13 mmol/L ≈ 3.5 mg/dL
    v, u = C.to_conventional("Phosphorus", 1.13, "mmol/l")
    assert round(v, 2) == 3.5 and u == "mg/dL"


def test_convergence_cleared_crp_phosphorus():
    assert C.unit_convergence_gaps({"CRP": {"mg/L", "mg/dL"}}) == []
    assert C.unit_convergence_gaps({"Phosphorus": {"mg/dL", "mmol/l"}}) == []


# ── Разведение имён 2026-07-30 (шаг D нити loinc-name-home) ──

def test_ка_и_отношение_хс_лпвп_разные_имена():
    """КА = (ОХ−ЛПВП)/ЛПВП, Chol/HDL = ОХ/ЛПВП — разница ровно 1.

    Склейка того же класса, что ox-LDL→LDL: оба числа правдоподобны, и в тренде
    подмена выглядит как улучшение липидного профиля на единицу.
    """
    assert C.normalize("коэффициент атерогенности") == "Atherogenic_index"
    assert C.normalize("индекс атерогенности") == "Atherogenic_index"
    assert C.normalize("CHOL/dHDLC") == "Chol_HDL_ratio"
    assert C.normalize("cholesterol/HDL") == "Chol_HDL_ratio"


def test_белок_мочи_канонизируется():
    """Каноническое имя должно быть достижимо через варианты словаря."""
    assert "Urine_Protein" in C.CANONICALS
    assert C.normalize("Белок") == "Urine_Protein"
    assert C.normalize("белок в моче") == "Urine_Protein"
    # Граница с ДРУГОЙ стороны: другие «белки» обязаны остаться на своих местах,
    # иначе голое «белок» стало бы воронкой. Проверяем ОБА направления, потому что
    # односторонний тест зеленеет и на правиле, которое всё утягивает в мочу.
    assert C.normalize("Общий белок") == "Total_Protein"
    assert C.normalize("С-реактивный белок (СРБ)") == "CRP"
    assert C.normalize("с-реактивный белок, высокочувствительный") == "CRP"


def test_срб_узнаётся_полным_написанием():
    """Промоут выводит имя из raw_name: полное имя и аббревиатура должны совпадать."""
    for spelling in ("С-реактивный белок", "с реактивный белок", "hs-CRP", "СРБ"):
        assert C.normalize(spelling) == "CRP", spelling


def test_рфмк_и_soluble_fibrin_monomer_одно_имя():
    """Вердикт владельца 2026-07-30: РФМК = Soluble Fibrin Monomer Complex.

    До правила западный бланк с тем же анализом завёл бы отдельное имя, и две
    точки одного вещества никогда не встретились бы в одном тренде — тот же
    механизм тихой потери, что у СРБ и белка мочи.
    """
    for spelling in ("РФМК", "растворимые фибрин-мономерные комплексы",
                     "Soluble Fibrin Monomer Complex", "SFMC", "Fibrin monomer"):
        assert C.normalize(spelling) == "RFMK", spelling
    # Граница: D-димер — другой продукт деградации фибрина, не тот же аналит.
    assert C.normalize("D-dimer") == "D_dimer"
    assert C.normalize("фибриноген") == "Fibrinogen"


def test_мг_на_100_мл_это_мг_дл():
    """Дециллитр равен 100 мл: меняется запись единицы, не величина."""
    assert C._norm_unit("мг/100 мл") == C._norm_unit("мг/дл") == "mg/dl"
    assert C._norm_unit("mg/100 ml") == "mg/dl"


# ── Иммунологические имена: словарные контроли ──

def test_иммунологические_имена_канонизируются():
    """Сконструированные варианты общего словаря: класс антитела сохраняется."""
    cases = {
        " anti-CCP (учебная форма) ": "Anti_CCP",
        " Anti-TPO ": "Anti_TPO",
        " IgG2 (учебная подпись) ": "IgG2",
        " IgG4 (учебная подпись) ": "IgG4",
    }
    for raw, canon in cases.items():
        assert C.normalize(raw) == canon, raw


def test_класс_антитела_не_склеивается():
    """IgA и IgG одного антигена — РАЗНЫЕ аналиты с разными нормами.
    Односторонний тест зеленел бы и на правиле, сводящем оба класса в одно имя."""
    assert C.normalize("Антитела к глиадину IgA") != C.normalize("Антитела к глиадину IgG")
    assert C.normalize("Helicobacter pylori IgA") != C.normalize("Helicobacter pylori IgG")


def test_субклассы_igg_не_склеиваются_с_общим():
    """Сумма субклассов IgG1–IgG4 и есть общий IgG: склеить целое с частью —
    тот же класс ошибки, что ox-LDL в LDL. Общий печатают в мг/дл, субклассы в г/л."""
    assert len({C.normalize(n) for n in ("IgG1", "IgG2", "IgG3", "IgG4")}) == 4
    # Общего имени намеренно НЕТ: normalize обязан вернуть его сырым, а не подобрать
    # ближайший субкласс. Красное здесь означало бы, что кто-то завёл склейку.
    assert C.normalize("Иммуноглобулин G общий") not in {"IgG1", "IgG2", "IgG3", "IgG4"}
    assert C.normalize("Антитела к тиреопероксидазе, анти-ТПО") == "Anti_TPO"


def test_ph_и_плотность_мочи_безразмерны():
    """pH и относительная плотность безразмерны; отсутствие единицы не потеря."""
    assert C.is_dimensionless("Urine_pH")  # имя канона; голое «pH» намеренно НЕ синоним
    assert C.is_dimensionless("Urine_SG")
    assert C.normalize("DANSİTE".lower()) == "Urine_SG"
    assert C.normalize("Относительная плотность") == "Urine_SG"
    # Граница: эритроциты мочи считаются в поле зрения или кл/мкл — там единица
    # НУЖНА, и записывать их в безразмерные значило бы спрятать настоящую потерю.
    assert not C.is_dimensionless("Urine_RBC")


def test_composite_reference_spellings():
    """Составные названия должны проходить гейт наравне с краткими."""
    assert C.normalize(" TIBC (учебная подпись) ") == "TIBC"
    assert C.normalize(" Methylmalonic acid ") == "Methylmalonic_acid"
    assert C.normalize(" transferrin saturation (учебная подпись) ") == "Transferrin_saturation"


def test_насыщение_трансферрина_не_сам_трансферрин():
    """Концентрация белка и доля насыщения — разные величины и разные тренды."""
    assert C.normalize("Transferrin") == "Transferrin"
    assert C.normalize("трансферрин") == "Transferrin"
    assert C.normalize("Transferrin saturation") != C.normalize("Transferrin")


def test_биоактивный_тестостерон_не_свободный():
    """Свободная фракция входит в биодоступную как часть; имена не склеиваются."""
    assert C.normalize("Биологически активный тестостерон") == "Testosterone_bioavailable"
    assert C.normalize("Тестостерон свободный") == "Testosterone_free"
    assert C.normalize("Биологически активный тестостерон") != C.normalize("Тестостерон свободный")
    # НЕГАТИВНЫЙ КОНТРОЛЬ: общий тестостерон не должен уехать ни в одну из веток
    assert C.normalize("Тестостерон") == "Testosterone"


def test_сегментоядерные_нейтрофилы_часть_а_не_целое():
    """Арифметика бланка закрывается (синтетика той же формы): сегментоядерные
    58,00 % + палочкоядерные 4,00 % = 62 % ≈ «Нейтрофилы (NEU)» 62,10 %
    автоанализатора. Синоним склеивал часть с целым, и разница уходила в тренд
    как падение на величину палочкоядерных."""
    seg = C.normalize("Сегментоядерные нейтрофилы")
    band = C.normalize("Палочкоядерные нейтрофилы")
    assert seg == "Neutrophils_segmented_pct"
    assert band == "Band_neutrophils"   # дом уже был; второго не заводим
    assert seg != C.normalize("Нейтрофилы%")
    assert 58.00 + 4.00 == 62.00          # то, чем доказана раздельность величин
    # НЕГАТИВНЫЙ КОНТРОЛЬ: новые имена ЗНАКОМЫ канону, иначе Layer-2 промоута
    # заблокирует живые строки как «процедура/услуга» — так уже было с TIBC.
    assert {seg, band, "Testosterone_bioavailable"} <= C.CANONICALS


def test_метод_не_уехал_в_имя():
    """Граница решения 31.07: «по Фонио» — это МЕТОД подсчёта тромбоцитов, а не
    другой аналит. Метод получит свой носитель из бланка (секция страницы); имя
    остаётся одно, иначе у метода станет два дома — как у материала (blood в
    колонке против serum в имени), и это уже стоило расхождений."""
    assert C.normalize("Тромбоциты по Фонио") == "PLT"
    assert C.normalize("Тромбоциты (PLT)") == "PLT"


def test_размерность_уточняет_имя_а_материал_нет():
    """Единица различает абсолютное количество и процент под общим именем. Материал хранится в отдельной колонке specimen и не порождает суффикс."""
    assert C.dimension_key("Immature_granulocytes", "10^9/л") == "Immature_granulocytes_abs"
    assert C.dimension_key("Immature_granulocytes", "%") == "Immature_granulocytes_pct"
    assert C.dimension_key("Plasma_cells", "%") == "Plasma_cells_pct"
    # чужие аналиты не трогаются
    assert C.dimension_key("HGB", "g/l") == "HGB"
    # НЕГАТИВНЫЙ КОНТРОЛЬ: пустая единица не порождает суффикс-догадку
    assert C.dimension_key("Plasma_cells", "") == "Plasma_cells"
    assert C.dimension_key("Plasma_cells", None) == "Plasma_cells"
    # материал НЕ уточняется этой функцией — у него своя колонка
    assert C.dimension_key("Methylmalonic_acid", "ммоль/моль креатинина") == "Methylmalonic_acid"


def test_суффиксные_имена_известны_канону():
    """Иначе Layer-2 промоута заблокирует живые строки как «процедуру/услугу».
    Множество выводится из таблицы уточнений — второй перечень краснеет здесь."""
    for c, (suffixes, _rule) in C._DIMENSION_BY_UNIT.items():
        for s in suffixes:
            assert f"{c}_{s}" in C.CANONICALS


def test_rdw_fl_refines_to_sd_percent_stays_bare():
    """Правило-вывод ADR, первый кирпич (решение владельца 2026-08-13):
    `фл` — однозначная единица RDW-SD, суффикс выводится; `%` (RDW-CV) и пустая
    единица имя НЕ уточняют — суффикс из неоднозначности был бы догадкой."""
    assert C.dimension_key("RDW", "фл") == "RDW_SD"
    assert C.dimension_key("RDW", "fl") == "RDW_SD"
    assert C.dimension_key("RDW", "%") == "RDW"
    assert C.dimension_key("RDW", "") == "RDW"
    assert "RDW_SD" in C.CANONICALS
    # идентичность: фл-строка узнаётся как RDW_SD, %-строка по-прежнему не судится
    assert C.identity_name("RDW", "фл") == "RDW_SD"
    assert C.identity_name("RDW", "%") is None


def test_bleak_2026_08_29_names_resolve():
    """Составные имена и буквенные обозначения сводятся к словарю."""
    import lab_canon as lc
    assert lc.normalize("Аполипопротеин B") == "ApoB"
    assert lc.normalize("Аполипопротеин В") == "ApoB"          # кириллица — как было
    assert lc.normalize("Насыщение трансферрина железом") == "Transferrin_saturation"
    assert lc.normalize("P-LCR Соотношение крупных тромбоцитов") == "P_LCR"
    assert lc.normalize("Соотношение крупных тромбоцитов") == "P_LCR"


def test_key_designators_fold_both_scripts():
    """Обратный индекс дополняется двойниками одиночных букв через верхний регистр. Целые слова фолдинг не меняет."""
    import lab_canon as lc
    # оба написания однобуквенного обозначения находят одну статью
    assert lc.normalize("Витамин В12") == lc.normalize("Витамин B12") == "Vitamin_B12"
    assert lc.normalize("Апо В") == lc.normalize("Апо B") == "ApoB"
    # фолд ключей не трогает целые слова (нечитаемый «Copбит» запрещён)
    assert lc._fold_key_designators("сорбит") == "сорбит"
    assert lc._fold_key_designators("апо в") == "апо b"


def test_stool_markers_and_urine_casts_have_canonical_names_2026_09_14():
    """Сконструированный набор синонимов: каноническое имя должно быть достижимо, соседние классы не смешиваются."""
    import lab_canon as lc
    assert lc.normalize("Кальпротектин") == "Calprotectin"
    assert lc.normalize("Зонулин") == "Zonulin"
    assert lc.normalize("Панкреатическая эластаза I") == "Pancreatic_elastase_1"
    assert lc.normalize("Цилиндры (другие)") == "Urine_Casts_other"
    # Граница: соседний мочевой цилиндр как сводился, так и сводится.
    assert lc.normalize("Цилиндры гиалиновые") == "Urine_Casts_hyaline"
