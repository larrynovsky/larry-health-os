"""W5A-D6: tests для _build_patient_context_block (per-call profile injection).

Также проверка манифеста: нет хардкода пациента.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest
import cbcr_hypothesis as ch

pytestmark = pytest.mark.unit


def test_block_calls_helpers_per_call(monkeypatch):
    """_build_patient_context_block вызывает helpers, не использует module-const."""
    import hai_core
    import gp_agent

    call_count = {"profile": 0, "history": 0}

    def fake_profile():
        call_count["profile"] += 1
        return f"profile-call-{call_count['profile']}"

    def fake_history():
        call_count["history"] += 1
        return f"history-call-{call_count['history']}"

    monkeypatch.setattr(hai_core, "_build_patient_profile", fake_profile)
    monkeypatch.setattr(gp_agent, "_build_clinical_history", fake_history)

    block1 = ch._build_patient_context_block()
    block2 = ch._build_patient_context_block()

    assert "profile-call-1" in block1
    assert "profile-call-2" in block2  # call-by-call, не cached
    assert "history-call-1" in block1
    assert "history-call-2" in block2


def test_block_falls_back_when_helpers_fail(monkeypatch):
    """Если helpers падают — block содержит fallback маркеры, не raise."""
    import hai_core
    import gp_agent

    def boom():
        raise RuntimeError("DB unreachable")

    monkeypatch.setattr(hai_core, "_build_patient_profile", boom)
    monkeypatch.setattr(gp_agent, "_build_clinical_history", boom)

    block = ch._build_patient_context_block()
    assert "недоступ" in block.lower()


def test_block_has_header_sections():
    """Block содержит секции «Профиль пациента» и «Клиническая история» — для LLM ориентир."""
    block = ch._build_patient_context_block()
    assert "## Профиль пациента" in block
    assert "## Клиническая история" in block


def test_manifest_no_patient_hardcode():
    """Регрессия D6: манифест не должен содержать конкретного пациента."""
    manifest = ch._load_manifest()
    import pii_census   # досье владельца — из приватного словаря, не литералами в публичном тесте
    forbidden = pii_census.literals(["owner_clinical", "surname"])
    found = [t for t in forbidden if t in manifest]
    assert found == [], f"Hardcode пациента в манифесте: {found}"
