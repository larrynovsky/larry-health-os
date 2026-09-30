"""publication_reader — cheap-triage + analyze (SX-17b)."""
from __future__ import annotations

import json
from datetime import date

import pytest

pytestmark = pytest.mark.unit


def _seed_searcher_output(db):
    db.execute(
        "INSERT INTO agent_reports (date, agent_type, agent_name, has_findings, findings, pubmed_ids) "
        "VALUES (?, 'literature_search', 'test', 1, ?, ?)",
        (date.today().isoformat(),
         json.dumps([{
             "pmid": "PM_TEST_1", "topic_id": "t1", "title": "Title",
             "year": "2024", "journal": "J", "abstract": "Abstract text",
             "priority": "high", "domain": "test",
         }]),
         json.dumps(["PM_TEST_1"])),
    )


def test_reader_dismisses_at_triage_when_not_relevant(db, anthropic_mock):
    _seed_searcher_output(db)
    anthropic_mock.script(
        match=lambda p: "релевантности" in p or "relevant" in p.lower() or "ОТНОСИТСЯ" in p,
        response=json.dumps({"relevant": False, "reason": "pediatric, not my population"}),
    )
    import publication_reader as pr
    result = pr.run(max_per_run=1)
    assert result["candidates"] == 1
    assert result["dismissed"] == 1
    assert result["kept"] == 0


def test_reader_keeps_when_relevant(db, anthropic_mock):
    _seed_searcher_output(db)
    # Triage: relevant=True, затем analyze: structured
    anthropic_mock.script(
        match=lambda p: "релевантности" in p or "ОТНОСИТСЯ" in p,
        response=json.dumps({"relevant": True, "reason": "matches profile"}),
    )
    anthropic_mock.script(
        match=lambda p: "claim" in p.lower() and "population" in p.lower(),
        response=json.dumps({
            "claim": "X improves Y", "population": "post-surgery survivors",
            "evidence_level": "rct", "applicability_to_me": "direct",
            "relevance_assessment": "highly relevant",
        }),
    )
    import publication_reader as pr
    result = pr.run(max_per_run=1)
    assert result["kept"] == 1


# ── A1 нейтрализация (brief-neutralization 2026-07-16) ───────────────────────

def test_triage_prompt_uses_per_tenant_context_not_hardcoded():
    """A1 НЕСУЩИЙ: промпт триажа берёт профиль пациента ПАРАМЕТРОМ (per-tenant из БД),
    не из вшитой константы-досье. Падение = досье владельца вернулось в промпт."""
    import publication_reader as pr
    assert not hasattr(pr, "PATIENT_CONTEXT"), "вернулась вшитая константа-досье владельца"
    prompt = pr.TRIAGE_PROMPT_TEMPLATE.format(
        patient_context="__TENANT_PROFILE_MARKER__",
        title="t", journal="j", year="2024", abstract="a")
    assert "__TENANT_PROFILE_MARKER__" in prompt
    import pii_census
    for leak in pii_census.literals(["owner_clinical"]):
        assert leak not in prompt, f"утечка диагноза владельца в промпте триажа: {leak}"


def test_analysis_prompt_uses_per_tenant_context_not_hardcoded():
    """A1 НЕСУЩИЙ (второй промпт): analyze тоже per-tenant, без вшитого досье."""
    import publication_reader as pr
    prompt = pr.ANALYSIS_PROMPT_TEMPLATE.format(
        patient_context="__TENANT_PROFILE_MARKER__",
        pmid="1", title="t", journal="j", year="2024", abstract="a",
        topic_id="x", domain="d", priority="high")
    assert "__TENANT_PROFILE_MARKER__" in prompt
    import pii_census
    for leak in pii_census.literals(["owner_clinical"]):
        assert leak not in prompt, f"утечка диагноза владельца в промпте анализа: {leak}"
