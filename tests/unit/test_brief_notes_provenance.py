"""Провенанс заметок в утреннем брифе (нить brief-repeat, 2026-08-04).

Пинит решение владельца: сказанное ИМ САМИМ система не пересказывает никогда;
выведенное системой из его слов получает РОВНО ОДНУ квитанцию «записал так, поправь».

Вымышленная новость пользователя не должна повторяться в следующих брифах.
Дискриминатор — `source`, а не давность: кулдаун не заменяет провенанс.
"""
from __future__ import annotations
import pytest

pytestmark = pytest.mark.unit

import patient_context as pc
import memory_facts_db as mf
import health_db


def _insert(value: str, source: str, receipt=None) -> int:
    mf._hdb._ensure_memory_facts_table()
    with health_db.get_conn() as conn:
        return conn.execute(
            "INSERT INTO memory_facts (mem_class, value, source, temporal_class, "
            "active, receipt_shown_at) VALUES ('state', ?, ?, 'standing', 1, ?)",
            (value, source, receipt),
        ).lastrowid


def test_owner_said_goes_to_backdrop_and_never_to_receipts(db):
    """Его собственные слова — фон с явным запретом пересказа, без всякой квитанции."""
    _insert("Я перенёс занятие по керамике на четверг", "conversation")
    text, receipts = pc.brief_notes()
    assert "занятие по керамике" in text, "заметка вообще не доехала до промпта"
    assert receipts == [], "по словам владельца квитанция не положена"
    assert "ЗАПРЕЩЕНО пересказывать" in text, "исчез запрет пересказа — вернётся пересказ пользовательской новости"
    assert "УЧИТЫВАЙ" not in text, "вернулась старая формулировка, приглашавшая пересказать"


def test_arbiter_unverified_gets_exactly_one_receipt(db):
    """Выведенное системой: одна квитанция, после отметки — молчание навсегда."""
    fid = _insert("Fixture item count 17", "arbiter_unverified")
    text, receipts = pc.brief_notes()
    assert receipts == [fid], f"квитанция не выдана: {receipts}"
    assert "поправь, если не так" in text, "исчезла форма квитанции"

    assert mf.mark_receipts_shown(receipts) == 1
    text2, receipts2 = pc.brief_notes()
    assert receipts2 == [], "квитанция выдана повторно — «ровно один раз» сломано"
    assert "поправь, если не так" not in text2, "блок квитанции остался после отметки"
    assert "Fixture item count 17" in text2, "факт исчез из фона — потеря дороже повтора"


def test_mark_receipts_shown_is_idempotent(db):
    """Повторная отметка ничего не меняет (catch-up брифа не должен ломать счёт)."""
    fid = _insert("выведенное значение", "arbiter_unverified")
    assert mf.mark_receipts_shown([fid]) == 1
    assert mf.mark_receipts_shown([fid]) == 0
    assert mf.mark_receipts_shown([]) == 0


def test_unknown_source_is_silent_not_narrated(db):
    """Список источников под квитанцию ЗАКРЫТ по строгому: незнакомый источник молчит,
    а не получает право рассказать о себе. Ошибаться безопаснее в сторону молчания."""
    _insert("что-то от неизвестного писателя", "some_future_writer")
    text, receipts = pc.brief_notes()
    assert receipts == [], "незнакомый источник выпросил квитанцию"
    assert "что-то от неизвестного писателя" in text


def test_negative_control_receipt_detector_is_alive(db, monkeypatch):
    """ИСПОЛНЕННЫЙ негативный контроль: глушим признак — позитив обязан покраснеть.

    Без этого зелёный test_arbiter_unverified_gets_exactly_one_receipt неотличим от
    зелёного по совпадению (§20): он бы прошёл и на коде, который квитанций не выдаёт.

    Восстанавливаем признак руками, а НЕ monkeypatch.undo(): undo снимает ВСЕ патчи
    теста, включая те, которыми фикстура `db` увела health_db на временную базу — то есть
    вторая половина контроля стреляла бы по БОЕВОЙ базе. Поймано первым же прогоном.
    """
    _insert("Fixture item count 17", "arbiter_unverified")
    original = pc._NEEDS_RECEIPT
    monkeypatch.setattr(pc, "_NEEDS_RECEIPT", ())
    _text, receipts = pc.brief_notes()
    assert receipts == [], "контроль негоден: квитанция выдалась при пустом списке источников"

    monkeypatch.setattr(pc, "_NEEDS_RECEIPT", original)
    _text2, receipts2 = pc.brief_notes()
    assert receipts2, "признак мёртв: с восстановленным списком квитанция не выдалась"
