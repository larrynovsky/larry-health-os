"""Состав снимка EFLM — правило, а не карта (решение владельца 25.09, нить eflm-rule).

Оракулы: правило берёт то, что узнаёт словарь канона, и только это; двойник в один канон не
берётся ни один; пять аналитов, по которым сеются пороги лаб-трендов, правило узнаёт по их
точным именам в EFLM (снятие британского написания из словаря → красное здесь).
"""
from __future__ import annotations

import norm_documents as nd


def _m(name, cvi=True, mid=1):
    metas = [{"cvi": {"median": 5, "lower": 4, "upper": 6, "number_used": 3, "updated_at": "2026-01-01T00:00:00Z"},
              "matrix": {"matrix_expansion": "Serum"}}] if cvi else []
    return {"analyte": {"id": mid, "display_name": name}, "metas": metas}


def test_rule_takes_what_canon_knows_and_nothing_else():
    data = [_m("Haemoglobin (Hb)"), _m("Ferritin"), _m("Amyloid-beta protein 42 (Aβ42)"),
            _m("Albumin", cvi=False)]
    picked, clash = nd._eflm_select(data)
    assert set(picked) == {"HGB", "Ferritin"}, picked.keys()
    assert clash == []


def test_two_measurands_into_one_canon_take_neither():
    picked, clash = nd._eflm_select([_m("Ferritin", mid=1), _m("ferritin", mid=2), _m("Iron")])
    assert "Ferritin" not in picked and clash == ["Ferritin"]
    assert "Iron" in picked


def test_trend_rule_analytes_resolve_by_their_eflm_names():
    # точные display_name EFLM (API 2026-09-25) пяти аналитов, по которым health_db сеет пороги трендов
    names = {"Carcinoembryonic antigen (CEA)": "CEA", "Cancer antigen 19-9 (CA 19-9)": "CA19-9",
             "Haemoglobin (Hb)": "HGB", "Mean corpuscular volume (MCV) ": "MCV", "Albumin": "Albumin"}
    picked, _ = nd._eflm_select([_m(n, mid=i) for i, n in enumerate(names)])
    assert set(picked) == set(names.values())


def test_no_hand_map_left():
    assert not hasattr(nd, "EFLM_TERMS_PATH"), "ручная карта вернулась — состав снова выдаёт автора"
