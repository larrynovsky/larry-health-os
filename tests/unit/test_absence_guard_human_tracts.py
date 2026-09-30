"""Сторож отсутствия стоит на трактах, чей выход читает ЧЕЛОВЕК.

Решение владельца 14.09 (вариант C): судья один, политика отказа разная. Отчёт GP
блокируется (он перегенерируется), чат и бриф — ПОПРАВЛЯЮТСЯ: у них нет второго
пути доставки, и блок превратился бы в молчание.

Мутации, которые обязаны ронять эти тесты, названы в каждом.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_judge_returns_hits_and_note(db, clock):
    """Судья общий: находит ложь и отдаёт готовое уточнение с датой."""
    clock.set("2026-09-14")
    db.add_lab_result("2026-09-01", "TSH", 1.2, unit="мкМЕ/мл")
    import gp_context as gc
    hits, note = gc.judge_absence_claims("ТТГ никогда не сдавался")
    assert [h["test"] for h in hits] == ["TSH"]
    assert "2026-09-01" in note


def test_judge_silent_on_honest_window_claim(db, clock):
    """Позитивный контроль: честная формулировка с границей окна не трогается.

    Без этого сторож гнал бы модель обратно в безграничное враньё (урок 13.09).
    """
    clock.set("2026-09-14")
    db.add_lab_result("2021-06-14", "Insulin", 6.3, unit="мкЕд/мл")
    import gp_context as gc
    hits, note = gc.judge_absence_claims("инсулин — нет данных за последние 730 дней")
    assert hits == [] and note == ""


def test_judge_fails_open_when_canon_unreadable(monkeypatch):
    """Сбой чтения канона НЕ задерживает текст: fail-OPEN назван в докстроке.

    Мутация: сделать except → raise, и тест краснеет вместе с доставкой ответа.
    """
    import gp_context as gc, health_db
    monkeypatch.setattr(health_db, "get_recent_labs",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("БД недоступна")))
    hits, note = gc.judge_absence_claims("ТТГ никогда не сдавался")
    assert hits == [] and note == ""


def test_chat_reply_is_corrected_not_blocked(db, clock):
    """Чат: ответ доезжает И несёт уточнение. Мутация: заменить поправку на блок —
    краснеет утверждение про сохранённый текст ответа."""
    clock.set("2026-09-14")
    db.add_lab_result("2026-09-01", "TSH", 1.2, unit="мкМЕ/мл")
    import hai_chat
    out = hai_chat._guarded_reply("ТТГ никогда не сдавался, стоит сдать")
    assert "стоит сдать" in out          # исходный ответ НЕ выброшен
    assert "2026-09-01" in out           # и уточнение приписано


def test_report_guard_appends_note(db, clock):
    """Бриф и недельный отчёт — та же политика поправки, тот же судья."""
    clock.set("2026-09-14")
    db.add_lab_result("2026-09-01", "TSH", 1.2, unit="мкМЕ/мл")
    import hai_reports
    out = hai_reports._guarded_report("ТТГ ни разу не проверялся", "morning")
    assert "ТТГ ни разу не проверялся" in out
    assert "2026-09-01" in out


def test_report_guard_silent_when_clean(db, clock):
    """Позитивный контроль: чистый текст не обрастает служебными приписками."""
    clock.set("2026-09-14")
    db.add_lab_result("2026-09-01", "TSH", 1.2, unit="мкМЕ/мл")
    import hai_reports
    text = "Сон вчера 7 часов, ВСР в норме."
    assert hai_reports._guarded_report(text, "morning") == text
