"""
T-4.5 — LLM-judge: устаревшие источники помечаются датой.

Источник: USE_CASES.md UC-D-05.
Status: skipped — требует Anthropic API + явный паттерн в выводах,
который сейчас не реализован.
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.llm_judge, pytest.mark.requires_anthropic_key]


def test_stale_lab_marker_in_gp_output_skipped():
    pytest.skip("UC-D-05 explicit marker не реализован — intended")


def test_stale_genome_marker_in_council_output_skipped():
    pytest.skip("UC-D-05 explicit marker не реализован — intended")
