"""
Ф4 brief_validator — пост-рендер проверка. Pure unit.

Главный контроль: подавленный геном-ген, утёкший в текст из промпта (риск Ф0),
ловится. Показанный ген — не утечка. Гипотезы (pass-through) не проверяются.
"""
from __future__ import annotations

import pytest

import brief_cards as bc
import brief_validator as bv

pytestmark = pytest.mark.unit

_CRY1 = {"gene": "CRY1", "genotype": "AG", "significance": "Pathogenic",
         "clinical_summary": "синтетический вариант", "effect_allele_status": "verified"}


def _sup_cry1():
    return bc.from_genome(_CRY1, "sleep")  # semantic_key genome:sleep:cry1


def test_leak_of_suppressed_gene_detected():
    r = bv.validate("Сегодня фон ровный. Учитывая CRY1, глубокий сон хронически ниже.",
                    shown_cards=[], suppressed_cards=[_sup_cry1()])
    assert r["ok"] is False
    assert any(l["gene"].lower() == "cry1" for l in r["leaks"])


# ── РЕАЛЬНЫЙ путь пайплайна (регресс 2026-07-14) ────────────────────────────
# Для sleep-домена CRY1 НЕ genome-карточка: он склеен в карту sleep_agent через
# genome_mods. Старый сторож (provider=="genome") этот — флагманский — случай
# пропускал. genome_terms несёт ген независимо от провайдера.

def _band():
    return {"z": -1.8, "p_low": 40}


def test_leak_via_suppressed_sleep_card_realpipeline():
    """Подавлена карта sleep:deep с CRY1-модификатором → CRY1 в тексте = утечка."""
    card = bc.from_sleep_deep(42, _band(), genome_mods=["CRY1 AG Pathogenic"])
    assert card.provider == "sleep_agent" and card.genome_terms == ["CRY1"]
    r = bv.validate("Глубокий сон ниже полосы; вероятно из-за CRY1.",
                    shown_cards=[], suppressed_cards=[card])
    assert r["ok"] is False
    assert any(l["gene"] == "CRY1" for l in r["leaks"])


def test_shown_sleep_card_with_gene_not_leak():
    card = bc.from_sleep_deep(42, _band(), genome_mods=["CRY1 AG Pathogenic"])
    r = bv.validate("Учитывая CRY1, глубокий сон ниже полосы.",
                    shown_cards=[card], suppressed_cards=[])
    assert r["ok"] is True


def test_dedup_merges_genome_terms_so_leak_survives():
    """drift:deep:down канонизируется в sleep:deep:below_band и идёт РАНЬШЕ карты
    sleep_agent. Без слияния genome_terms дедуп выбросил бы CRY1-носитель → сторож
    ослеп. После фикса — survivor несёт CRY1."""
    drift = bc.from_drift({"metric": "deep", "direction": "down", "delta_pct": -20,
                           "streak_days": 5, "current_7d": 40, "baseline_30d": 55,
                           "severity": "moderate"})
    sleep = bc.from_sleep_deep(42, _band(), genome_mods=["CRY1 AG Pathogenic"])
    survivors = bc.dedup([drift, sleep])
    assert len(survivors) == 1
    # Первая карта выигрывает телом (ключ drift:deep:down), НО genome_terms слиты —
    # именно это доносит CRY1 до сторожа, какой бы порядок ни был.
    assert "CRY1" in survivors[0].genome_terms
    r = bv.validate("Глубокий сон ниже; CRY1 в игре.",
                    shown_cards=[], suppressed_cards=survivors)
    assert r["ok"] is False


def test_genome_terms_deduped():
    """Два варианта одного гена → одно имя (дубль гена схлопывается, порядок сохранён)."""
    card = bc.from_sleep_deep(42, _band(),
                              genome_mods=["CRY1 AG Path", "CRY1 GG Path", "CRY2 CT Path"])
    assert card.genome_terms == ["CRY1", "CRY2"]


def test_word_boundary_no_substring_fp():
    """Ген PPARG подавлен, но в тексте PPARGC1A (другой ген) → НЕ утечка (граница слова)."""
    card = bc.from_genome({"gene": "PPARG", "genotype": "GG", "significance": "Risk",
                           "clinical_summary": "x", "effect_allele_status": "verified"}, "energy")
    r = bv.validate("Показатель PPARGC1A сегодня в норме.",
                    shown_cards=[], suppressed_cards=[card])
    assert r["ok"] is True and not r["leaks"]
    r2 = bv.validate("Вариант PPARG влияет на метаболизм.",
                     shown_cards=[], suppressed_cards=[card])
    assert r2["ok"] is False


def test_clean_text_no_leak():
    r = bv.validate("Готовность выросла, ВСР выше нормы. Хорошая ночь.",
                    shown_cards=[], suppressed_cards=[_sup_cry1()])
    assert r["ok"] is True and not r["leaks"]


def test_shown_gene_is_not_a_leak():
    card = _sup_cry1()
    r = bv.validate("Учитывая CRY1, глубокий сон ниже полосы.",
                    shown_cards=[card], suppressed_cards=[])
    assert r["ok"] is True


def test_forbidden_phrase_hit():
    card = bc.Card(provider="x", semantic_key="k", lane="routine",
                   forbidden_claims=["направление эффекта подтверждено"])
    r = bv.validate("Здесь направление эффекта подтверждено данными.",
                    shown_cards=[card], suppressed_cards=[])
    assert r["ok"] is False and r["forbidden_hits"]


def test_hypotheses_passthrough_not_checked():
    hyp = bc.from_hypothesis({"memory_id": 5, "observation": "CRY1 упомянут тут", "status": "open"})
    # гипотеза suppressed, но provider!=genome → не проверяем её содержимое как утечку гена
    r = bv.validate("CRY1 в тексте", shown_cards=[], suppressed_cards=[hyp])
    assert r["ok"] is True
