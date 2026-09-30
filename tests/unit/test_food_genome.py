"""
Сезон×геном (S3): выгодные продукты месяца ПЕР-ТЕНАНТ.

RST-флагман: список зависит от генома ЭТОГО тенанта (MTHFR-буст фолата) — тенант без
патогенного MTHFR получает ДРУГОЙ список. Кросс-тенант-изоляция на уровне генератора.
"""
from __future__ import annotations

import pytest

import brief_cards as bc
import food_genome as fg

pytestmark = pytest.mark.unit

_MTHFR = [{"gene": "MTHFR", "genotype": "TT", "significance": "Pathogenic"}]  # синтетика
_NO_MTHFR = [{"gene": "SLCO1B1", "genotype": "TT", "significance": "Risk"}]


def test_summer_beneficial_has_tomatoes_apples(monkeypatch):
    monkeypatch.setattr(fg._sp, "in_season", lambda month: {
        "fruits": ["яблоки"], "vegetables": ["помидоры"], "seafood": []})
    items = fg.beneficial_this_month(7, [], lang="ru")
    foods = {i["food"] for i in items}
    assert "помидоры" in foods and "яблоки" in foods


def test_winter_no_out_of_season_strawberries(monkeypatch):
    monkeypatch.setattr(fg._sp, "in_season", lambda month: {
        "fruits": ["яблоки"], "vegetables": ["капуста"], "seafood": []})
    foods = {i["food"] for i in fg.beneficial_this_month(1, [], lang="ru")}
    assert "яблоки" in foods and "клубника" not in foods


@pytest.mark.owner_data
def test_mthfr_boosts_folate_foods_first():
    items = fg.beneficial_this_month(7, _MTHFR)
    boosted = [i for i in items if i["boosted"]]
    assert boosted and all(i["food"] in fg._FOLATE_FOODS for i in boosted)
    # Буст-обоснование про фолат, но БЕЗ имени гена (гены ≤1/мес + скраб; 2026-07-14).
    assert "фолат" in boosted[0]["why"] and "MTHFR" not in boosted[0]["why"]


@pytest.mark.owner_data
def test_fruits_rank_above_vegetables():
    """владелец 2026-07-14: фрукты приоритетнее овощей. Первый непустой — фрукт."""
    items = fg.beneficial_this_month(7, _MTHFR)
    assert items, "июль должен давать продукты"
    assert items[0]["is_fruit"] is True
    # все фрукты идут раньше любого овоща
    veg_idx = [i for i, x in enumerate(items) if not x["is_fruit"]]
    fruit_idx = [i for i, x in enumerate(items) if x["is_fruit"]]
    assert not fruit_idx or not veg_idx or max(fruit_idx) < min(veg_idx)


@pytest.mark.owner_data
def test_food_of_day_rotates_with_variety():
    """владелец 2026-07-14 «одни бобы»: продукт дня ротируется по дате, не фиксируется."""
    picks = [fg.food_of_day(7, [], d)["food"] for d in range(12)]
    assert len(set(picks)) >= 6          # день за днём разное, не один продукт
    assert fg.food_of_day(7, [], 0)["is_fruit"] is True   # цикл начинается с фруктов
    # в цикле встречаются и морепродукты (не только фрукты/овощи)
    all_kinds = {fg.food_of_day(7, [], d)["kind"] for d in range(25)}
    assert all_kinds == {0, 1, 2}        # фрукты, овощи, морепродукты — все три


@pytest.mark.owner_data
def test_seafood_included_and_ranks_last():
    """Морепродукты (регион) теперь в списке; ранг ниже фруктов и овощей (2026-07-14)."""
    items = fg.beneficial_this_month(7, [])
    kinds = {i["food"]: i["kind"] for i in items}
    assert any(k == 2 for k in kinds.values()), "морепродукты должны попадать в список"
    # тунец (омега-3) в июле есть
    assert "тунец" in kinds
    # все морепродукты (kind=2) идут после любого фрукта/овоща (kind<2)
    sea_idx = [i for i, x in enumerate(items) if x["kind"] == 2]
    land_idx = [i for i, x in enumerate(items) if x["kind"] < 2]
    assert not sea_idx or not land_idx or min(sea_idx) > max(land_idx)


@pytest.mark.owner_data
def test_per_tenant_isolation_mthfr_vs_not():
    """RST: один месяц, РАЗНЫЙ геном → разный список (буст только у MTHFR-тенанта)."""
    tenant_a = fg.beneficial_this_month(7, _MTHFR)
    tenant_b = fg.beneficial_this_month(7, _NO_MTHFR)
    assert any(i["boosted"] for i in tenant_a)
    assert not any(i["boosted"] for i in tenant_b)   # тенант без MTHFR — без фолат-буста


def test_from_season_food_card_shape():
    it = {"food": "помидоры", "tag": "lycopene", "why": "ликопин", "boosted": False}
    c = bc.from_season_food(it)
    assert c.provider == "food" and c.semantic_key.startswith("food:seasonal:lycopene:")
    assert "помидоры" in c.evidence_summary


def test_region_food_catalog_preserves_selection_and_legacy_views(tmp_path, monkeypatch):
    """Закрытый каталог заменяет шаблон целиком; алиас, причины и ранг сохраняются."""
    import runpy
    from pathlib import Path
    import yaml
    import region_pack

    catalog = {
        "produce": {"яблоки": {"tag": "regional_fiber", "why": {"ru": "пектин", "en": "pectin"},
                                "label": {"ru": "яблоки", "en": "regional apples"}}},
        "seafood": {"сельдь": {"tag": "regional_omega3", "why": {"ru": "омега-3", "en": "omega-3"},
                               "label": {"ru": "сельдь", "en": "regional herring"}}},
    }
    path = tmp_path / "region.yaml"
    path.write_text(yaml.safe_dump({"food_catalog": catalog, "seasonal": {
        7: {"fruits": ["яблоки"], "vegetables": [], "seafood": ["сельдь"]}}}, allow_unicode=True))
    monkeypatch.setattr(region_pack, "PATH", path)
    region_pack._load.cache_clear()
    try:
        module = runpy.run_path(str(Path(fg.__file__)))
        assert module["FOOD_BENEFITS"] == {"яблоки": ("regional_fiber", "пектин")}
        assert module["_SEAFOOD_BENEFITS"] == {"сельдь": ("regional_omega3", "омега-3")}
        for lang, labels, reasons in (("ru", ["яблоки", "сельдь"], ["пектин", "омега-3"]),
                                     ("en", ["regional apples", "regional herring"], ["pectin", "omega-3"])):
            items = module["beneficial_this_month"](7, [], lang=lang)
            assert [i["food_alias"] for i in items] == ["яблоки", "сельдь"]
            assert [i["food"] for i in items] == labels
            assert [i["why"] for i in items] == reasons
            assert [i["kind"] for i in items] == [0, 2]
            assert [i["tag"] for i in items] == ["regional_fiber", "regional_omega3"]
    finally:
        region_pack._load.cache_clear()


def test_food_without_region_pack_matches_neutral_template(tmp_path, monkeypatch):
    """Свежая установка получает тот же полный нейтральный каталог в коде и в своде."""
    import runpy
    from pathlib import Path
    import yaml
    import region_pack

    monkeypatch.setattr(region_pack, "PATH", tmp_path / "absent.yaml")
    region_pack._load.cache_clear()
    try:
        module = runpy.run_path(str(Path(fg.__file__)))
        template = yaml.safe_load((Path(fg.__file__).parent / "templates/methodology/clinical_kb/food.yaml").read_text())
        expected = {b["food"]: (b["tag"], b["why"]) for b in template["shared"]["benefits"]}
        assert len(expected) == len(template["shared"]["benefits"]) == 48
        assert module["food_catalog_for_person"]("ru") == expected
        assert {"яблоки", "капуста", "скумбрия", "сельдь", "треска"} <= expected.keys()
        assert template["by_condition"] == {}
        # Без календаря регион не выдумываем, даже при наличии общего каталога.
        assert module["beneficial_this_month"](7, [], lang="en") == []
    finally:
        region_pack._load.cache_clear()
