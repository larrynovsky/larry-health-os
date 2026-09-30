"""
Eventual consistency: Tasks ↔ Reminders sync window 3ч.

Источник: USE_CASES.md UC-G-02 + TEST_ARCHITECTURE.md §5.6.
Уровень: consistency.

`reminders_sync.py` запускается каждые 3ч (`com.larry.health.reminders-sync`).
Между запусками БД и macOS Reminders могут расходиться. После sync — сходятся.

Тест: имитируем «выполнено в Reminders, но sync ещё не прошёл» → status=open;
после sync → status=done.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.consistency


def test_task_open_in_db_when_reminder_completed_but_no_sync(db):
    """До sync: Reminder отмечен done в OS, БД ещё показывает open."""
    import health_db
    health_db.save_task(
        source="gp_daily_auto",
        type_="analysis",
        content="Сдать CEA / CA19-9",
        source_date="2026-05-01",
        fingerprint="lab:CEA,CA19.9",
    )
    open_tasks = health_db.get_open_tasks()
    assert open_tasks, "Task должна быть в open"
    # Симулируем: Reminder в OS отмечен done, но sync ещё не вызван
    # → БД продолжает показывать open
    assert any(t.get("fingerprint") == "lab:CEA,CA19.9" for t in open_tasks)


def test_task_done_in_db_after_simulated_sync(db):
    """После sync: задача отмечена done в БД."""
    import health_db
    health_db.save_task(
        source="gp_daily_auto",
        type_="analysis",
        content="Сдать ферритин",
        source_date="2026-05-01",
        fingerprint="lab:ferritin",
    )
    open_tasks = health_db.get_open_tasks()
    task_id = open_tasks[0]["id"]

    # Симуляция reminders_sync: помечаем как done
    # (в реальной схеме нет completed_at — закрытие через status='done')
    with db.conn() as c:
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (task_id,))

    open_after = health_db.get_open_tasks()
    assert all(t["id"] != task_id for t in open_after), \
        "после sync задача не должна быть в open"


def test_dedup_via_fingerprint_prevents_duplicate(db):
    """Одна и та же fingerprint → save_task не плодит дубли."""
    import health_db
    fp = "lab:CEA,CA19.9"

    health_db.save_task(source="gp_daily_auto", type_="analysis",
                         content="первый", source_date="2026-05-01",
                         fingerprint=fp)
    # Повторное сохранение — должно либо вернуть существующий ID, либо
    # пропустить (зависит от реализации save_task с UPSERT).
    health_db.save_task(source="gp_daily_auto", type_="analysis",
                         content="второй", source_date="2026-05-01",
                         fingerprint=fp)

    rows = db.fetchall("SELECT * FROM tasks WHERE fingerprint=?", (fp,))
    # В zависимости от семантики save_task: либо 1 (UPSERT), либо несколько,
    # но `get_open_tasks` должен возвращать с учётом dedup.
    open_tasks = health_db.get_open_tasks()
    same_fp = [t for t in open_tasks if t.get("fingerprint") == fp]
    assert len(same_fp) <= 1, \
        f"dedup по fingerprint не сработал: {len(same_fp)} в open_tasks"


def test_eventual_window_simulated_3h(db, clock):
    """
    Полный цикл: t0 — task создана; t1 — Reminder выполнен в OS; t2 — sync.
    Между t1 и t2 (до 3ч) — БД и OS расходятся.
    После t2 — сходятся.
    """
    import health_db

    # t0
    clock.set("2026-05-08T09:00:00")
    health_db.save_task(source="x", type_="analysis", content="t",
                         source_date="2026-05-08", fingerprint="evt-test")
    open_t0 = health_db.get_open_tasks()
    assert any(t.get("fingerprint") == "evt-test" for t in open_t0)

    # t1: пользователь отметил Reminder done в OS, но sync ещё не вызван
    clock.advance(hours=1)
    open_t1 = health_db.get_open_tasks()
    # БД пока ничего не знает — open
    assert any(t.get("fingerprint") == "evt-test" for t in open_t1)

    # t2: sync вызван (через 3ч cadence)
    clock.advance(hours=2)  # суммарно +3ч от t0
    task_id = next(t["id"] for t in open_t1 if t.get("fingerprint") == "evt-test")
    with db.conn() as c:
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (task_id,))

    open_t2 = health_db.get_open_tasks()
    assert not any(t.get("fingerprint") == "evt-test" for t in open_t2), \
        "после sync задача должна исчезнуть из open"
