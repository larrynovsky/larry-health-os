"""Квитанция бота за файл обещает разбор только там, где разборщик его берёт (empty-profile, 24.09).

До 24.09 любой файл получал «распознаю и пришлю на подтверждение», а разборщик входящих берёт
только форматы DOC_EXTS: геном в .txt/.vcf/.zip лежал бы без движения под обещанием разбора.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("name", ["genome_23andme.txt", "sample.vcf", "export.zip", "notes.docx"])
def test_unparsed_format_is_not_promised(name):
    from handlers.messages import _receipt_text
    t = _receipt_text(name)
    assert "распознаю" not in t and "не разбираю" in t


def test_every_parsed_format_is_promised_for_labs_and_conclusions():
    """Обещание в квитанции = маршрут, который реально возьмёт файл: анализы — DOC_EXTS
    распознавателя, заключения — DOC_EXTS разбора документов (они не совпадают: .tif)."""
    from handlers.messages import _receipt_text
    from lab_intake_watcher import DOC_EXTS
    import import_medical_events as ime
    for ext in DOC_EXTS:
        t = _receipt_text(f"file{ext}")
        assert "Анализы распознаю" in t, ext
        assert ("из заключения врача извлеку диагнозы" in t) == (ext in ime.DOC_EXTS), ext


def test_receipt_warns_when_intake_service_is_down(tmp_path, monkeypatch):
    """Пульс разборщика старше 10 минут или его нет → квитанция говорит, что файл просто лежит."""
    import os, time
    import lab_intake_watcher
    from handlers import messages
    hb = tmp_path / "lab_intake_state.json"
    monkeypatch.setattr(lab_intake_watcher, "heartbeat_path", lambda: hb)
    assert "Разбор сейчас на паузе" in messages._intake_down_note()          # пульса нет
    hb.write_text("{}")
    assert messages._intake_down_note() == ""                      # свежий пульс
    old = time.time() - 3600
    os.utime(hb, (old, old))
    assert "Разбор сейчас на паузе" in messages._intake_down_note()          # цикл встал час назад
