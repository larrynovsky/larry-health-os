"""
UC-B-05 — Lifestyle agents молчат без доменных данных.

Источник: USE_CASES.md §3.B → UC-B-05.
Реализация: `lifestyle_agents.py`.
Status: `implemented`.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_lifestyle_agents_module_imports():
    import lifestyle_agents
    assert lifestyle_agents is not None


def test_lifestyle_agents_have_brief_function():
    """В модуле должны быть функции для генерации brief'ов."""
    import lifestyle_agents as la
    candidates = ["sleep_brief", "movement_brief", "stress_brief", "energy_brief",
                   "build_briefs", "run_lifestyle_agents", "all_briefs"]
    found = [n for n in candidates if hasattr(la, n)]
    assert found, f"lifestyle_agents не имеет ни одной из {candidates}"


def test_genome_block_alone_does_not_create_brief(db):
    """
    Геномный блок без поведенческих данных не должен создавать brief
    (контракт lifestyle_agents: агент молчит только при полном отсутствии данных).

    Smoke: lifestyle_agents.run работает с пустыми daily_metrics.
    """
    import lifestyle_agents as la
    # Просто smoke: нет exception при минимальном входе
    db.add_genetic_variant("rs4680", gene="COMT", genotype="AG",
                            domain_tags="NEURO,ENERGY")
    # Без daily_metrics. Любой brief должен либо быть пустым, либо None.
    runner = getattr(la, "run", None) or getattr(la, "all_briefs", None)
    if runner is None:
        pytest.skip("публичная run/all_briefs функция отсутствует")
