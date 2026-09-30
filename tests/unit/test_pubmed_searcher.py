"""pubmed_searcher — sweep по темам с дедупликацией (SX-17a)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

pytestmark = pytest.mark.unit


def _setup_topics(tmp_path, monkeypatch):
    cfg = tmp_path / "survivorship_topics.yaml"
    cfg.write_text(yaml.safe_dump({
        "topics": [
            {"id": "test_topic_1", "name": "Test 1",
             "pubmed_query": "cancer survivor", "relevance_window_years": 3,
             "priority": "high", "domain": "test"}
        ]
    }))
    import pubmed_searcher as ps
    monkeypatch.setattr(ps, "TOPICS_PATH", cfg)
    return ps


def test_searcher_saves_candidates_to_agent_reports(db, tmp_path, monkeypatch):
    ps = _setup_topics(tmp_path, monkeypatch)
    fake = [{"pmid": "12345", "title": "T", "year": "2024",
             "journal": "J", "abstract": "A"}]
    with patch("pubmed_client.search_pubmed", return_value=fake):
        result = ps.run()
    assert result["candidates_total"] == 1
    rows = db.fetchall("SELECT pubmed_ids FROM agent_reports "
                       "WHERE agent_type='literature_search'")
    assert len(rows) >= 1


def test_searcher_dedup_skips_already_seen(db, tmp_path, monkeypatch):
    ps = _setup_topics(tmp_path, monkeypatch)
    fake = [{"pmid": "12345", "title": "T", "year": "2024",
             "journal": "J", "abstract": "A"}]
    with patch("pubmed_client.search_pubmed", return_value=fake):
        ps.run()
        result2 = ps.run()
    # Второй прогон не должен создать новых candidates
    assert result2["new_pmids_count"] == 0


def test_source_down_writes_no_false_empty_report(db, tmp_path, monkeypatch):
    """27.09: NCBI отвечал 500 на все темы, а отчёт «0 новых» сохранялся как успех.

    Пустота и отказ источника неотличимы, пока клиент глотает ошибку. Теперь отказ всех тем
    не пишет отчёт — пропуск назовёт датчик свежести. Мутация: убрать strict=True → клиент
    вернёт [] и отчёт запишется → тест краснеет.
    """
    ps = _setup_topics(tmp_path, monkeypatch)
    import requests

    def _down(*a, **k):
        raise requests.HTTPError("500 Server Error")
    monkeypatch.setattr("pubmed_client.requests.get", _down)
    result = ps.run()
    assert result["topics_failed"] == 1
    assert db.fetchall("SELECT 1 FROM agent_reports WHERE agent_type='literature_search'") == []
