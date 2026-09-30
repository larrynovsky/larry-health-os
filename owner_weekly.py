"""Понедельничная сводка оператору и доказательство живости его канала.

run() после 09:10 по дому: максимум одно доставленное сообщение за понедельник,
до десяти строк, включая инженерную очередь; без событий — сердцебиение.
HEALTH_OWNER_WEEKLY изолирует журнал И квитанцию (соседний *.receipt.json).
flock общего журнала сериализует писателей и сборщики. Строки удаляются только
после подтверждения транспорта и атомарной записи квитанции. Смерть процесса
между отправкой и квитанцией может дать повтор: транспорт не идемпотентен.
"""
from __future__ import annotations

import fcntl
import json
import os
import textwrap
from datetime import datetime, timedelta
from pathlib import Path

import i18n
import notify
import owner_nag
import parked_decisions
from _time_inject import get_now
from triage_agent import PROVEN_CHANNELS

DIGEST_WEEKDAY = 0
SEND_HOUR, SEND_MINUTE = 9, 10


def last_scheduled_at(now: datetime) -> datetime:
    """Последний срок сводки по домашнему времени, включая неделю до 09:10 понедельника."""
    home = now.replace(tzinfo=owner_nag.HOME_TZ) if now.tzinfo is None else now.astimezone(owner_nag.HOME_TZ)
    due = (home - timedelta(days=(home.weekday() - DIGEST_WEEKDAY) % 7)).replace(
        hour=SEND_HOUR, minute=SEND_MINUTE, second=0, microsecond=0)
    return due - timedelta(days=7) if due > home else due


def receipt_path(base: Path | None = None) -> Path:
    """base изолирует датчик; env изолирует весь тестовый прогон."""
    if base is not None:
        return Path(base) / "logs" / "owner_weekly_last_run.json"
    if os.environ.get("HEALTH_OWNER_WEEKLY"):
        return notify.weekly_path().with_suffix(".receipt.json")
    return Path(__file__).resolve().parent / "logs" / "owner_weekly_last_run.json"


def run(now: datetime | None = None) -> dict:
    now = now or get_now(owner_nag.HOME_TZ)
    now = now.replace(tzinfo=owner_nag.HOME_TZ) if now.tzinfo is None else now.astimezone(owner_nag.HOME_TZ)
    due = last_scheduled_at(now)
    if now.weekday() != DIGEST_WEEKDAY or due.date() != now.date():
        return {"sent": False, "reason": "outside_schedule"}
    path = notify.weekly_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    receipt = receipt_path()
    with path.open("a+", encoding="utf-8") as journal:
        fcntl.flock(journal, fcntl.LOCK_EX)
        previous = {}
        if receipt.exists():
            try:
                previous = json.loads(receipt.read_text(encoding="utf-8"))
                if not isinstance(previous, dict):
                    raise ValueError("receipt must be an object")
            except (OSError, ValueError):
                notify.fault("owner_weekly: receipt unreadable", person_key=None)
                return {"sent": False, "reason": "receipt_unreadable"}
        if (previous.get("sent") is True and previous.get("date") == now.date().isoformat()
                and previous.get("via") in PROVEN_CHANNELS):
            return previous
        journal.seek(0)
        try:
            records = [json.loads(line) for line in journal if line.strip()]
            lines = list(dict.fromkeys(" ".join(r["text"].split())[:280] for r in records))
        except (ValueError, KeyError, TypeError, AttributeError):
            notify.fault("owner_weekly: journal unreadable", person_key=None)
            return {"sent": False, "reason": "journal_unreadable"}
        try:
            queue = owner_nag.engineering_queue_line(parked_decisions.list_open(now.date()), now.date())
        except Exception as exc:
            notify.fault(f"owner_weekly: queue read failed ({type(exc).__name__})", person_key=None)
            queue = i18n.t("owner.weekly.queue_unavailable")
        if queue:
            lines.insert(0, queue)
        # Standing-находки integrity (решения владельца 31.08, 13.09, 21.09: действия нет) не
        # паркуются ночным циклом — «стареют в недельной сводке» (night_cycle). До 4b их вёз
        # понедельничный дайджест триажа; теперь одна строка счётом, без перечня.
        try:
            import night_cycle
            standing = night_cycle.standing_count()
        except Exception as exc:  # noqa: BLE001 — сводка важнее этой строки; сбой в журнал
            notify.fault(f"owner_weekly: standing read failed ({type(exc).__name__})", person_key=None)
            standing = 0
        if standing:
            lines.append(i18n.t("owner.weekly.standing", count=standing))
        text = i18n.t("owner.weekly.empty")
        if lines:
            text = i18n.t("owner.weekly.heading") + "\n" + "\n".join(f"• {s}" for s in lines[:10])
            if len(lines) > 10:
                text += "\n" + i18n.t("owner.weekly.more", count=len(lines) - 10)
            # Резерв notify передаёт первые 1000 символов. Сокращаем строки заранее,
            # чтобы до него дошли все десять пунктов и счётчик оставшихся.
            if len(text) > notify.FALLBACK_TEXT_LIMIT:
                text = i18n.t("owner.weekly.heading") + "\n" + "\n".join(
                    "• " + textwrap.shorten(s, width=85, placeholder="…") for s in lines[:10])
                if len(lines) > 10:
                    text += "\n" + i18n.t("owner.weekly.more", count=len(lines) - 10)
        via = notify.notify_operator(text)
        result = {"ran_at": now.isoformat(), "date": now.date().isoformat(),
                  "sent": True, "via": via, "lines": len(lines)}
        receipt.parent.mkdir(parents=True, exist_ok=True)
        tmp = receipt.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as out:
            json.dump(result, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, receipt)
        if via in PROVEN_CHANNELS:
            journal.seek(0)
            journal.truncate()
            journal.flush()
            os.fsync(journal.fileno())
        else:
            notify.fault("owner_weekly: delivery unproven", person_key=None)
        return result


if __name__ == "__main__":
    run()
