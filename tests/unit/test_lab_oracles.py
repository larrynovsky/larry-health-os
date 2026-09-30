"""Unit-тесты оракулов верификации анализов (lab_oracles).

Оракулы ловят потерю десятичной точки, рассогласование value/ref/flag,
провал полноты, дубли и отсутствие единиц. Числа ниже придуманы для теста.
"""
import lab_oracles as lo


def _t(name, value, **kw):
    base = {"canonical_name": name, "value": value, "unit": "", "ref_low": None,
            "ref_high": None, "doc_flag": None, "panel": None}
    base.update(kw)
    return base


def test_clean_pass_is_green():
    # panel не указан → оракул полноты не применяется (применяется только
    # когда панель документа известна и можно проверить её ядро)
    tests = [
        _t("RDW", 16.2, unit="%", ref_low=11.5, ref_high=14.5, doc_flag="H"),
        _t("Calcium", 9.3, unit="mg/dL", ref_low=8.6, ref_high=10.3, doc_flag="N"),
    ]
    r = lo.verify(tests)
    assert r["status"] == "green"
    assert r["count"] == 0


def test_decimal_loss_rdw_caught_by_bounds_and_magnitude():
    # Придуманный RDW: потеря точки увеличивает значение в десять раз
    tests = [_t("RDW", 162.0, unit="%", ref_low=11.5, ref_high=14.5, doc_flag="H")]
    r = lo.verify(tests)
    assert r["status"] == "flagged"
    assert r["issues"].get("bounds")       # вне физиологии
    assert r["issues"].get("magnitude")    # ×10 выше ref_high


def test_si_units_not_false_flagged_by_bounds():
    # HGB 127 g/L = 12.7 g/dL — норма; оракул unit-aware, не флагует по границам
    # (to_conventional перед проверкой). Регресс BL-LAB-CANON-1: раньше СИ-панели
    # ложно флагались целиком (HGB=127 вне [4,22]).
    assert not lo.verify([_t("HGB", 127.0, unit="g/L")])["issues"].get("bounds")
    assert not lo.verify([_t("Glucose", 5.2, unit="mmol/L")])["issues"].get("bounds")
    # без нормализации (raw g/dL) реальный выброс всё ещё ловится
    assert lo.verify([_t("HGB", 127.0, unit="g/dL")])["issues"].get("bounds")


def test_internal_inconsistency_calcium_flag_says_normal():
    # Придуманный Calcium: десятичный сдвиг противоречит флагу нормы
    tests = [_t("Calcium", 94.0, unit="mg/dL", ref_low=8.6, ref_high=10.3, doc_flag="N")]
    r = lo.verify(tests)
    assert r["status"] == "flagged"
    # ловится и bounds, и internal, и magnitude — достаточно internal
    assert r["issues"].get("internal")


def test_recall_gap_missing_cbc_core():
    tests = [
        _t("HCT", 44.0, panel="cbc"),
        _t("PLT", 265.0, panel="cbc"),
    ]
    r = lo.verify(tests)
    assert r["status"] == "flagged"
    assert any("recall" in m for m in r["issues"].get("completeness", []))


def test_dedup_same_analyte_twice():
    tests = [_t("HGB", 14.1, panel="cbc"), _t("HGB", 14.1, panel="cbc")]
    r = lo.verify(tests)
    assert r["issues"].get("dedup")


def test_units_required_when_ref_present():
    tests = [_t("Glucose", 100.0, unit="", ref_low=70.0, ref_high=100.0, doc_flag="N")]
    r = lo.verify(tests)
    assert r["issues"].get("units")


def test_canonical_ref_crosscheck():
    # документ напечатал явно неверный реф vs канон
    tests = [_t("Glucose", 100.0, unit="mg/dL", ref_low=7.0, ref_high=10.0, doc_flag="H")]
    refs = {"Glucose": (70.0, 100.0, "mg/dL")}
    r = lo.verify(tests, canonical_refs=refs)
    assert r["issues"].get("canonical_ref")


def test_dedup_ignores_null_canonical_names():
    # два показателя без canonical_name (вне словаря) не должны схлопываться
    tests = [_t(None, 1.36), _t(None, 3.8)]
    r = lo.verify(tests)
    assert not r["issues"].get("dedup")


def test_string_numbers_do_not_crash():
    # модель иногда отдаёт числа строками — оракул не должен падать (str-float)
    t = _t("Calcium", "9.3", unit="mg/dL", ref_low="8.6", ref_high="10.3", doc_flag="N")
    r = lo.verify([t])
    assert r["status"] in ("green", "flagged")


def test_percent_over_100():
    tests = [_t("Neutrophils_pct", 594.0, unit="%", panel="cbc")]
    r = lo.verify(tests)
    assert r["issues"].get("bounds")
