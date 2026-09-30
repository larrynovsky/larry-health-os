"""
UC-G-01 — GP report → tasks → Reminders, dedup по fingerprint.

Источник: USE_CASES.md §3.G → UC-G-01.
Реализация: `task_agent.py`, `health_db.save_task`.
Status: `partial`.

Здесь — unit-уровень: dedup по fingerprint в health_db.
Полный e2e (AppleScript Reminders) — отдельный e2e_mock.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_save_task_with_fingerprint(db):
    import health_db
    health_db.save_task(
        source="gp_daily_auto", type_="analysis",
        content="Test task", source_date="2026-05-01",
        fingerprint="lab:CEA",
    )
    row = db.fetchone("SELECT fingerprint FROM tasks WHERE source_date='2026-05-01'")
    assert row is not None
    assert row["fingerprint"] == "lab:CEA"


def test_dedup_via_fingerprint_on_open_tasks(db):
    """get_open_tasks не должна возвращать несколько задач с одним fingerprint."""
    import health_db
    fp = "lab:CEA,CA19.9"
    for _ in range(3):
        health_db.save_task(
            source="x", type_="analysis", content=f"Task",
            source_date="2026-05-01", fingerprint=fp,
        )
    open_tasks = health_db.get_open_tasks()
    same_fp = [t for t in open_tasks if t.get("fingerprint") == fp]
    # Либо UPSERT (1), либо несколько физических записей но get_open_tasks
    # должна возвращать уникальные. Если возвращает >1 — это баг.
    assert len(same_fp) <= 1, \
        f"dedup по fingerprint: {len(same_fp)} в open_tasks"


def test_task_agent_module_imports():
    import task_agent
    assert task_agent is not None


def test_task_agent_has_extract_tasks_public_api():
    """task_agent.extract_tasks — публичный entrypoint (alias на extract_tasks_from_report)."""
    import task_agent
    assert hasattr(task_agent, "extract_tasks"), \
        "task_agent.extract_tasks отсутствует (UC-G-01 public API)"
    assert hasattr(task_agent, "extract_tasks_from_report"), \
        "Алиас должен указывать на оригинал"
    # Проверка что это та же функция (alias, не отдельная реализация)
    assert task_agent.extract_tasks is task_agent.extract_tasks_from_report


def test_extract_tasks_signature():
    """Сигнатура extract_tasks: report_text + source + опции."""
    import inspect
    from task_agent import extract_tasks
    sig = inspect.signature(extract_tasks)
    params = sig.parameters
    assert "report_text" in params
    assert "source" in params
