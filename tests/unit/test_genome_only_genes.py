"""
Фильтр only_genes в build_lifestyle_genome_block (Bug: геном-повтор, 2026-07-14).

Класс бага: гейт одобряет ген персонально (≤1/мес), но блок домена отдавался
целиком — включая PINNED-гены (MTHFR/PER3/COMT), которые форсятся всегда. Так
подавленный MTHFR протекал в текст (BRIEF_LEAK). only_genes перебивает и PINNED.
"""
from __future__ import annotations

import pytest

import genome_context as gc

pytestmark = pytest.mark.unit


def test_only_genes_filters_to_approved(db):
    # Полный энергетический блок содержит несколько генов; only_genes сужает до одного.
    full = gc.build_lifestyle_genome_block("energy", max_variants=6) or ""
    if "MTHFR" not in full:
        pytest.skip("нет MTHFR в тестовом геноме — фикстура без энергетических вариантов")
    only_lepr = gc.build_lifestyle_genome_block("energy", max_variants=6, only_genes={"LEPR"})
    assert "MTHFR" not in only_lepr  # PINNED MTHFR подавлен фильтром
    # и если LEPR есть в геноме — он остаётся; иначе блок пуст (оба варианта валидны)
    assert only_lepr == "" or "LEPR" in only_lepr


def test_only_genes_empty_when_none_match(db):
    # Ген, которого нет в наборе → пустой блок, не весь домен.
    block = gc.build_lifestyle_genome_block("energy", max_variants=6,
                                            only_genes={"NONEXISTENT_GENE"})
    assert block == ""
