"""W5A-D1: tests для _check_phantom_pubmed detector."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest
import cbcr_hypothesis as ch

pytestmark = pytest.mark.unit


def test_pubmed_source_without_pmid_flagged():
    """source=pubmed без PMID → critical_issue."""
    hyp = {"evidence_for": [
        {"source": "pubmed", "fact": "Post-chemo fatigue 30%", "weight": "moderate"},
    ]}
    issues = ch._check_phantom_pubmed(hyp)
    assert len(issues) == 1
    assert "phantom" in issues[0].lower()


def test_literature_source_without_pmid_flagged():
    """source с 'literature' → тоже phantom."""
    hyp = {"evidence_for": [
        {"source": "pubmed / clinical_knowledge",
         "fact": "Exampliplatin neurotoxicity 6-18 months", "weight": "supporting"},
    ]}
    issues = ch._check_phantom_pubmed(hyp)
    assert len(issues) == 1


def test_pubmed_source_with_pmid_ok():
    """source=pubmed + PMID:12345 в fact → не flagged."""
    hyp = {"evidence_for": [
        {"source": "pubmed", "fact": "Post-chemo fatigue 30% (PMID:12345678)", "weight": "strong"},
    ]}
    assert ch._check_phantom_pubmed(hyp) == []


def test_non_pubmed_source_ok():
    """source=oura.daily_metrics → never flagged даже без PMID."""
    hyp = {"evidence_for": [
        {"source": "oura.daily_metrics", "fact": "deep sleep −18.9%", "weight": "strong"},
        {"source": "lab_results", "fact": "TSH 2.1", "weight": "moderate"},
    ]}
    assert ch._check_phantom_pubmed(hyp) == []


def test_empty_evidence_for_ok():
    """Нет evidence_for → нет issues."""
    assert ch._check_phantom_pubmed({}) == []
    assert ch._check_phantom_pubmed({"evidence_for": []}) == []


def test_mixed_evidence_partial_flag():
    """Из 3 evidence — только 1 phantom → 1 issue."""
    hyp = {"evidence_for": [
        {"source": "oura.daily_metrics", "fact": "data", "weight": "strong"},
        {"source": "pubmed", "fact": "claim without pmid", "weight": "moderate"},
        {"source": "pubmed", "fact": "claim PMID:99999", "weight": "moderate"},
    ]}
    issues = ch._check_phantom_pubmed(hyp)
    assert len(issues) == 1
    assert "[1]" in issues[0]
