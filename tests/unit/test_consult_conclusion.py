"""Заключение приёма — вывод документа без идентификаторов человека, а не его шапка (27.09).

Синтетический документ той же формы, что бланки клиник: шапка с адресом и идентификаторами,
затем находки, затем вывод. Числа и имена выдуманы.
"""
import import_all

DOC = """Example Hospital - Imaging Institute
10 Main St., Sometown
Phone: 00-0000000
Patient: DOE JOHN, ID: 123456789, Date of Birth: 01/01/1970
Findings
Liver without focal lesions.
Summary
No evidence of active disease. Follow-up in 6 months.
Patient ID: 123456789
"""


def test_conclusion_starts_at_summary_and_drops_identity():
    out = import_all.consult_conclusion(DOC)
    assert out.startswith("Summary No evidence of active disease")
    assert "123456789" not in out and "Date of Birth" not in out and "Hospital" not in out


def test_without_sections_identity_still_dropped():
    out = import_all.consult_conclusion("Clinic X\nName: John Doe\nDOB 01/01/1970\nStable course.\n")
    assert out == "Clinic X Stable course."
