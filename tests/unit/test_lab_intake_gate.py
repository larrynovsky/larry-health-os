"""Гейт и водяной знак вотчера: два предохранителя, которые стоят денег при отказе.

Замер 2026-07-29: в корне личной папки iCloud большая часть файлов вне staging —
не лабораторные (заключения обследований, счета, памятки, фото, архивы).
Включение вотчера БЕЗ гейта означало бы vision-прогон каждого;
включение БЕЗ водяного знака — прогон по многолетнему архиву разом.

Оба предохранителя молчаливы по замыслу (пропуск не-лаба — не событие), а значит
их отказ тоже был бы молчаливым: счёт пришёл бы позже и без объяснения. Поэтому
проверка не «работает ли», а «отсекает ли» — по каждому предохранителю отдельно.
"""
from __future__ import annotations

import json
import time

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture()
def wat(tmp_path, monkeypatch):
    # notify резолвит секреты на импорте и падает, если тенант задан, а секреты нет —
    # это правильный отказ (анти-утечка между тенантами), поэтому даём фикстурный путь,
    # а не ослабляем сам гард.
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(secrets))
    import lab_intake_watcher as w
    data = tmp_path / "health" / "data"
    data.mkdir(parents=True)
    monkeypatch.setattr(w.health_db, "DB_PATH", str(data / "health.db"))
    return w


def test_state_lives_outside_icloud(wat, tmp_path):
    """Состояние — в data/ тенанта. Сайдкары рядом с файлами засорили бы личную
    папку владельца в iCloud, а он туда смотрит глазами."""
    assert wat._state_path().parent.name == "data"
    assert "CloudDocs" not in str(wat._state_path())


def test_watermark_is_set_once_and_survives(wat):
    st = {"watermark": 111.0, "notlab": []}
    wat._save_state(st)
    assert wat._load_state()["watermark"] == 111.0


def test_broken_state_file_does_not_crash_the_watcher(wat):
    wat._state_path().write_text("{не json")
    assert wat._load_state() == {}


def test_partner_never_reads_owner_icloud(wat, monkeypatch):
    """Межтенантная граница: CR/ — личная папка владельца. Процесс партнёра
    обязан видеть только свой инбокс, иначе это утечка между тенантами."""
    monkeypatch.setattr(wat, "_tenant", lambda: "health_partner")
    assert all("CloudDocs" not in str(d) for d in wat._watched())


def test_owner_watches_both_sources_when_icloud_exists(wat, monkeypatch, tmp_path):
    cr = tmp_path / "CR"
    cr.mkdir()
    monkeypatch.setattr(wat, "_tenant", lambda: "health")
    monkeypatch.setattr(wat, "_ICLOUD_CR", cr)
    dirs = [str(d) for d in wat._watched()]
    assert any(d.endswith("incoming") for d in dirs)
    assert str(cr) in dirs


def test_non_lab_file_never_reaches_the_recogniser(wat, monkeypatch, tmp_path):
    """Ключевая проверка: распознаватель стоит денег, и гейт обязан его не звать.
    Мок на run_backfill — если гейт откажет, вызов будет виден."""
    inbox = tmp_path / "health" / "incoming"
    inbox.mkdir(parents=True)
    doc = inbox / "заключение обследования.pdf"
    doc.write_text("текстовое заключение, никаких таблиц")
    old = time.time() - 3600
    import os
    os.utime(doc, (old, old))

    called = []
    monkeypatch.setattr(wat, "_tenant", lambda: "health")
    monkeypatch.setattr(wat, "_ICLOUD_CR", tmp_path / "нет-такой")
    monkeypatch.setattr(wat, "_is_lab", lambda p: False)
    monkeypatch.setattr(wat, "_processed", lambda: set())
    monkeypatch.setattr(wat.lab_backfill, "run_backfill",
                        lambda *a, **k: called.append(a) or {"rows": 1})
    wat._save_state({"watermark": old - 10, "notlab": []})

    wat.process_once()
    assert called == [], "не-лабораторный документ уехал в vision-распознавание"
    assert "заключение обследования.pdf" in json.loads(wat._state_path().read_text())["notlab"]


def test_unreadable_file_is_explained_once(wat, monkeypatch):
    """Настоящий classify-gate при отказе extract_text. Мутация: вернуть молчаливый пропуск."""
    import import_all
    import os
    inbox = wat._incoming(); inbox.mkdir(parents=True)
    p = inbox / "unreadable.jpg"; p.write_bytes(b"not an image")
    old = time.time() - 100
    os.utime(p, (old, old))
    wat._save_state({"watermark": 0, "notlab": []})
    monkeypatch.setattr(wat, "_processed", lambda: set())
    monkeypatch.setattr(wat, "_watched", lambda: [inbox])
    def fail(*a):
        raise ValueError("synthetic unreadable file")
    monkeypatch.setattr(import_all, "extract_text", fail)
    told = []
    monkeypatch.setattr(wat.notify, "notify", lambda msg, **k: told.append(msg) or "telegram")
    for _ in range(3):
        wat.process_once()
    assert len(told) == 1 and p.name in told[0]
    assert "оригинальный PDF" in told[0] and "чёткое фото" in told[0]


def test_long_pdf_is_refused_before_classifier_and_vision(wat, monkeypatch):
    """Мутация: проверить предел только после классификатора, который мог отказать."""
    import fitz
    import import_all
    import os
    inbox = wat._incoming(); inbox.mkdir(parents=True)
    p = inbox / "too-long.pdf"
    limit = wat.lab_recognizer._MAX_PAGES
    with fitz.open() as doc:
        for _ in range(limit + 1):
            doc.new_page()
        doc.save(str(p))
    old = time.time() - 100
    os.utime(p, (old, old))
    wat._save_state({"watermark": 0, "notlab": []})
    monkeypatch.setattr(wat, "_watched", lambda: [inbox])
    monkeypatch.setattr(wat, "_processed", lambda: set())
    import genome_intake, import_medical_events
    monkeypatch.setattr(genome_intake, "process_pending", lambda *a, **k: 0)
    monkeypatch.setattr(import_medical_events, "process_incoming", lambda *a, **k: 0)
    classifiers, vision, told = [], [], []
    monkeypatch.setattr(import_all, "classify", lambda *a: classifiers.append(a) or "other")
    monkeypatch.setattr(wat.lab_backfill, "run_backfill", lambda *a: vision.append(a) or {})
    monkeypatch.setattr(wat.notify, "notify", lambda msg, **k: told.append(msg) or "telegram")
    wat.process_once(); wat.process_once()
    assert len(told) == 1 and "Разделите PDF" in told[0]
    assert not classifiers and not vision


def test_sidecar_json_is_not_treated_as_an_image(wat, monkeypatch, tmp_path):
    """Живой баг на тенанте партнёра (Studio, 2026-07-29 14:08): вотчер брал
    `*.jpg.triage.json` и падал с «cannot identify image file» в каждом опросе.
    Отсечка по расширению обязана срабатывать ДО чтения файла — иначе на скане
    это означало бы попытку OCR по кругу."""
    inbox = tmp_path / "health" / "incoming"
    inbox.mkdir(parents=True)
    junk = inbox / "abc.jpg.triage.json"
    junk.write_text("{}")
    old = time.time() - 3600
    import os
    os.utime(junk, (old, old))

    looked = []
    monkeypatch.setattr(wat, "_tenant", lambda: "health")
    monkeypatch.setattr(wat, "_ICLOUD_CR", tmp_path / "нет-такой")
    monkeypatch.setattr(wat, "_is_lab", lambda p: looked.append(p) or True)
    monkeypatch.setattr(wat, "_processed", lambda: set())
    monkeypatch.setattr(wat.lab_backfill, "run_backfill", lambda *a, **k: {"rows": 1})
    wat._save_state({"watermark": old - 10, "notlab": []})

    wat.process_once()
    assert looked == [], "сайдкар дошёл до чтения — отсечка по расширению не сработала"


def test_file_older_than_watermark_is_not_taken(wat, monkeypatch, tmp_path):
    """Второй предохранитель, независимый от первого: даже лабораторный бланк,
    лежавший до включения, не берётся — иначе первый же запуск оплатил бы архив."""
    inbox = tmp_path / "health" / "incoming"
    inbox.mkdir(parents=True)
    doc = inbox / "старый бланк.pdf"
    doc.write_text("WBC RBC HGB")
    old = time.time() - 86400
    import os
    os.utime(doc, (old, old))

    called = []
    monkeypatch.setattr(wat, "_tenant", lambda: "health")
    monkeypatch.setattr(wat, "_ICLOUD_CR", tmp_path / "нет-такой")
    monkeypatch.setattr(wat, "_is_lab", lambda p: True)      # гейт ПРОПУСКАЕТ
    monkeypatch.setattr(wat, "_processed", lambda: set())
    monkeypatch.setattr(wat.lab_backfill, "run_backfill",
                        lambda *a, **k: called.append(a) or {"rows": 1})
    wat._save_state({"watermark": time.time(), "notlab": []})

    wat.process_once()
    assert called == [], "файл старше водяного знака всё-таки уехал в распознавание"


@pytest.fixture(autouse=True)
def _no_side_routes(monkeypatch):
    """process_once зовёт ещё два маршрута входящих (геном, заключения — document-intake 24.09).
    Здесь судится разбор АНАЛИЗОВ: соседние маршруты глушатся, чтобы тест не тянул модель
    и не слал человеку «не смог разобрать» по фиктивным файлам. У них свои тесты."""
    import genome_intake, import_medical_events
    monkeypatch.setattr(genome_intake, "process_pending", lambda *a, **k: 0)
    monkeypatch.setattr(import_medical_events, "process_incoming", lambda *a, **k: 0)


def test_words_miss_is_asked_of_the_model_not_refused(wat, monkeypatch, tmp_path):
    """Нить lab-intake-retry (05.10). На новой установке словарь слов пуст (`docs.type_markers` = '{}'),
    и русский бланк получал «не бланк», не дойдя до модели. Мутации: вернуть отказ по словам; звать
    модель, когда слова уже сказали «да» (лишний вызов); судить не по ответу модели."""
    import fitz
    import config_db
    import doc_triage
    import import_all
    p = tmp_path / "анализы крови.pdf"
    with fitz.open() as doc:
        doc.new_page()
        doc.save(str(p))
    # Пример ВЫДУМАН: русская биохимия без английской шапки и латинских сокращений.
    ru = ("ИНВИТРО Результаты исследований\nИсследование Результат Единицы Референсные значения\n"
          "Глюкоза 5.1 ммоль/л 4.1-5.9\nАЛТ 22 Ед/л <41\nФерритин 120 мкг/л 30-400\n") * 3
    monkeypatch.setattr(import_all, "extract_text", lambda path: ru)
    monkeypatch.setattr(config_db, "doc_type_markers", lambda conn=None: {})
    asked = []
    answer = {"label": doc_triage.LAB}
    monkeypatch.setattr(doc_triage, "classify_image",
                        lambda img, mt="image/jpeg", client=None: asked.append(mt) or
                        {**answer, "fallback": False})
    assert import_all.classify(p, ru) != "lab"           # предпосылка: слова промахиваются
    assert wat._is_lab(p) is True and len(asked) == 1     # модель спрошена и решила
    answer["label"] = doc_triage.REPORT
    assert wat._is_lab(p) is False                         # «заключение» — не бланк
    asked.clear()
    monkeypatch.setattr(import_all, "extract_text", lambda path: "Laboratory tests\n" + ru)
    assert wat._is_lab(p) is True and asked == []          # слова сказали «да» — модель не нужна
