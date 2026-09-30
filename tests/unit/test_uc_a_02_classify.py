"""
UC-A-02 — Не-лаб PDF классифицируется в правильный домен и не запускает Council.

Источник: USE_CASES.md §3.A → UC-A-02.
Реализация: `import_all.py:classify` + `is_financial`.
Status: `partial` · Confirmation: `proposed`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

from import_all import classify, is_financial


def _p(name: str) -> Path:
    return Path("/tmp/health/CR") / name


@pytest.fixture(autouse=True)
def _synthetic_doctor_pattern(monkeypatch):
    """Врач-паттерн тенанта — синтетический (до 2026-09-23 тест зеленел на НАСТОЯЩЕЙ фамилии
    онколога владельца из БД снапшота — §20: зелёный от окружения)."""
    import import_all
    pat = {"pattern": "drivanov", "doc_type": "oncology_visit", "match_on": "filename", "match_type": "literal"}
    monkeypatch.setattr(import_all, "_DOC_PATTERNS", [pat] + list(import_all._DOC_PATTERNS))


# ── Классификация по имени ──────────────────────────────────────────────────


def test_lab_classified_as_lab():
    """Имя содержит `lab eng` → "lab" (по name pattern в classify)."""
    assert classify(_p("lab eng 2026-05-01.pdf"), "") == "lab"


def test_lab_classified_by_content():
    """Имя без 'lab', но текст содержит 'laboratory tests' → "lab"."""
    assert classify(_p("results.pdf"), "Laboratory Tests Report 2026") == "lab"


def test_oncology_visit_by_tenant_doctor_pattern():
    assert classify(_p("DrIvanov_visit_2026-04-20.pdf"), "") == "oncology_visit"


def test_pet_imaging():
    assert classify(_p("PET-CT_scan.pdf"), "") == "imaging_pet"


def test_pathology():
    assert classify(_p("pathology_report.pdf"), "") == "pathology"


def test_biopsy_russian_name():
    assert classify(_p("биопсия_заключение.pdf"), "") == "biopsy"


def test_endoscopy_gastroscopy():
    assert classify(_p("gastroscopy_report.pdf"), "") == "endoscopy"


def test_discharge():
    assert classify(_p("discharge_summary.pdf"), "") == "discharge"


def test_genetic_msi():
    assert classify(_p("MSI_report.pdf"), "") == "genetic"


def test_general_medical_fallback():
    """Неизвестный медицинский PDF → general_medical."""
    assert classify(_p("unknown_medical.pdf"), "что-то медицинское") == "general_medical"


# ── Financial filter ────────────────────────────────────────────────────────


def test_is_financial_claim_form():
    assert is_financial(_p("claim_form_2024.pdf")) is True


def test_is_financial_invoice():
    assert is_financial(_p("invoice_clinic.pdf")) is True


def test_is_financial_receipt():
    assert is_financial(_p("receipt_pharmacy.pdf")) is True


def test_payments_path_excluded():
    """Файл в /Payments/ без медицинских ключевых слов → financial."""
    p = Path("/Users/zz/health/Payments/something.pdf")
    assert is_financial(p) is True


def test_payments_path_with_medical_keyword_kept():
    """Файл в /Payments/ с медицинским keyword — не financial."""
    p = Path("/Users/zz/health/Payments/pathology_scan.pdf")
    assert is_financial(p) is False


def test_normal_medical_not_financial():
    assert is_financial(_p("lab_2026-05-01.pdf")) is False


def test_classify_returns_one_of_known_types():
    """Property-style: classify всегда возвращает строку из known set."""
    known = {"lab", "oncology_visit", "imaging_pet", "pathology", "biopsy",
             "endoscopy", "discharge", "procedure", "medication",
             "nutrition_guide", "genetic", "hospital_bill", "general_medical"}
    samples = [
        ("anything.pdf", ""),
        ("DrIvanov_visit.pdf", ""),
        ("lab eng.pdf", ""),
        ("random.pdf", "discharge text"),
    ]
    for name, text in samples:
        cls = classify(Path(name), text)
        assert cls in known, f"unknown class={cls} for {name}"
