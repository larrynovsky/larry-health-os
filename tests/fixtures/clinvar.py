"""
tests/fixtures/clinvar.py — JSON-фикстуры и mock для ClinVar/MyVariant.info API.

`genome_annotator.py` и `genome_update_agent.py` используют MyVariant.info
для аннотации SNP и проверки изменений значимости. Mock возвращает
заранее заскриптованные ответы для конкретных rsid.

Использование:

    def test_genome_update_upward_movement(clinvar_mock):
        clinvar_mock.set("rs4680", significance="risk factor")
        clinvar_mock.set("rs6265", significance="Pathogenic")  # было Benign
        # ... запускаем genome_update_agent
        assert clinvar_mock.calls
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pytest


# ── Эталонные SNP из FUNCTIONAL_WHITELIST ───────────────────────────────────

REFERENCE_SNPS = {
    "rs4680": {
        "gene": "COMT", "genotype": "GG",
        "significance": "risk factor",
        "clinical_summary": "Val158Val, высокий клиренс дофамина (синтетика)",
    },
    "rs6265": {
        "gene": "BDNF", "genotype": "CT",
        "significance": "Benign",
        "clinical_summary": "Val66Met, синтетический генотип",
    },
    "rs1800497": {
        "gene": "ANKK1", "genotype": "AG",
        "significance": "risk factor",
        "clinical_summary": "Taq1A A1/A2, синтетический генотип",
    },
    "rs1801133": {
        "gene": "MTHFR", "genotype": "CC",
        "significance": "risk factor",
        "clinical_summary": "677CC, синтетический генотип",
    },
}


@dataclass
class ClinVarMock:
    """Mock ответов ClinVar/MyVariant API."""
    answers: dict = field(default_factory=lambda: dict(REFERENCE_SNPS))
    calls: list = field(default_factory=list)
    fail_mode: Optional[str] = None  # "timeout" | "schema_drift" | "404"

    def set(self, rsid: str, **fields) -> None:
        """Установить/переопределить ответ для rsid."""
        existing = self.answers.get(rsid, {})
        existing.update(fields)
        self.answers[rsid] = existing

    def remove(self, rsid: str) -> None:
        self.answers.pop(rsid, None)

    def get(self, rsid: str) -> dict:
        """Имитация одного API-запроса."""
        self.calls.append(rsid)
        if self.fail_mode == "timeout":
            raise TimeoutError("ClinVar API timeout (mock)")
        if self.fail_mode == "404":
            return {}
        if self.fail_mode == "schema_drift":
            # Имитация изменения схемы — отсутствует expected поле
            return {"_unknown_format": True}
        return self.answers.get(rsid, {})

    def batch_get(self, rsids: list[str]) -> dict[str, dict]:
        return {r: self.get(r) for r in rsids}


@pytest.fixture
def clinvar_mock() -> ClinVarMock:
    return ClinVarMock()
