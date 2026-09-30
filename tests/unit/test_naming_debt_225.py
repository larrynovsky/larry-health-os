"""Независимо составленный корпус написаний общего словаря.

Проверяет суффиксы, сноски, смешение алфавитов и границы похожих имён.
Корпус не воспроизводит staging или перечень анализов человека.
"""
from __future__ import annotations

import pytest

import lab_canon as LC

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("raw,canonical", [
    # Сконструированные варианты; пояснения в скобках заведомо учебные.
    (" 3MHIS (учебная подпись) ", "Methylhistidine_3"),
    (" ГЛУТАТИОН (учебная подпись) ", "Glutathione_reduced"),
    (" ОМЕГА-3 ЖК В % ОТ СУМ. ЖК (учебная подпись) ", "Omega3_FA_pct"),
    (" C0 (учебная подпись) ", "Carnitine_free"),
    (" триметиламин-n-оксид (учебная подпись) ", "TMAO"),
    (" СРЕДНЯЯ ИММУННАЯ РЕАКТИВНОСТЬ (учебная подпись) ", "ELI_Mean_IR"),
    (" glycine (учебная подпись) ", "Glycine"),
    (" taurine ", "Taurine"),
    (" Zinc** ", "Zinc"),
    (" Copper*** ", "Copper"),
    (" EPA (учебная подпись) ", "EPA"),
    (" DHA (учебная подпись) ", "DHA"),
    (" ОМЕГА-3 ЖК (учебная подпись) ", "Omega3_FA"),
    (" TMAO (учебная подпись) ", "TMAO"),
    (" БИСФЕНОЛ A (учебная подпись) ", "Bisphenol_A"),
    (" БИСФЕНОЛ А (учебная подпись) ", "Bisphenol_A"),
    (" ПЕПСИНОГЕН I (учебная подпись) ", "Pepsinogen_I"),
    (" ИММУНОГЛОБУЛИН M общий (учебная подпись) ", "IgM_total"),
    (" C3 КОМПОНЕНТ КОМПЛЕМЕНТА (учебная подпись) ", "C3_complement"),
    (" CD3+ (учебная подпись), % ", "T_cells_pct"),
    (" CD3+ (учебная подпись), абс ", "T_cells_abs"),
    (" AdrM (учебная подпись) ", "ELI_AdrM"),
    (" АНТИТЕЛА К ТРАНСГЛЮТАМИНАЗЕ Ig A (учебная подпись) ", "Anti_tTG_IgA"),
    (" АНТИ-ТГ (учебная подпись) ", "Anti_Thyroglobulin"),
    (" CA242** ", "CA242"),
    (" СООТНОШЕНИЕ ТМА/ТМАО (учебная подпись) ", "TMA_TMAO_ratio"),
])
def test_stage_names_resolve(raw, canonical):
    assert LC.normalize(raw) == canonical


def test_anti_ttg_and_anti_tg_are_two_analytes():
    """МИНА СКЛЕЙКИ: трансглютаминаза (целиакия) ≠ тиреоглобулин (щитовидка).
    Русские имена различаются одной морфемой; склейка смешала бы два домена."""
    assert LC.normalize("Антитела к трансглютаминазе Ig A") == "Anti_tTG_IgA"
    assert LC.normalize("Антитела к тиреоглобулину") == "Anti_Thyroglobulin"
    assert LC.normalize("Антитела к трансглютаминазе Ig A") != LC.normalize("Антитела к тиреоглобулину")


def test_eli_percent_native_identity():
    """ЭЛИ-величины: % — законная и единственная размерность, identity судится.
    Краснеет при снятии PERCENT_NATIVE из гарда (исполняемый негативный контроль)."""
    assert LC.identity_name("AdrM", "%") == "ELI_AdrM"
    assert LC.identity_name("Тироглобулин", "%") == "ELI_Thyroglobulin"
    # мина Инсулин: гормон-тёзка гардом ПО-ПРЕЖНЕМУ задержан
    assert LC.identity_name("Инсулин", "%") is None
    assert LC.identity_name("RDW", "%") is None


def test_ratios_are_dimensionless():
    for n in ("AA/EPA", "LA/DGLA", "Липофильный индекс", "Иммунорегуляторный индекс"):
        assert LC.is_dimensionless(n), n


def test_negative_control_unknown_stays_unknown():
    """Мусор не смеет считаться сводимым и после расширения словаря."""
    assert LC.normalize("Зюзюблик обыкновенный") not in LC.CANONICALS


# ── электрофорез: дом — канон (решение владельца 2026-08-14) ──

def test_electrophoresis_fraction_pairs():
    """Одна фракция, две размерности: доля (%) и масса (г/л) разводятся
    суффиксом по единице — тот же механизм, что RDW-CV/SD."""
    assert LC.normalize("Alpha 1") == "Alpha1_globulin"
    assert LC.normalize("Alpha 1 (г/л)") == "Alpha1_globulin"   # скобки срезает _key
    assert LC.dimension_key("Alpha1_globulin", "%") == "Alpha1_globulin_pct"
    assert LC.dimension_key("Alpha1_globulin", "г/л") == "Alpha1_globulin_conc"
    assert LC.dimension_key("Gamma_globulin", "%") == "Gamma_globulin_pct"
    # неизвестная/пустая единица имя НЕ уточняет — суффикс не гадается
    assert LC.dimension_key("Beta2_globulin", "") == "Beta2_globulin"
    assert "Alpha1_globulin_pct" in LC.CANONICALS and "Gamma_globulin_conc" in LC.CANONICALS


def test_electrophoresis_collisions_stay_apart():
    """НЕГАТИВНЫЕ КОНТРОЛИ: GGT не утягивается в Gamma-фракцию; биохимический
    Albumin (г/л, г/дл) остаётся собой — фракцией его делает ТОЛЬКО «%»."""
    assert LC.normalize("GGT") == "GGT"
    assert LC.normalize("Гамма-ГТ") != "Gamma_globulin"
    assert LC.normalize("Albumin") == "Albumin"
    assert LC.normalize("Альбумин") == "Albumin"
    # биохимический альбумин в % не меряется → % однозначно фракция
    assert LC.dimension_key("Albumin", "%") == "Albumin_pct"
    assert LC.dimension_key("Albumin", "г/л") == "Albumin"
    assert LC.dimension_key("Albumin", "g/dL") == "Albumin"
    assert LC.identity_name("Albumin", "%") == "Albumin_pct"
