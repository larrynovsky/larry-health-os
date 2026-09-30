"""L5 integration: genome_context рендер после strand-фикса (Ф3).

Проверяет, что заполненный effect_allele правильно маршрутизирует варианты:
- верифицированный носитель (hetero/homo) → секция «верифицированы»;
- верифицированный wildtype Pathogenic → свёрнутая сводка «не подтверждено»,
  а НЕ отдельной строкой и НЕ тихо выброшен (анти-тихий-отказ);
- NULL effect_allele → секция «не верифицированы».
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest

import health_db
import genome_context as gc

pytestmark = pytest.mark.integration


def _seed(rsid, genotype, significance, effect_allele, status):
    health_db.upsert_genetic_variant(rsid, {
        "gene": "GENE_" + rsid, "genotype": genotype, "significance": significance,
        "conditions": ["cond"], "domain_tags": ["oncology"],
        "effect_allele": effect_allele, "effect_allele_status": status,
        "ref_allele": "G", "assembly": "GRCh37",
    })


def test_rendering_routes_by_verified_carrier_status(db):
    # верифицированный носитель: AG, effect_allele A → hetero
    _seed("rsCAR", "AG", "Pathogenic", "A", "resolved")
    # верифицированный wildtype: GG, effect_allele A (нет в генотипе) → wildtype
    _seed("rsWT", "GG", "Pathogenic", "A", "resolved")
    # не верифицирован: effect_allele NULL
    _seed("rsUNV", "CC", "Pathogenic", None, "palindromic")

    block = gc.build_genetic_context_block()

    # носитель — в верифицированной секции, строкой
    assert "⚠️ ПАТОГЕННЫЕ (верифицированы)" in block
    assert "rsCAR" in block

    # wildtype — в сводке «не подтверждено», и НЕ отдельной строкой
    assert "НОСИТЕЛЬСТВО НЕ ПОДТВЕРЖДЕНО" in block
    assert "1 «патогенных/вероятно патогенных»" in block
    assert "rsWT" not in block          # не вывалился отдельной строкой

    # NULL — в «не верифицированы»
    assert "НЕ ВЕРИФИЦИРОВАНЫ" in block
    assert "rsUNV" in block


def test_no_verified_noncarrier_summary_when_none(db):
    """Если нет verified-wildtype Pathogenic — сводки нет (не пустой шум)."""
    _seed("rsCAR", "AG", "Pathogenic", "A", "resolved")
    block = gc.build_genetic_context_block()
    assert "НОСИТЕЛЬСТВО НЕ ПОДТВЕРЖДЕНО" not in block


def test_carriers_prioritized_and_unverified_bounded(db):
    """Ф5: носитель показан несмотря на 20 непроверяемых Pathogenic;
    непроверяемая секция ограничена (есть строка '… и ещё')."""
    _seed("rsCAR", "AG", "Pathogenic", "A", "resolved")
    for i in range(20):
        # CC + effect_allele NULL → homo_unknown → unverified
        _seed(f"rsU{i:02d}", "CC", "Pathogenic", None, "palindromic")

    block = gc.build_genetic_context_block(max_variants=10)

    assert "rsCAR" in block                       # носитель не вытеснен
    assert "НЕ ВЕРИФИЦИРОВАНЫ" in block
    assert "… и ещё" in block                      # хвост свёрнут, не вся стена
    # показано не больше 8 непроверяемых строк
    shown = sum(1 for ln in block.splitlines() if ln.strip().startswith("• ") and "неизвестен" in ln)
    assert shown <= 8, shown
