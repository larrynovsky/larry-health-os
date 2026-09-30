"""BL-CONSULT-SECOND-HOME-1: у приёма врача один дом — медкарта (events + encounters).

Таблица consultations — архив; код её не читает и не пишет (миграция снята 27.09). Читатели
промптов (gp_context, consult_prep, survivorship) видят приёмы медкарты, в том числе те,
которых в копии не было, а одна специальность под разными словами сводится в одну.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def test_only_migration_touches_the_archive_table():
    hits = []
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", "plans/", ".")) or "/." in rel:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if re.search(r"\b(FROM|INTO|UPDATE)\s+consultations\b", line):
                hits.append(f"{rel}:{i}")
    assert hits == [], hits


def test_encounter_without_archive_row_is_visible(db):
    import health_db
    with db.conn() as c:
        cur = c.execute("INSERT INTO events (event_type, effective_date, status, performer) "
                        "VALUES ('encounter', '2025-03-18', 'completed', 'Dr X')")
        c.execute("INSERT INTO encounters (event_id, specialty, assessment) VALUES (?, 'Oncologist', 'ok')",
                  (cur.lastrowid,))
    assert health_db.get_last_consultation()["date"] == "2025-03-18"


def test_specialty_spellings_are_one(db):
    import health_db
    health_db.save_consultation("2024-02-14", "oncologist", key_findings="old")
    health_db.save_consultation("2026-03-12", "oncology", key_findings="new")
    health_db.save_consultation("2026-06-01", "imaging", key_findings="other")
    last = health_db.get_last_consultation(specialist_type="oncologist")
    assert last["date"] == "2026-03-12"
    assert health_db.consultation_exists("2026-03-12", "Oncologist")
    assert not health_db.consultation_exists("2026-03-12", "imaging")


@pytest.mark.parametrize("text,fname,want", [
    # дата рождения стоит в тексте первой — не дата документа
    ("Patient: X, Date of Birth: 01/02/1960\nthe test was performed on: 17/03/2022", "PET.pdf", "2022-03-17"),
    # полная дата в имени файла сильнее первой даты текста (там дата операции)
    ("Surgery on 04/10/2021 ... 11/10/21", "Discharge 11.10.21.pdf", "2021-10-11"),
    # в имени только месяц — берётся подписанная дата исследования
    ("Date of Birth: 01/02/1960 Date of Examination: 19/11/2024", "PET 11.2024.pdf", "2024-11-19"),
    ("Date: 03.05.2024 anything", "x 01.01.2020.pdf", "2024-05-03"),
    ("DOB: 01/02/1960 only", "PET 07.2023.pdf", "2023-07-01"),
])
def test_parse_date_skips_birth_and_prefers_filename(text, fname, want):
    import import_all
    assert import_all.parse_date(text, fname) == want
