"""
UC-A-07 — Финансовые документы не попадают в медицинские данные.

Источник: USE_CASES.md UC-A-07.
Покрывается фильтром `is_financial` в `import_all.py`.
Status: `implemented`.

Этот UC по существу дублирует часть проверок UC-A-02; здесь мы делаем
явный smoke test, что ни один payment-документ не классифицируется
как медицинский.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

from import_all import is_financial


PAYMENT_NAMES = [
    "claim_form_2024.pdf", "invoice_2024.pdf", "receipt_pharmacy.pdf",
    "f&d_payment.pdf", "поручение_оплаты.pdf", "clinic_invoice.pdf",
    "surgicare quotation.pdf", "чек_2024.pdf",
]


@pytest.mark.parametrize("name", PAYMENT_NAMES)
def test_payment_name_is_filtered(name: str):
    assert is_financial(Path(f"/Users/zz/health/CR/{name}")) is True


@pytest.mark.parametrize("name", [
    "lab_eng_2026.pdf", "Ivanov_2026.pdf", "PET-CT_scan.pdf",
    "biopsy_report.pdf", "gastroscopy_report.pdf", "discharge_summary.pdf",
])
def test_medical_name_not_financial(name: str):
    assert is_financial(Path(f"/Users/zz/health/CR/{name}")) is False
