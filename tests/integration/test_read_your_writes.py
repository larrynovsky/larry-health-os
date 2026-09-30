"""
tests/integration/test_read_your_writes.py — Фаза 3 (2026-07-05): read-your-writes.

Явно сказанное в чате видно структурным потребителям (build_patient_brief /
reasoning_block) СРАЗУ — до того, как арбитр (~90с) структурирует это в memory_facts.
Источник — conversation_history новее _arbiter_last_run.

Тестируем рискованное (не happy-path):
  - НОВОЕ (после последнего прогона арбитра) видно;
  - СТАРОЕ (уже обработанное, до cutoff) НЕ дублируется;
  - реплики ассистента НЕ подмешиваются (только user-ввод);
  - мёртвый арбитр (очень старый cutoff) не вываливает сутки — окно max_hours.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.integration


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _seed(db, arbiter_run: str, msgs: list[tuple[str, str, str]]) -> None:
    """arbiter_run — updated_at heartbeat'а; msgs — (role, content, created_at)."""
    db.execute(
        "INSERT OR REPLACE INTO system_config "
        "(key, value_text, category, updated_at, source) "
        "VALUES ('_arbiter_last_run','ok','_heartbeat',?,'test')",
        (arbiter_run,),
    )
    for role, content, created in msgs:
        db.execute(
            "INSERT INTO conversation_history (role, content, created_at) "
            "VALUES (?,?,?)",
            (role, content, created),
        )


def test_just_said_visible_before_arbiter(db):
    """Сказанное после последнего прогона арбитра видно сразу — и в pending_chat,
    и в едином reasoning_block (потребитель-рассуждатель)."""
    import patient_context as pc
    now = datetime.utcnow()
    _seed(db, _fmt(now - timedelta(hours=1)), [
        ("user", "перенёс вечернюю прогулку вчера", _fmt(now - timedelta(minutes=10))),
    ])
    assert "перенёс вечернюю прогулку" in pc.pending_chat()
    assert "перенёс вечернюю прогулку" in pc.reasoning_block()


def test_old_and_assistant_excluded(db):
    """До-cutoff (уже структурировано арбитром) и реплики ассистента не подмешиваются."""
    import patient_context as pc
    now = datetime.utcnow()
    _seed(db, _fmt(now - timedelta(hours=1)), [
        ("user", "СТАРОЕ уже обработано", _fmt(now - timedelta(hours=3))),   # < cutoff
        ("assistant", "ОТВЕТ бота", _fmt(now - timedelta(minutes=5))),        # не user
        ("user", "болит голова сегодня", _fmt(now - timedelta(minutes=5))),   # свежее
    ])
    out = pc.pending_chat()
    assert "болит голова" in out
    assert "СТАРОЕ" not in out, "до-cutoff не должно всплывать (иначе дубль recent_notes)"
    assert "ОТВЕТ бота" not in out, "реплики ассистента — не read-your-writes"


def test_dead_arbiter_bounded_by_window(db):
    """Мёртвый арбитр (cutoff недельной давности): не вываливаем всё подряд —
    ограничены окном max_hours, поэтому очень старое сообщение отсекается."""
    import patient_context as pc
    now = datetime.utcnow()
    _seed(db, _fmt(now - timedelta(days=7)), [
        ("user", "позавчерашнее", _fmt(now - timedelta(days=2))),             # вне окна 12ч
        ("user", "часовой давности", _fmt(now - timedelta(hours=1))),         # в окне
    ])
    out = pc.pending_chat(max_hours=12)
    assert "часовой давности" in out
    assert "позавчерашнее" not in out


def test_quiet_when_all_structured(db):
    """Арбитр в актуальном состоянии (cutoff = сейчас): свежее уже обработано →
    pending пуст, никакого шума в контексте."""
    import patient_context as pc
    now = datetime.utcnow()
    _seed(db, _fmt(now + timedelta(seconds=1)), [
        ("user", "уже структурировано", _fmt(now - timedelta(minutes=30))),
    ])
    assert pc.pending_chat() == ""
