"""tests/unit/test_doc_review_attachment.py — карточка doc_review несёт сам документ.

ЗАЧЕМ. Карточка «📄 Новый документ … Что это?» показывала одно только имя файла.
Ответить на такой вопрос владелец может, лишь пойдя искать документ на диске — то есть
по памяти либо никак; не ответил за 48 часов, и `auto_confirm_stale_reviews` подтвердит
за него тип, предложенный моделью. Отказ молчаливый: ничего не падает, просто человек-
гейт незаметно вырождается в задержку с автоприёмом (§13 — эскалация к человеку имеет
смысл, только если человеку есть на чём вынести вердикт).

ЧТО СТЕРЕЖЁТ — четыре ветки `_send_doc_review_message`:
  1. файл на месте → уходит `send_document`, тот же текст в caption и ТЕ ЖЕ кнопки
     (потеря кнопок = карточка без ответа, callback `docrev_<id>_<type>` живёт в них);
  2. файла нет → карточка без технического хвоста, причина доставлена оператору;
  3. файл больше лимита Telegram → человеческая строка о сохранённом файле;
  4. Telegram отклонил вложение → карточка всё равно уходит текстом (алерт не теряется).

ЧЕГО НЕ ДОКАЗЫВАЕТ. Что Telegram реально отрисует вложение: мок принимает `document`
как есть. Проверено, что уходит существующий путь и верное имя файла, — не то, что боту
хватит прав, сети и что 45 МБ действительно пролезут. Живой оракул только один — прогон
на Studio с настоящим ботом.

СРЕДА. Импортирует `jobs.scheduled` на уровне модуля, как соседний `test_hae_alert.py`:
вне Studio прогону нужен `HEALTH_SECRETS_DIR` (tests/conftest.py уводит только
`HEALTH_DATA_DIR`, и без секретов `bot.filters` падает на импорте).
"""
from __future__ import annotations

import json

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from telegram.error import BadRequest

import jobs.scheduled as sched
import i18n

_REVIEW = {"id": 7, "source_file": "docs/2026-07-29_probe.pdf", "proposed_type": "general_medical"}
_NAME = "2026-07-29_probe.pdf"


@pytest.fixture
def root(tmp_path, monkeypatch):
    """Корень документов подменяем у ЕДИНСТВЕННОГО дома — `import_all.HEALTH`.

    Если появится вторая копия корня, этот тест не заметит подмены и покраснеет —
    и это правильно: расхождение домов должно быть видно, а не молча работать.
    """
    # Вложения не зависят от классификатора документов; его импорт не читает БД.
    with patch("health_db.get_doc_patterns", side_effect=OSError("invented offline database")):
        import import_all
    monkeypatch.setattr(import_all, "HEALTH", tmp_path)
    monkeypatch.setattr(sched, "owner_chat_id", lambda: 42)
    monkeypatch.setattr(sched, "is_owner", lambda: True)
    return tmp_path


def _make_doc(root: Path, payload: bytes = b"%PDF-1.4 fake") -> Path:
    p = root / "docs" / _NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(payload)
    return p


def _send(bot) -> None:
    asyncio.run(sched._send_doc_review_message(bot, _REVIEW))


def _kinds(tg) -> list:
    return [m["type"] for m in tg.outgoing]


def test_file_attached_with_same_caption_and_buttons(root, tg):
    p = _make_doc(root)
    _send(tg)

    assert _kinds(tg) == ["document"], "файл на месте — карточка обязана уйти документом"
    sent = tg.outgoing[0]
    assert Path(sent["document"]) == p
    assert sent["filename"] == _NAME
    assert "Выбери тип документа" in (sent["caption"] or "")
    cbs = [b.callback_data for row in sent["reply_markup"].inline_keyboard for b in row]
    assert "docrev_7_lab" in cbs and "docrev_7_skip" in cbs


def test_missing_file_names_the_reason(root, tg, fault_journal):
    with patch("notify.notify_operator", return_value="telegram") as operator:
        _send(tg)   # файл не создавали

    assert _kinds(tg) == ["message"]
    text = tg.outgoing[0]["text"]
    assert text == i18n.t("cards.document.review", fname=_NAME, proposed=_REVIEW["proposed_type"])
    assert _REVIEW["source_file"] not in text and str(root) not in text
    operator.assert_not_called()
    records = [json.loads(line) for line in fault_journal.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["where"] == 'scheduled.doc_review'
    assert "file missing review_id=7" in records[0]["text"]


def test_oversize_names_the_size(root, tg, monkeypatch):
    _make_doc(root, b"x" * 4096)
    monkeypatch.setattr(sched, "_DOC_ATTACH_MAX_MB", 0.001)
    with patch("notify.notify_operator", return_value="telegram") as operator:
        _send(tg)

    assert _kinds(tg) == ["message"]
    text = tg.outgoing[0]["text"]
    assert text == (i18n.t("cards.document.review", fname=_NAME, proposed=_REVIEW["proposed_type"])
                    + i18n.t("cards.document.too_large"))
    assert "Файл большой, в чат не влез — он сохранён у меня." in text
    assert _REVIEW["source_file"] not in text and str(root) not in text
    operator.assert_not_called()


def test_tenant_never_gets_a_file(root, tg, monkeypatch, fault_journal):
    """Партнёру вложение не уходит НИКОГДА — даже когда файл по пути ЕСТЬ.

    Корень документов — литерал владельца, и одинаковый относительный путь у двух
    тенантов отдал бы партнёру документ владельца. Проба нарочно кладёт файл на место:
    случай «не нашлось» доказывал бы отсутствие утечки совпадением, а не запретом.
    """
    _make_doc(root)
    monkeypatch.setattr(sched, "is_owner", lambda: False)
    with patch("notify.notify_operator", return_value="telegram") as operator:
        _send(tg)

    assert _kinds(tg) == ["message"]
    text = tg.outgoing[0]["text"]
    assert text == i18n.t("cards.document.review", fname=_NAME, proposed=_REVIEW["proposed_type"])
    assert "тенант" not in text.lower()
    assert _REVIEW["source_file"] not in text and str(root) not in text
    operator.assert_not_called()
    records = [json.loads(line) for line in fault_journal.read_text().splitlines()]
    assert len(records) == 1 and records[0]["where"] == "scheduled.doc_review"
    assert "attachment owner-only review_id=7" in records[0]["text"]


def test_telegram_reject_still_delivers_the_card(root, tg, fault_journal):
    _make_doc(root)

    async def _boom(**kwargs):
        raise BadRequest("file is too big")

    tg.send_document = _boom
    with patch("notify.notify_operator", return_value="telegram") as operator:
        _send(tg)

    assert _kinds(tg) == ["message"], "вложение не прошло — но алерт терять нельзя"
    text = tg.outgoing[0]["text"]
    assert text == i18n.t("cards.document.review", fname=_NAME, proposed=_REVIEW["proposed_type"])
    assert _REVIEW["source_file"] not in text and str(root) not in text
    assert "BadRequest" not in text and "file is too big" not in text
    operator.assert_not_called()
    records = [json.loads(line) for line in fault_journal.read_text().splitlines()]
    assert len(records) == 1 and records[0]["where"] == "scheduled.doc_review"
    assert "attachment rejected review_id=7: BadRequest: file is too big" in records[0]["text"]
