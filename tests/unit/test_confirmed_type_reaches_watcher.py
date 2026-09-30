"""Кнопка «🧪 Анализы»: подтверждение человека сильнее классификатора.

Дефект, который здесь закрывается (замер 2026-07-29, чтение пути + два замера
классификатора): `apply_confirmed_type` звала `coordinate_import`, а та первым
делом ПЕРЕКЛАССИФИЦИРОВАЛА документ — тем же классификатором, который уже
промахнулся, — и молча выходила на `if doc_type != "lab": return`. То есть
человеческий гейт был фиктивным ровно для тех документов, ради которых
существует. Цена измерена: специализированная панель из архивного скана
в каноне отсутствовала.

Проверяется свойство, а не реализация: после подтверждения документ ОБЯЗАН
попасть в разбор, даже если машина по-прежнему считает его не-лабом и даже если
он лежит в архиве с прошлого года.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture()
def wat(tmp_path, monkeypatch):
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(secrets))
    import lab_intake_watcher as w
    data = tmp_path / "health" / "data"
    data.mkdir(parents=True)
    monkeypatch.setattr(w.health_db, "DB_PATH", str(data / "health.db"))
    return w


def test_human_verdict_overrides_the_classifier(wat, monkeypatch, tmp_path):
    """Гейт говорит «не лаб», человек говорит «лаб» — едет распознавание."""
    inbox = tmp_path / "health" / "incoming"
    inbox.mkdir(parents=True)
    doc = inbox / "спец-панель.pdf"
    doc.write_text("Ferritin 54 ng/ml")
    old = time.time() - 3600
    import os
    os.utime(doc, (old, old))

    called = []
    monkeypatch.setattr(wat, "_tenant", lambda: "health")
    monkeypatch.setattr(wat, "_ICLOUD_CR", tmp_path / "нет-такой")
    monkeypatch.setattr(wat, "_is_lab", lambda p: False)   # машина ПРОТИВ
    monkeypatch.setattr(wat, "_processed", lambda: set())
    monkeypatch.setattr(wat.notify, "notify", lambda msg: None)
    monkeypatch.setattr(wat.lab_backfill, "run_backfill",
                        lambda *a, **k: called.append(a) or {"rows": 3, "pending": 3})
    wat._save_state({"watermark": old - 10, "notlab": [doc.name]})

    wat.force_lab(doc)
    wat.process_once()
    assert called, "подтверждение человека не дошло до распознавания"


def test_human_verdict_beats_the_watermark_too(wat, monkeypatch, tmp_path):
    """Файл из архива, старше водяного знака. Именно это делает кнопку
    осмысленной: она про старые документы ровно так же, как про новые."""
    inbox = tmp_path / "health" / "incoming"
    inbox.mkdir(parents=True)
    doc = inbox / "бланк 2023.pdf"
    doc.write_text("WBC RBC HGB")
    old = time.time() - 86400 * 400
    import os
    os.utime(doc, (old, old))

    called = []
    monkeypatch.setattr(wat, "_tenant", lambda: "health")
    monkeypatch.setattr(wat, "_ICLOUD_CR", tmp_path / "нет-такой")
    monkeypatch.setattr(wat, "_is_lab", lambda p: True)
    monkeypatch.setattr(wat, "_processed", lambda: set())
    monkeypatch.setattr(wat.notify, "notify", lambda msg: None)
    monkeypatch.setattr(wat.lab_backfill, "run_backfill",
                        lambda *a, **k: called.append(a) or {"rows": 1, "pending": 1})
    wat._save_state({"watermark": time.time(), "notlab": []})

    wat.force_lab(doc)
    wat.process_once()
    assert called, "подтверждённый архивный документ отсечён водяным знаком"


def test_force_lab_clears_the_machine_verdict(wat, tmp_path):
    wat._save_state({"watermark": 0.0, "notlab": ["x.pdf"]})
    wat.force_lab(Path("/любой/путь/x.pdf"))
    st = wat._load_state()
    assert "x.pdf" not in st["notlab"]
    assert "x.pdf" in st["forced"]


def test_button_no_longer_calls_the_old_route(monkeypatch, tmp_path):
    """Замок на демонтируемый маршрут: coordinate_import писал прямо в
    lab_results мимо staging. Кнопка не должна его звать НИКОГДА — иначе
    подтверждение человека снова станет дорогой в канон в обход ревью."""
    import import_all
    import import_coordinator

    fitz = pytest.importorskip("fitz")
    doc = tmp_path / "бланк.pdf"
    d = fitz.open()                          # настоящий PDF: подделка тут не годится,
    d.new_page()                             # функция открывает файл прежде всего
    d.save(str(doc))
    d.close()

    old_route = []
    forced = []
    monkeypatch.setattr(import_coordinator, "coordinate_import",
                        lambda *a, **k: old_route.append(a) or {})
    import lab_intake_watcher
    monkeypatch.setattr(lab_intake_watcher, "force_lab", lambda p: forced.append(p))

    import_all.apply_confirmed_type(str(doc), "lab")
    assert old_route == [], "кнопка снова зовёт демонтируемый маршрут"
    assert forced, "кнопка не сообщила вердикт вотчеру"


@pytest.fixture(autouse=True)
def _no_side_routes(monkeypatch):
    """process_once зовёт ещё два маршрута входящих (геном, заключения — document-intake 24.09).
    Здесь судится разбор АНАЛИЗОВ: соседние маршруты глушатся, чтобы тест не тянул модель
    и не слал человеку «не смог разобрать» по фиктивным файлам. У них свои тесты."""
    import genome_intake, import_medical_events
    monkeypatch.setattr(genome_intake, "process_pending", lambda *a, **k: 0)
    monkeypatch.setattr(import_medical_events, "process_incoming", lambda *a, **k: 0)
