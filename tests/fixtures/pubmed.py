"""
tests/fixtures/pubmed.py — mock PubMed E-utilities API.

`pubmed_client.py` верифицирует PMID, упомянутые в специалист/GP отчётах
(TC §2: has_findings=1 → каждый PMID должен существовать). Mock возвращает
фиктивные но валидные структуры для конкретных PMID.

Использование:

    def test_specialist_pmid_verification(pubmed_mock):
        pubmed_mock.add(38123456, title="Adenocarcinoma management")
        pubmed_mock.add(38234567, title="HRV in chemotherapy")
        # ... запускаем pubmed_client.verify_pmids([38123456, 38234567])
        assert pubmed_mock.calls
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest


@dataclass
class PubMedMock:
    articles: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)
    fail_mode: str | None = None  # "timeout" | "404" | "schema_drift"

    def add(self, pmid: int | str, title: str, authors: list[str] = None,
            year: int = 2024, journal: str = "Mock Journal") -> None:
        self.articles[str(pmid)] = {
            "PMID": str(pmid),
            "Article": {
                "ArticleTitle": title,
                "AuthorList": [{"LastName": a} for a in (authors or ["Mock"])],
                "Journal": {"Title": journal,
                             "JournalIssue": {"PubDate": {"Year": str(year)}}},
            },
        }

    def fetch(self, pmid: int | str) -> dict:
        self.calls.append(("fetch", str(pmid)))
        if self.fail_mode == "timeout":
            raise TimeoutError("PubMed E-utilities timeout (mock)")
        if self.fail_mode == "404":
            return {}
        if self.fail_mode == "schema_drift":
            return {"_unknown": True}
        return self.articles.get(str(pmid), {})

    def search(self, query: str) -> list[str]:
        self.calls.append(("search", query))
        return [pmid for pmid, art in self.articles.items()
                if query.lower() in art.get("Article", {}).get("ArticleTitle", "").lower()]


@pytest.fixture
def pubmed_mock() -> PubMedMock:
    return PubMedMock()
