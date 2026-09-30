"""
Wave 5A W-6 — unit-тесты для cbcr_lookup.read_cbcr_concept.

Требуется доступ к wiki в iCloud. Если её нет — тесты пропускаются (skip), не падают.

Помечены slow: читают ~97 markdown-файлов из iCloud, что может вызвать stall
на Studio если файлы evicted из локального кэша CloudKit.
Запустить вручную: pytest -m slow tests/unit/test_cbcr_lookup.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import cbcr_lookup

pytestmark = [pytest.mark.unit, pytest.mark.slow]


@pytest.fixture(autouse=True)
def _skip_if_no_wiki():
    if not cbcr_lookup.WIKI_DIR.exists():
        pytest.skip(f"CBCR wiki not available at {cbcr_lookup.WIKI_DIR}")
    # Сброс кэша между тестами — на случай если правят wiki во время разработки
    cbcr_lookup._build_alias_index.cache_clear()


# ─── canonical имена ───────────────────────────────────────────────────────

def test_canonical_lookup_illness_script():
    text = cbcr_lookup.read_cbcr_concept("Illness Script")
    assert "illness script" in text.lower()
    # body, не frontmatter
    assert not text.startswith("---")
    assert "aliases:" not in text[:200]


def test_canonical_lookup_case_insensitive():
    a = cbcr_lookup.read_cbcr_concept("Illness Script")
    b = cbcr_lookup.read_cbcr_concept("illness script")
    c = cbcr_lookup.read_cbcr_concept("ILLNESS SCRIPT")
    assert a == b == c


def test_canonical_lookup_semantic_qualifiers():
    text = cbcr_lookup.read_cbcr_concept("Semantic Qualifiers")
    assert "Bordage" in text or "semantic" in text.lower()
    assert not text.startswith("---")


# ─── alias resolution ──────────────────────────────────────────────────────

def test_alias_resolves_to_canonical():
    """`Clinical Scripts` — alias из Illness Script.md → должен дать body Illness Script."""
    canonical = cbcr_lookup.read_cbcr_concept("Illness Script")
    aliased = cbcr_lookup.read_cbcr_concept("Clinical Scripts")
    assert canonical == aliased


def test_alias_resolves_pathophysiology():
    """`Pathophysiology` — alias из Fault.md → должен дать body Fault."""
    canonical = cbcr_lookup.read_cbcr_concept("Fault")
    aliased = cbcr_lookup.read_cbcr_concept("Pathophysiology")
    assert canonical == aliased


# ─── unknown / typo ────────────────────────────────────────────────────────

def test_unknown_concept_returns_suggestions():
    """Опечатка → должны быть top-5 suggestions."""
    result = cbcr_lookup.read_cbcr_concept("illnes script")  # typo
    assert "не найден" in result or "❌" in result
    # Должна быть suggestion на illness script
    assert "illness" in result.lower()


def test_completely_unknown_returns_message():
    """Совсем не похожее имя — сообщение об отсутствии, не traceback."""
    result = cbcr_lookup.read_cbcr_concept("totally_made_up_concept_xyz_42")
    assert "не найден" in result
    # graceful, не Exception


# ─── strip frontmatter ─────────────────────────────────────────────────────

def test_frontmatter_stripped():
    """Output не должен содержать YAML-frontmatter."""
    text = cbcr_lookup.read_cbcr_concept("Bias")
    # Frontmatter markers
    assert "title: Bias" not in text
    assert "aliases:" not in text
    assert "confidence:" not in text


# ─── tool schema ───────────────────────────────────────────────────────────

def test_tool_schema_format():
    schema = cbcr_lookup.get_tool_schema()
    assert schema["name"] == "read_cbcr_concept"
    assert "description" in schema
    assert schema["input_schema"]["type"] == "object"
    assert "name" in schema["input_schema"]["properties"]
    assert schema["input_schema"]["required"] == ["name"]


def test_tool_schema_description_mentions_canonical_concepts():
    """Description должен дать LLM подсказки о канонических именах — иначе LLM не угадает."""
    schema = cbcr_lookup.get_tool_schema()
    desc = schema["input_schema"]["properties"]["name"]["description"]
    # Минимальный набор
    assert "Illness Script" in desc
    assert "Semantic Qualifiers" in desc
    assert "Bias" in desc


# ─── размер индекса (sanity) ───────────────────────────────────────────────

def test_index_has_meaningful_size():
    """Wiki должна иметь ≥80 концептов (сейчас 95-96)."""
    index = cbcr_lookup._build_alias_index()
    unique_paths = set(index.values())
    assert len(unique_paths) >= 80, f"Wiki сократилась: {len(unique_paths)} файлов"
    # Aliases должны добавлять ≥150 entries сверх canonical
    assert len(index) > len(unique_paths) + 150
