"""
Проводка пост-рендер сторожа (gp_agent._validate_brief), регресс 2026-07-14.

RST «test the wiring»: зелёный юнит brief_validator доказывает ЧИСТУЮ функцию, но не
то, что call-site правильно разбивает cards на shown/suppressed по shown-set гейта.
Здесь проверяем именно разбиение + что ген-модификатор (склеенный в sleep-карту)
ловится на реальном пути, а показанный — нет.
"""
from __future__ import annotations

import pytest

import brief_cards as bc
import gp_agent

pytestmark = pytest.mark.unit

_BAND = {"z": -1.8, "p_low": 40}


def _sleep_cry1():
    return bc.from_sleep_deep(42, _BAND, genome_mods=["CRY1 AG Pathogenic"])


def test_hook_flags_suppressed_cry1_leak():
    """sleep:deep подавлена (нет в shown), текст называет CRY1 → сторож ловит."""
    cards = [_sleep_cry1()]
    shown = set()  # ничего не показано → sleep-карта в suppressed
    v = gp_agent._validate_brief("Фон ровный, но CRY1 тянет глубокий вниз.", cards, shown)
    assert v["ok"] is False
    assert any(l["gene"] == "CRY1" for l in v["leaks"])


def test_hook_shown_cry1_not_flagged():
    """sleep:deep ПОКАЗАНА → CRY1 легитимен, не утечка."""
    cards = [_sleep_cry1()]
    shown = {"sleep:deep:below_band"}
    v = gp_agent._validate_brief("Учитывая CRY1, глубокий сон ниже полосы.", cards, shown)
    assert v["ok"] is True


def test_hook_clean_text_ok():
    cards = [_sleep_cry1()]
    v = gp_agent._validate_brief("Готовность выше нормы, спокойная ночь.", cards, set())
    assert v["ok"] is True and not v["leaks"]


# ── C3 (2026-07-15): находка валидатора ДЕЙСТВУЕТ (вырез), а не только логируется ──

def test_strip_leaked_genes_removes_sentence_keeps_rest():
    txt = "Готовность выше нормы, спокойная ночь. При вариантах CRY1 глубокий сон ниже."
    out, removed = gp_agent._strip_leaked_genes(txt, {"CRY1"})
    assert "CRY1" not in out
    assert "Готовность выше нормы" in out
    assert removed == [["CRY1"]]


def test_strip_leaked_genes_preserves_paragraphs():
    txt = "Абзац один без генов.\n\nВторой абзац с CRY1 внутри."
    out, _ = gp_agent._strip_leaked_genes(txt, {"CRY1"})
    assert "Абзац один" in out and "CRY1" not in out and "\n\n" not in out  # 2-й абзац ушёл целиком


def test_strip_leaked_genes_empty_noop():
    txt = "Ничего не режем."
    assert gp_agent._strip_leaked_genes(txt, set()) == (txt, [])


def test_validator_driven_strip_removes_suppressed_gene():
    """Флагман C3 (инцидент 2026-07-15): подавленный ген, пойманный
    валидатором, должен быть ВЫРЕЗАН, не только залогирован. Воспроизводит связку
    validate→strip детерминированно, без LLM."""
    cards = [_sleep_cry1()]
    shown = set()  # CRY1-карта подавлена (не в shown)
    report = "Фон ровный, но CRY1 тянет глубокий вниз. Днём — прогулка."
    v = gp_agent._validate_brief(report, cards, shown)
    assert v["ok"] is False
    leaked = {l["gene"].upper() for l in v["leaks"] if l.get("gene")}
    stripped, removed = gp_agent._strip_leaked_genes(report, leaked)
    assert "CRY1" not in stripped, "подавленный ген обязан быть вырезан"
    assert "прогулка" in stripped, "соседнее предложение должно уцелеть"
    v2 = gp_agent._validate_brief(stripped, cards, shown)
    assert v2["ok"] is True, "после выреза — чисто"
