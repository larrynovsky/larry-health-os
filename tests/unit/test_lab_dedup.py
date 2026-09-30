"""Дедуп распознавания по content-hash (recognized_docs).

Закрывает долг: не переразбирать один и тот же файл (дорого: vision + API),
даже под другим именем / в новом run. Реестр по sha256 содержимого; --force
переразбирает. recognize/oracles замоканы — тест логики, без API.
"""
import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    try:
        import lab_backfill
    except Exception as e:
        pytest.skip(f"lab_backfill import: {e}")
    monkeypatch.setattr(lab_backfill, "_SHARED_VOCAB_DB", tmp_path / "shared" / "lab_vocab.db")
    return health_db, lab_backfill, tmp_path


def test_recognized_roundtrip(env):
    _, lb, _ = env
    assert lb.already_recognized("deadbeef") is False
    lb._record_recognized("deadbeef", "doc.pdf", "r1")
    assert lb.already_recognized("deadbeef") is True
    lb._record_recognized("deadbeef", "doc.pdf", "r2")   # idempotent
    assert lb.already_recognized("deadbeef") is True


def test_run_backfill_skips_dup_and_force_reparse(env, monkeypatch):
    _, lb, tmp = env
    # Документ ВНУТРИ тенанта (2026-08-09). Раньше он лежал рядом с корнем тенанта, и
    # это проходило только потому, что принадлежность никто не сверял — ровно тот
    # разрыв, который закрыт `_assert_belongs_to_tenant`. В проде бланк всегда лежит
    # в инбоксе тенанта; фикстура приведена к проду, а не гейт ослаблен под фикстуру.
    inbox = tmp / "health" / "incoming"
    inbox.mkdir(parents=True, exist_ok=True)
    f = inbox / "lab.pdf"; f.write_bytes(b"%PDF fake lab content")
    calls = []

    def fake_recognize(path, date, **k):
        calls.append(str(path))
        return {"tests": [], "extractor_version": "v", "source_file": "lab.pdf",
                "date": date, "stats": {"pages": 1, "pass1_count": 0, "pass2_count": 0,
                                        "reconciled": 0, "disagreements": 0, "singles": 0}}

    monkeypatch.setattr(lb.lab_recognizer, "recognize", fake_recognize)
    monkeypatch.setattr(lb.lab_oracles, "verify",
                        lambda tests, **k: {"status": "green", "count": 0, "issues": {}})

    s1 = lb.run_backfill("r1", doc_path=str(f), date="2025-01-01")
    assert len(calls) == 1 and s1["skipped_dup"] == 0          # первый раз — распознан

    s2 = lb.run_backfill("r2", doc_path=str(f), date="2025-01-01")
    assert len(calls) == 1 and s2["skipped_dup"] == 1          # дубль — recognize НЕ вызван

    s3 = lb.run_backfill("r3", doc_path=str(f), date="2025-01-01", force=True)
    assert len(calls) == 2 and s3["skipped_dup"] == 0          # --force переразбирает
