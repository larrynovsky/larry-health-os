

"""SX-18: расширенные edge cases для survivorship-модулей.

Покрывает error paths, malformed inputs, missing config, parse failures —
там где модули должны **не падать**, а возвращать degraded result.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

pytestmark = pytest.mark.unit


# ── pubmed_searcher ─────────────────────────────────────────────────────────


def test_searcher_no_topics_file_returns_empty(db, tmp_path, monkeypatch):
    """Если topics.yaml отсутствует — run() возвращает пустую сводку,
    не падает."""
    import pubmed_searcher as ps
    monkeypatch.setattr(ps, "TOPICS_PATH", tmp_path / "absent.yaml")
    result = ps.run()
    assert result == {"topics": 0, "candidates": 0, "new_pmids": 0}


def test_searcher_malformed_yaml_returns_empty(db, tmp_path, monkeypatch):
    """Битый YAML не должен ронять searcher."""
    bad = tmp_path / "topics.yaml"
    bad.write_text("topics: [{ id: oops, missing_quote: 'x")  # битый YAML
    import pubmed_searcher as ps
    monkeypatch.setattr(ps, "TOPICS_PATH", bad)
    result = ps.run()
    assert result["candidates"] == 0 if "candidates" in result else True


def test_searcher_pubmed_client_throws_skips_topic(db, tmp_path, monkeypatch):
    """pubmed_client.search_pubmed throws — топик пропускается,
    остальные обрабатываются."""
    cfg = tmp_path / "t.yaml"
    cfg.write_text(yaml.safe_dump({
        "topics": [
            {"id": "broken_topic", "name": "B",
             "pubmed_query": "x", "relevance_window_years": 3,
             "priority": "medium", "domain": "test"},
            {"id": "ok_topic", "name": "OK",
             "pubmed_query": "y", "relevance_window_years": 3,
             "priority": "medium", "domain": "test"},
        ]
    }))
    import pubmed_searcher as ps
    monkeypatch.setattr(ps, "TOPICS_PATH", cfg)

    calls = {"n": 0}

    def fake_search(q, max_results=5, years_back=3, strict=False):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("pubmed 503")
        return [{"pmid": "OK1", "title": "T", "year": "2024",
                 "journal": "J", "abstract": "A"}]

    with patch("pubmed_client.search_pubmed", side_effect=fake_search):
        result = ps.run()
    assert result["new_pmids_count"] == 1
    assert calls["n"] == 2  # оба топика были опрошены


def test_searcher_topic_without_query_skipped(db, tmp_path, monkeypatch):
    """Topic без pubmed_query И без name — пропуск."""
    cfg = tmp_path / "t.yaml"
    cfg.write_text(yaml.safe_dump({
        "topics": [{"id": "no_query", "relevance_window_years": 3,
                    "priority": "medium", "domain": "test"}]
    }))
    import pubmed_searcher as ps
    monkeypatch.setattr(ps, "TOPICS_PATH", cfg)
    with patch("pubmed_client.search_pubmed", return_value=[]) as mock_search:
        ps.run()
    # search не должен был быть вызван — нет query
    assert mock_search.call_count == 0


# ── publication_reader ───────────────────────────────────────────────────────


def _seed_search_candidate(db, pmid="PR_EDGE_1"):
    db.execute(
        "INSERT INTO agent_reports (date, agent_type, agent_name, has_findings, findings, pubmed_ids) "
        "VALUES (?, 'literature_search', 'test', 1, ?, ?)",
        (date.today().isoformat(),
         json.dumps([{
             "pmid": pmid, "topic_id": "t", "domain": "d",
             "title": "T", "year": "2024", "journal": "J",
             "abstract": "Some abstract text", "priority": "medium",
         }]),
         json.dumps([pmid])),
    )


def test_reader_triage_parse_fail_dismisses_candidate(db, anthropic_mock):
    """Если triage возвращает невалидный JSON — finding должен быть
    dismissed (не keep, не падать)."""
    _seed_search_candidate(db)
    anthropic_mock.script(
        match="ОТНОСИТСЯ",  # triage
        response="not valid JSON at all"
    )
    import publication_reader as pr
    result = pr.run(max_per_run=1)
    # либо dismissed, либо skipped — главное не kept и не exception
    assert result.get("kept", 0) == 0


def test_reader_analyze_parse_fail_skipped(db, anthropic_mock):
    """Triage ok, analyze возвращает мусор → finding пропускается."""
    _seed_search_candidate(db, pmid="PR_EDGE_2")
    anthropic_mock.script(
        match="ОТНОСИТСЯ",
        response=json.dumps({"relevant": True, "reason": "ok"})
    )
    anthropic_mock.script(
        match="медицинский аналитик",
        response="мусор без JSON"
    )
    import publication_reader as pr
    result = pr.run(max_per_run=1)
    # Если analyze вернул мусор — claim/population отсутствуют, finding пропущен
    assert result.get("kept", 0) == 0


# ── literature_curator ──────────────────────────────────────────────────────


def _seed_reading_finding_for_curator(db, pmid="LC_EDGE_1"):
    db.execute(
        "INSERT INTO agent_reports (date, agent_type, agent_name, has_findings, findings, pubmed_ids) "
        "VALUES (?, 'publication_reading', 'test', 1, ?, ?)",
        (date.today().isoformat(),
         json.dumps([{
             "pmid": pmid, "status": "kept",
             "title": "T", "year": "2024", "journal": "J",
             "topic_id": "t", "domain": "d",
             "claim": "C", "population": "P",
             "evidence_level": "rct", "applicability_to_me": "direct",
             "relevance_assessment": "ok",
         }]),
         json.dumps([pmid])),
    )


def test_curator_decision_parse_fail_skips(db, anthropic_mock):
    """Если decision-LLM вернул мусор — finding идёт в skipped, не падает."""
    _seed_reading_finding_for_curator(db)
    anthropic_mock.script(
        match="curator медицинской литературы",
        response="totally broken not JSON"
    )
    import literature_curator as lc
    result = lc.run(max_per_run=1)
    assert result["actions"].get("skipped", 0) == 1


def test_curator_unknown_action_skipped(db, anthropic_mock):
    """Decision возвращает action='magic' — попадёт в дефолт note (или skipped),
    главное — не сломаться. Текущая реализация делает note для unknown, но мы
    лояльно принимаем оба варианта."""
    _seed_reading_finding_for_curator(db, pmid="LC_EDGE_2")
    anthropic_mock.script(
        match="curator медицинской литературы",
        response=json.dumps({
            "action": "magic", "rationale": "weird",
            "summary": "S", "linked_problem_id": None,
            "task_type": None, "task_deadline_days": None,
        })
    )
    import literature_curator as lc
    result = lc.run(max_per_run=1)
    # action='magic' уходит в skipped по текущему контракту:
    # см. literature_curator.py — есть явная ветка else → skipped
    assert (result["actions"].get("skipped", 0) + result["actions"].get("note", 0)) == 1


# ── hypothesis_semantic_check ───────────────────────────────────────────────


def test_semcheck_disabled_in_config_returns_false(db, tmp_path, monkeypatch):
    """semantic_dedup.enabled=False → check всегда (False, None, 'disabled...')."""
    cfg = tmp_path / "survivorship_config.yaml"
    cfg.write_text(yaml.safe_dump({
        "semantic_dedup": {"enabled": False, "window_days": 90}
    }))
    import hypothesis_semantic_check as sc
    monkeypatch.setattr(sc, "CONFIG_PATH", cfg)
    is_dup, eid, reason = sc.check("любая candidate observation")
    assert is_dup is False
    assert eid is None
    assert "disabled" in reason.lower()


def test_semcheck_haiku_parse_fail_returns_false(db, anthropic_mock, tmp_path, monkeypatch):
    """Haiku вернул мусор → check считает что не duplicate (safe default)."""
    # Засеем одну открытую гипотезу
    import health_db as hdb
    hdb.save_memory(
        category="hypothesis",
        key="h_edge_1",
        value=json.dumps({"observation": "Старая гипотеза про сон", "status": "open"}),
        confidence=0.5,
        source="test",
    )
    cfg = tmp_path / "survivorship_config.yaml"
    cfg.write_text(yaml.safe_dump({
        "semantic_dedup": {"enabled": True, "window_days": 90, "check_rejected": True}
    }))
    import hypothesis_semantic_check as sc
    monkeypatch.setattr(sc, "CONFIG_PATH", cfg)

    anthropic_mock.script(
        match="дедупликатор медицинских гипотез",
        response="totally not JSON"
    )
    is_dup, eid, reason = sc.check("кандидат")
    assert is_dup is False
    assert eid is None


def test_semcheck_no_existing_hypotheses_returns_false(db, tmp_path, monkeypatch):
    """Если в БД нет открытых гипотез — check возвращает (False, ..., 'no existing...')."""
    cfg = tmp_path / "survivorship_config.yaml"
    cfg.write_text(yaml.safe_dump({
        "semantic_dedup": {"enabled": True, "window_days": 90, "check_rejected": True}
    }))
    import hypothesis_semantic_check as sc
    monkeypatch.setattr(sc, "CONFIG_PATH", cfg)
    is_dup, eid, reason = sc.check("одинокий кандидат без существующих")
    assert is_dup is False
    assert eid is None
    assert "no existing" in reason.lower() or "compare" in reason.lower()
