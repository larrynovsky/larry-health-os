"""Датчик единого чокпоинта lab_reconcile.

Проверяет: (1) кандидаты = не-lab доки (lab исключён), (2) дедуп по content-hash
(already_recognized → skipped), (3) dry не тратит vision. Vision/API не вызываются
(замоканы resolve/sha/recognized)."""
from pathlib import Path

import lab_backfill
import lab_reconcile


def test_reconcile_filters_non_lab_and_dedup(db, monkeypatch):
    with db.conn() as c:
        c.execute("INSERT INTO imported_docs(source_file, doc_type) VALUES('CR/lab.pdf','lab')")
        c.execute("INSERT INTO imported_docs(source_file, doc_type) VALUES('CR/photo1.jpeg','general_medical')")
        c.execute("INSERT INTO imported_docs(source_file, doc_type) VALUES('CR/photo2.jpeg','')")
        c.commit()

    monkeypatch.setattr(lab_backfill, "resolve_document", lambda sf: Path("/fake/") / Path(sf).name)
    monkeypatch.setattr(lab_backfill, "_file_sha256", lambda p: "sha_" + p.name)
    # photo1 уже распознан, photo2 — новый
    monkeypatch.setattr(lab_backfill, "already_recognized", lambda sha: sha == "sha_photo1.jpeg")

    s = lab_reconcile.reconcile(dry=True)
    assert s["scanned"] == 2       # lab-док исключён из кандидатов
    assert s["skipped_dup"] == 1   # photo1 уже распознан
    assert s["new"] == 1           # photo2 новый → пошёл бы в vision
    assert s["rows"] == 0          # dry: vision не гонялся


def test_reconcile_missing_file_counted(db, monkeypatch):
    with db.conn() as c:
        c.execute("INSERT INTO imported_docs(source_file, doc_type) VALUES('CR/gone.jpeg','general_medical')")
        c.commit()
    monkeypatch.setattr(lab_backfill, "resolve_document", lambda sf: None)  # файл не найден
    s = lab_reconcile.reconcile(dry=True)
    assert s["scanned"] == 1
    assert s["missing"] == 1
    assert s["new"] == 0
