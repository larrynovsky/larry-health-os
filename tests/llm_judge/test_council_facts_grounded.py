"""
T-4.4 — LLM-judge: Council facts grounded.

Источник: USE_CASES.md UC-H-01 + ROADMAP T-4.4.

Идея: отдельный Haiku-вызов с промптом «найди в финале Council
утверждения, которые не подтверждены в <context>». Если 0 — pass.

Status: skipped до полной реализации mock Council session.
Требует Anthropic ключ для реальных вызовов.
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.llm_judge, pytest.mark.requires_anthropic_key]


def test_council_finale_grounded_in_context_skipped():
    pytest.skip("UC-H-01 LLM-judge требует Anthropic API + mock Council session")


def test_no_unsupported_medical_claims_skipped():
    pytest.skip("Требует записанного eval-датасета (golden answers) + Anthropic")
