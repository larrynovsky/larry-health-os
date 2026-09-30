"""Unit-тесты чистой логики: реконсиляция проходов (lab_recognizer) и
маршрутизация auto/pending (lab_backfill). Без API и без БД.
"""
# Раньше здесь стоял os.environ.setdefault("HEALTH_DATA_DIR", tempfile.mkdtemp()) — пережиток
# импорт-гарда health_db. На машине, где переменная не задана (посторонний по уроку установки),
# он утекал на ВЕСЬ прогон и ронял следующие тесты (приёмка урока свежим агентом 2026-09-24:
# 3 красных; без строки — 17 зелёных в том же порядке). Окружение тестов задаёт conftest.
import lab_recognizer as R
import lab_backfill as B


def _t(name, value):
    return {"canonical_name": name, "raw_name": name, "value": value,
            "unit": "", "ref_low": None, "ref_high": None, "doc_flag": "N", "panel": "cbc"}


def test_reconcile_agree():
    out = R._reconcile([_t("RDW", 13.6)], [_t("RDW", 13.6)])
    assert len(out) == 1
    assert out[0]["value_agreement"] == "agree"
    assert out[0]["confidence"] == "high"


def test_reconcile_disagree():
    out = R._reconcile([_t("RDW", 13.6)], [_t("RDW", 41.2)])
    assert out[0]["value_agreement"] == "disagree"
    assert out[0]["confidence"] == "low"
    assert out[0]["pass1_value"] == 13.6 and out[0]["pass2_value"] == 41.2


def test_reconcile_single():
    out = R._reconcile([_t("HGB", 14.0)], [])
    assert out[0]["value_agreement"] == "single"


def _tu(canonical, raw, value, unit):
    return {"canonical_name": canonical, "raw_name": raw, "value": value,
            "unit": unit, "ref_low": None, "ref_high": None, "doc_flag": "N",
            "panel": "chemistry"}


def test_reconcile_same_name_different_dimension_not_merged():
    """Синтетические доля и концентрация одного вещества — разные величины.
    Reconcile сохраняет обе при одинаковом имени и разных размерностях."""
    a = _tu("Albumin", "Albumin (г/л)", 42.1, "г/л")
    b = _tu("Albumin", "Albumin", 60.2, "%")
    out = R._reconcile([a], [b])
    assert len(out) == 2, "разная размерность канонического имени = разные измерения"
    vals = sorted((o["value"], o["value_agreement"]) for o in out)
    assert vals == [(42.1, "single"), (60.2, "single")]


def test_reconcile_within_pass_dimension_collision_keeps_both():
    """Та же мина ВНУТРИ одного прохода: dict по ключу молча терял вторую строку."""
    p1 = [_tu("Albumin", "Albumin", 60.2, "%"), _tu("Albumin", "Albumin (г/л)", 42.1, "г/л")]
    out = R._reconcile(p1, [])
    assert len(out) == 2, "внутрипроходная коллизия имени не должна съедать измерение"


def test_reconcile_canonical_same_dimension_still_merges():
    """Регресс: одинаковая размерность канонического имени сливается как раньше."""
    out = R._reconcile([_tu("Albumin", "Альбумин", 39.5, "г/л")],
                       [_tu("Albumin", "Albumin", 39.5, "г/л")])
    assert len(out) == 1 and out[0]["value_agreement"] == "agree"


def test_reconcile_noncanonical_names_stay_unit_blind():
    """Регресс: сырые имена ВНЕ канона сливаются слепо к форме единицы — иначе
    два написания одной единицы у двух проходов плодили бы ложные single."""
    a = {"canonical_name": None, "raw_name": "Alpha 1", "value": 4.8, "unit": "%",
         "ref_low": None, "ref_high": None, "doc_flag": "N", "panel": "chemistry"}
    b = dict(a, unit="проц")
    out = R._reconcile([a], [dict(b)])
    assert len(out) == 1 and out[0]["value_agreement"] == "agree"


def test_route_auto_when_green_and_full_agreement():
    verdict = {"status": "green", "count": 0}
    stats = {"disagreements": 0, "singles": 0}
    assert B._route(verdict, stats) == "auto"


def test_route_pending_on_oracle_flag():
    assert B._route({"status": "flagged", "count": 3}, {"disagreements": 0, "singles": 0}) == "pending"


def test_route_pending_on_disagree():
    assert B._route({"status": "green", "count": 0}, {"disagreements": 1, "singles": 0}) == "pending"


def test_route_pending_on_single():
    assert B._route({"status": "green", "count": 0}, {"disagreements": 0, "singles": 2}) == "pending"
