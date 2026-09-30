#!/usr/bin/env python3.11
"""
Создаёт синтетический Synevo PDF для end-to-end теста import_coordinator.

Содержит:
  KNOWN:   Glucose, Mg, Troponin T hs, Hemoglobin, Leucocytes, Ferritin
  UNKNOWN: XYZMARKER123, HomocysteineNew  (не в aliases → должны попасть в pending_field_reviews)
"""
from pathlib import Path
import fitz  # PyMuPDF

# Georgian characters (достаточно чтобы is_synevo_format() вернул True)
GEO = "სინევო ლაბორატორია"

LINES = [
    f"SYNEVO - {GEO}",
    "Laboratory Results 2026-06-01",
    "",
    # Pattern A: value \n unit \n ref_low  ref_high \n Name \\ Georgian
    "5.20",
    "mmol/L",
    "3.9  6.1",
    "Glucose \\ გლუკოზა",
    "",
    "2.41",
    "mg/dL",
    "1.6  2.6",
    "Mg \\ მაგნიუმი",
    "",
    "0.008",
    "ng/mL",
    "0.000  0.014",
    "Troponin T hs \\ ტროპონინი T",
    "",
    "87",
    "pg/mL",
    "0  125",
    "NT-proBNP \\ NT-pro-BNP",
    "",
    "15.8",
    "g/dL",
    "13.0  17.0",
    "Hemoglobin \\ ჰემოგლობინი",
    "",
    "7.2",
    "10^9/L",
    "4.0  10.0",
    "Leucocytes \\ ლეიკოციტები",
    "",
    "28.5",
    "ng/mL",
    "30.0  400.0",
    "Ferritin \\ ფერიტინი",
    "",
    # UNKNOWN fields (not in lab_name_aliases)
    "42.7",
    "U/mL",
    "10.0  50.0",
    "XYZMARKER123 \\ უცნობი",
    "",
    "9.1",
    "micromol/L",
    "5.0  15.0",
    "HomocysteineNew \\ ჰომოცისტეინი",
]

doc = fitz.open()
page = doc.new_page()

# Вставляем каждую строку отдельным text-блоком (имитируем Synevo block layout)
y = 50
for line in LINES:
    if line:
        page.insert_text((50, y), line, fontsize=9)
    y += 14

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import infra_config
out = infra_config.cloud_dir("CR", "TEST_synevo_sample.PDF")
doc.save(str(out))
print(f"Saved: {out}")
print(f"Pages: {len(doc)}")
print(f"Known fields (in aliases): Glucose, Mg, Troponin T hs, Hemoglobin, Leucocytes, Ferritin")
print(f"Unknown fields (not in aliases): XYZMARKER123, HomocysteineNew")
