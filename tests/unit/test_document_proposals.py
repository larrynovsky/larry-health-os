"""Заключения врачей любого тенанта → события + предложения под гейт человека (document-intake, 24.09).

До 24.09 разбор заключений читал только iCloud-папку CR/ владельца: у партнёра и постороннего
заключение из бота пропускалось молча. Модель в тестах не зовётся — извлечение подменено.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

TEXT = ("Консультация эндокринолога 15.01.2021.\nЖалобы на утомляемость.\n"
        "Диагноз: Дефицит витамина D, умеренный.\n"
        "Рекомендовано: Холекальциферол 2000 МЕ утром натощак.\nСахарный диабет исключён.")

EXTRACTED = {
    "event_type": "encounter", "effective_date": "2025-03-12", "performer": "Врач",
    "performer_role": "эндокринолог", "assessment": "дефицит витамина D", "plan": None,
    "diagnoses": [
        {"name": "Дефицит витамина D", "quote": "Дефицит   витамина D, умеренный"},
        {"name": "Сахарный диабет 2 типа", "quote": "Диагноз: сахарный диабет 2 типа"},  # выдумка
    ],
    "medications": [{"name": "Холекальциферол", "dose": "2000 МЕ", "quote": "Холекальциферол 2000 МЕ"}],
}


def test_quote_check():
    import import_medical_events as ime
    assert ime._quote_in(TEXT, "дефицит витамина d") is True
    assert ime._quote_in(TEXT, "гипертоническая болезнь") is False
    assert ime._quote_in(TEXT, "ги") is False            # слишком короткая «цитата»
    assert ime._quote_in(None, "что угодно") is None      # снимок: сверять не с чем


@pytest.fixture
def inbox(db, tmp_path, monkeypatch):
    import import_medical_events as ime
    monkeypatch.setattr(ime, "extract_text", lambda p: TEXT)
    monkeypatch.setattr(ime, "llm_extract", lambda name, text: dict(EXTRACTED))
    told = []
    import notify
    monkeypatch.setattr(notify, "notify", lambda m, *a, **k: told.append(m) or "ok")
    d = tmp_path / "tenant" / "incoming"
    (d / "reports").mkdir(parents=True)
    (d / "endo.pdf").write_bytes(b"%PDF-1.4 fake")
    return d, told


def _proposals(db):
    with db.conn() as c:
        return [json.loads(r[0])[0] for r in c.execute(
            "SELECT proposed FROM problem_list_proposals WHERE source='document'")]


def test_incoming_document_becomes_event_and_proposals(db, inbox):
    import import_medical_events as ime
    d, told = inbox
    assert ime.process_incoming(d) == 1
    with db.conn() as c:
        att = [r[0] for r in c.execute("SELECT attachments FROM events")]
        meds = [dict(r) for r in c.execute(
            "SELECT name, confirmation, source, notes FROM medications")]
    # save_event кодирует attachments ещё раз (исторически) — сверяем путь, а не форму строки
    assert len(att) == 1 and "incoming/endo.pdf" in att[0]
    props = _proposals(db)
    assert [p["new_value"]["title"] for p in props] == ["Дефицит витамина D"], \
        "выдуманный диагноз (цитаты нет в тексте) не должен стать предложением"
    assert "incoming/endo.pdf" in props[0]["reason"]
    assert meds and meds[0]["confirmation"] == "proposed" and meds[0]["source"] == "document"
    assert "Холекальциферол 2000 МЕ" in meds[0]["name"]
    with db.conn() as c:                                   # в медкарту — ни строки без человека
        assert c.execute("SELECT COUNT(*) FROM problem_list").fetchone()[0] == 0
    assert ime.process_incoming(d) == 0                    # повторный проход — не платим снова


def test_image_proposal_is_marked_unverified(db, inbox, monkeypatch):
    import import_medical_events as ime
    d, _ = inbox
    (d / "endo.pdf").unlink()
    (d / "reports" / "photo.jpg").write_bytes(b"\xff\xd8fake")
    monkeypatch.setattr(ime, "llm_extract_image", lambda p, n: dict(EXTRACTED))
    monkeypatch.setattr(ime, "_ocr_image", lambda p: None)          # OCR не прочёл / нет tesseract
    assert ime.process_incoming(d) == 1
    reasons = [p["reason"] for p in _proposals(db)]
    assert len(reasons) == 2 and all("не сверена" in r for r in reasons)


def test_image_quotes_checked_against_ocr(db, inbox, monkeypatch):
    """Снимок: цитаты модели сверяются с текстом tesseract — отдельным от модели путём (§17)."""
    import import_medical_events as ime
    d, _ = inbox
    (d / "endo.pdf").unlink()
    (d / "reports" / "photo.jpg").write_bytes(b"\xff\xd8fake")
    monkeypatch.setattr(ime, "llm_extract_image", lambda p, n: dict(EXTRACTED))
    monkeypatch.setattr(ime, "_ocr_image", lambda p: TEXT)
    assert ime.process_incoming(d) == 1
    props = {p["new_value"]["title"]: p["reason"] for p in _proposals(db)}
    # подтверждённое OCR — без пометки; не подтверждённое — НЕ отброшено (OCR сам ошибается),
    # а помечено для человека
    assert "не сверена" not in props["Дефицит витамина D"] and "НЕ подтверждена" not in props["Дефицит витамина D"]
    assert "НЕ подтверждена распознаванием" in props["Сахарный диабет 2 типа"]


def test_failure_is_loud_once_and_not_retried(db, inbox, monkeypatch):
    import import_medical_events as ime
    d, told = inbox
    calls = []
    monkeypatch.setattr(ime, "llm_extract", lambda n, t: calls.append(n) or {})
    ime.process_incoming(d)
    ime.process_incoming(d)
    assert calls == ["endo.pdf"], "провал не должен повторно оплачиваться каждым проходом"
    assert (d / "endo.pdf.events.failed").exists()
    assert len(told) == 1 and "endo.pdf" in told[0]


def test_empty_key_is_waiting_not_failed(db, inbox, monkeypatch):
    """Нить lab-intake-retry (05.10): пустой баланс поставщика — не провал документа. Мутации:
    писать .events.failed (навсегда), говорить человеку (это делает разбор анализов, один раз),
    повторять каждый проход."""
    import import_medical_events as ime
    d, told = inbox
    calls = []

    class Quota(Exception):
        status_code = 429

    def empty(n, t):
        calls.append(n)
        raise Quota("insufficient_quota")
    monkeypatch.setattr(ime, "llm_extract", empty)
    ime.process_incoming(d)
    ime.process_incoming(d)
    assert calls == ["endo.pdf"] and not told
    assert not (d / "endo.pdf.events.failed").exists() and (d / "endo.pdf.events.waitkey").exists()
    import os, time
    old = time.time() - ime.KEY_RETRY_SEC - 1
    os.utime(d / "endo.pdf.events.waitkey", (old, old))
    ime.process_incoming(d)
    assert calls == ["endo.pdf", "endo.pdf"]


def test_tenant_route_never_reads_owner_cr(db, inbox, monkeypatch, tmp_path):
    import import_medical_events as ime
    d, _ = inbox
    cr = tmp_path / "owner_icloud" / "CR"
    cr.mkdir(parents=True)
    (cr / "owner_secret.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(ime, "CR_DIR", cr)
    seen = []
    real = ime._process
    monkeypatch.setattr(ime, "_process", lambda p, *a, **k: seen.append(p) or real(p, *a, **k))
    ime.process_incoming(d)
    assert seen and all(d in Path(p).parents for p in seen), seen


def test_cyrillic_file_is_not_reprocessed(db, inbox, monkeypatch):
    """24.09 живьём: PDF с кириллицей в имени разбирался заново каждую минуту — attachments хранит
    имя экранированным (\\u0410…), и already_imported по имени как есть его не находил."""
    import import_medical_events as ime
    d, _ = inbox
    (d / "endo.pdf").rename(d / "ТЕСТОВ Ё _100001__0a1b.pdf")
    calls = []
    monkeypatch.setattr(ime, "llm_extract", lambda n, t: calls.append(n) or dict(EXTRACTED))
    ime.process_incoming(d)
    ime.process_incoming(d)
    ime.process_incoming(d)
    assert len(calls) == 1, calls
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_duplicate_sources_sees_repeated_document(db, inbox, monkeypatch):
    """Ночной датчик «один файл — одно событие»: повтор разбора виден, одиночный — нет."""
    import import_medical_events as ime
    d, _ = inbox
    ime.process_incoming(d)
    assert ime.duplicate_sources() == []
    monkeypatch.setattr(ime, "already_imported", lambda s: False)   # поломка идемпотентности
    ime._process(d / "endo.pdf", root=d.parent)
    dups = ime.duplicate_sources()
    assert len(dups) == 1 and dups[0][1] == 2
