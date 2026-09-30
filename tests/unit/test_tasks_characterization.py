"""
tests/unit/test_tasks_characterization.py — характеризационные пины домена tasks
(Поток B рефакторинга, 2026-06-27). Закрепляет ТЕКУЩЕЕ поведение до разбиения:
  save_task (дедуп по fingerprint), get_open_tasks (порядок приоритета),
  get_overdue_tasks (фильтр по возрасту), resolve_task.

behavior-preserving пины: «как есть» ≠ «как правильно».
"""
from __future__ import annotations

import health_db


def test_save_task_returns_id_and_appears_in_open(db):
    tid = health_db.save_task("gp", "action", "drink water")
    assert isinstance(tid, int)
    assert "drink water" in [t["content"] for t in health_db.get_open_tasks()]


def test_save_task_dedup_by_fingerprint_returns_none_while_open(db):
    first = health_db.save_task("gp", "action", "task A", fingerprint="fp1")
    assert isinstance(first, int)
    dup = health_db.save_task("gp", "action", "task A again", fingerprint="fp1")
    assert dup is None


def test_save_task_same_fingerprint_after_resolve_not_deduped(db):
    first = health_db.save_task("gp", "action", "task A", fingerprint="fp2")
    health_db.resolve_task(first)
    again = health_db.save_task("gp", "action", "task A", fingerprint="fp2")
    assert isinstance(again, int)
    assert again != first


def test_get_open_tasks_priority_order(db):
    health_db.save_task("s", "action", "med",  priority="medium")
    health_db.save_task("s", "action", "crit", priority="critical")
    health_db.save_task("s", "action", "high", priority="high")
    order = [t["content"] for t in health_db.get_open_tasks()]
    assert order.index("crit") < order.index("high") < order.index("med")


def test_get_overdue_tasks_filters_by_age(db):
    health_db.save_task("s", "action", "fresh")
    db.execute(
        "INSERT INTO tasks (content, status, source, priority, type, created_at) "
        "VALUES (?, 'open', 'test', 'medium', 'action', '2020-01-01')",
        ("old task",),
    )
    overdue = [t["content"] for t in health_db.get_overdue_tasks(days_old=7)]
    assert "old task" in overdue
    assert "fresh" not in overdue


def test_resolve_task_closes_and_returns_true(db):
    tid = health_db.save_task("s", "action", "temp")
    assert health_db.resolve_task(tid, resolved_text="ok") is True
    assert all(t["id"] != tid for t in health_db.get_open_tasks())
    row = db.fetchone("SELECT status, resolved_text FROM tasks WHERE id=?", (tid,))
    assert row["status"] == "completed"
    assert row["resolved_text"] == "ok"


def test_resolve_task_refuses_missing_or_already_closed(db):
    """28.09.2026: resolve_task возвращал True всегда — «/dismiss 999» отвечал «отклонена»,
    повторное нажатие перезаписывало итог уже закрытой задачи."""
    tid = health_db.save_task("s", "action", "temp")
    assert health_db.resolve_task(tid, resolved_text="done", status="completed") is True
    assert health_db.resolve_task(tid, resolved_text="x", status="dismissed") is False
    row = db.fetchone("SELECT status, resolved_text FROM tasks WHERE id=?", (tid,))
    assert (row["status"], row["resolved_text"]) == ("completed", "done")
    assert health_db.resolve_task(10**9, status="dismissed") is False
