#!/usr/bin/env python3.11
"""
reminders_sync.py — синхронизация macOS Reminders → SQLite.

Запускается launchd каждые 3 часа.
Читает выполненные напоминания из списка текущего тенанта (владелец: "Health"),
извлекает task_id из тела ([task_id:N]), закрывает задачи в БД.
"""

import subprocess
import sys
import re
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import health_db as db
from secrets_paths import is_owner_data


def reminders_list_name() -> str:
    """Owner keeps Health; other tenants use their data-directory suffix."""
    if is_owner_data():
        return "Health"
    name = Path(db._resolve_health_dir()).name.removeprefix("health_")
    return f"Health ({name})"


def _log_path() -> Path:
    """Preserve the owner's log path and isolate other tenants' logs."""
    if is_owner_data():
        return Path.home() / "health_reminders_sync.log"
    return Path(db._resolve_health_dir()) / "logs" / "health_reminders_sync.log"


_log_file = _log_path()
_log_file.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(_log_file),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


def _unread(why: str) -> list[int]:
    """Ящик напоминаний не прочитан — это сбой, а не «выполненных нет». Замер 30.09: нативный синк
    с 08.08 по 30.09 (все 422 прогона) падал по таймауту osascript, писал ERROR в свой лог и
    отвечал «No completed reminders» — галочки на iPhone восемь недель не закрывали ни одной задачи,
    и никто, кроме лога, этого не знал. Теперь сбой идёт в журнал сбоев (его читает ночной монитор)."""
    log.error(f"get_completed_task_ids failed: {why}")
    try:
        import notify
        notify.fault(f"reminders_sync.py: ящик напоминаний не прочитан — {why[:300]}", person_key=None)
    except Exception as e:  # noqa: BLE001 — журнал недоступен: лог выше уже есть
        log.error(f"notify.fault failed: {e}")
    return []


def get_completed_task_ids() -> list[int]:
    """
    Читает выполненные напоминания из списка текущего тенанта через AppleScript.
    Возвращает список task_id найденных в теле напоминания.
    """
    import reminders_backend
    if reminders_backend.caldav_configured():   # CalDAV (docker-install, этап 3)
        try:
            return reminders_backend.completed_task_ids(reminders_list_name())
        except Exception as e:  # noqa: BLE001 — сеть/сервер: громко, как ошибка AppleScript ниже
            return _unread(f"CalDAV: {e}")
    list_name = reminders_list_name().replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
tell application "Reminders"
    set output to ""
    try
        set rl to list "{list_name}"
        set done_reminders to (reminders of rl whose completed is true)
        repeat with r in done_reminders
            set b to body of r
            if b contains "[task_id:" then
                set output to output & b & "\\n---\\n"
            end if
        end repeat
    end try
    return output
end tell'''

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return _unread(f"AppleScript: {result.stderr.strip()}")

        raw = result.stdout
        ids = [int(m) for m in re.findall(r'\[task_id:(\d+)\]', raw)]
        return list(set(ids))  # дедупликация

    except Exception as e:
        return _unread(f"AppleScript: {e}")


def sync_completed_reminders():
    db.init_db()
    task_ids = get_completed_task_ids()

    if not task_ids:
        log.info("No completed reminders with task_id found")
        return

    log.info(f"Found {len(task_ids)} completed reminder(s): {task_ids}")

    synced = 0
    open_tasks = db.get_open_tasks(limit=1000)
    open_by_id = {t["id"]: t for t in open_tasks}

    for tid in task_ids:
        # Проверяем — задача ещё открыта?
        if tid not in open_by_id:
            log.info(f"Task #{tid} already closed, skipping")
            continue

        # Галочка — свидетельство ДЕЛАНИЯ, и для «сдать анализ» его достаточно.
        # Вопрос она закрыть не может: содержания в ней нет, а закрытый без
        # ответа вопрос неотличим от отвеченного (замер 2026-09-12: 7 вопросов,
        # 0 ответов; один из них закрыт именно отсюда). Триггер в БД отверг бы
        # такую запись — не доводим до исключения, а называем причину в логе.
        # Новые вопросы в Reminders не попадают вовсе; это про старые ремайндеры.
        if open_by_id[tid].get("type") == "question":
            log.info(f"Task #{tid} — вопрос: галочка в Reminders его не закрывает, "
                     f"ответ принимает бот (реплай или /done)")
            continue

        ok = db.resolve_task(tid, "completed via Reminders.app", "completed")
        if ok:
            log.info(f"Task #{tid} closed from Reminders sync")
            synced += 1
        else:
            log.warning(f"Task #{tid} not found in DB")

    log.info(f"Sync complete: {synced}/{len(task_ids)} tasks closed")


if __name__ == "__main__":
    sync_completed_reminders()
