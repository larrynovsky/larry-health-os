"""SX-19: integration-тест full literature pipeline через моки.

Сценарий:
    1. pubmed_searcher (мок pubmed_client) → agent_reports[literature_search]
    2. publication_reader (мок Anthropic) → agent_reports[publication_reading]
    3. literature_curator (мок Anthropic + cbcr_hypothesis) → действие

Проверяется реальная цепочка через БД между модулями, без LLM/HTTP вызовов.
"""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest
import yaml

pytestmark = pytest.mark.integration


@pytest.mark.timeout(30)
def test_searcher_reader_curator_e2e_keeps_relevant_and_creates_task(
    db, tmp_path, monkeypatch, anthropic_mock
):
    # 1. Topics config
    topics = tmp_path / "topics.yaml"
    topics.write_text(yaml.safe_dump({
        "topics": [{"id": "test_t", "name": "T",
                    "pubmed_query": "x", "relevance_window_years": 3,
                    "priority": "high", "domain": "test"}]
    }))
    import pubmed_searcher as ps
    monkeypatch.setattr(ps, "TOPICS_PATH", topics)

    # 2. Запуск searcher с мок-pubmed_client
    fake_results = [
        {"pmid": "INT_E2E_1", "title": "Survivorship X reduces Y after resection X",
         "year": "2024", "journal": "Annals Surg", "abstract": "Findings show..."},
    ]
    with patch("pubmed_client.search_pubmed", return_value=fake_results):
        s_result = ps.run()
    assert s_result["new_pmids_count"] == 1

    # 3. Reader: triage=True, analyze=structured
    # ВАЖНО: matchers должны быть уникальны для каждой стадии. Раньше analyze
    # matcher "claim + population" ложно срабатывал на DECISION_PROMPT curator,
    # т.к. там тоже есть Claim: / Population: поля. Используем уникальные строки.
    anthropic_mock.script(
        match="ОТНОСИТСЯ",  # только в TRIAGE_PROMPT_TEMPLATE
        response=json.dumps({"relevant": True, "reason": "matches"}),
    )
    anthropic_mock.script(
        match="медицинский аналитик",  # только в ANALYSIS_PROMPT_TEMPLATE
        response=json.dumps({
            "claim": "Intervention reduces dumping",
            "population": "post-resection", "evidence_level": "rct",
            "applicability_to_me": "direct",
            "relevance_assessment": "directly relevant to your population",
        }),
    )
    import publication_reader as pr
    r_result = pr.run(max_per_run=5)
    assert r_result["kept"] == 1
    assert r_result["dismissed"] == 0

    # 4. Curator: решение = task (sdat FE-1)
    anthropic_mock.script(
        match="curator медицинской литературы",  # только в DECISION_PROMPT
        response=json.dumps({
            "action": "task", "rationale": "applies directly",
            "summary": "Sdat FE-1 fecal elastase",
            "linked_problem_id": None,
            "task_type": "lab_test", "task_deadline_days": 14,
        }),
    )
    import literature_curator as lc
    c_result = lc.run(max_per_run=5)
    assert c_result["actions"].get("task", 0) == 1

    # 5. Проверяем end-to-end: задача создана и связана с PMID
    rows = db.fetchall(
        "SELECT id, content, source FROM tasks WHERE source = 'literature_curator'"
    )
    assert len(rows) >= 1
    assert any("INT_E2E_1" in r["content"] for r in rows)


@pytest.mark.timeout(30)
def test_searcher_dedup_blocks_repeat_pmid_in_second_pass(
    db, tmp_path, monkeypatch
):
    topics = tmp_path / "topics.yaml"
    topics.write_text(yaml.safe_dump({
        "topics": [{"id": "dedup_t", "name": "T",
                    "pubmed_query": "x", "relevance_window_years": 3,
                    "priority": "medium", "domain": "test"}]
    }))
    import pubmed_searcher as ps
    monkeypatch.setattr(ps, "TOPICS_PATH", topics)
    fake = [{"pmid": "DEDUP_1", "title": "X", "year": "2024",
              "journal": "J", "abstract": "A"}]
    with patch("pubmed_client.search_pubmed", return_value=fake):
        ps.run()
        result2 = ps.run()
    assert result2["new_pmids_count"] == 0
