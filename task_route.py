"""Задачи health — ассистенту хозяина вместо напоминаний iPhone (решение владельца 07.11.2026).

Маршрут — настройка тенанта system_config `tasks.route`:
  reminders — по умолчанию: публичная установка, у человека ассистента нет;
  assistant — дела письмом ассистенту из agent_mail.assistant (адрес — данные установки).
Анкеты и вопросы при любом маршруте идут в бот: на них отвечают там. Если человек не
ответил за `tasks.nudge_after_days` дней, ассистенту уходит ОДНА просьба напомнить
(отметка tasks.followup_sent_at ставится до письма: потерянное напоминание ничего не
ломает, повторное — спам; слова владельца 05.11).
"""
from __future__ import annotations

import math
import re
from datetime import date, timedelta, timezone

import agent_mail
import agent_reply
import config_db
import health_db
import task_agent
from _time_inject import get_now

ROUTES = ("reminders", "assistant")
BOT_TYPES = frozenset({"question", "assessment"})
PER_LETTER = 8   # дел в одном письме: тело письма ограничено 8000 знаков


def route() -> str:
    value = config_db.get_config("tasks.route", "reminders")
    if value not in ROUTES:
        raise ValueError(f"tasks.route: {value!r} — ожидается одно из {ROUTES}")
    return value


def _assistant() -> str:
    peers = sorted(agent_reply.allowed_peers())
    if not peers:
        raise ValueError("tasks.route=assistant, но agent_mail.assistant пуст")
    return peers[0]


ORIGIN_WORDS = {"doctor": "назначение врача (по документу)",
                "system": "предложение системы, не врача — решение за хозяином",
                "owner": "сам хозяин"}


def _one_line(text) -> str:
    """Текст задачи — в одну строку: перевод строки в нём мог бы изобразить служебную строку
    письма («Кто предложил: …»; ревью 07.11, находка 8)."""
    return " ".join(str(text or "").split())


def _item(t: dict) -> str:
    lines = [f"Задача #{t['id']}: {_one_line(t['content'])}"]
    if t.get("reason"):
        lines.append(f"Зачем: {_one_line(t['reason'])}")
    if t.get("deadline"):
        lines.append(f"Срок: {t['deadline']}")
    # Происхождение (п.2, 07.11): ассистент принял предложение системы за назначение врача.
    who = ORIGIN_WORDS.get(t.get("origin") or "")
    if who:
        lines.append(f"Кто предложил: {who}")
    return "\n".join(lines)


def hand_over(tasks: list[dict]) -> int:
    """Дела (не анкеты и не вопросы) — письмом ассистенту. Возвращает, сколько передано."""
    todo = [t for t in tasks if t.get("type") not in BOT_TYPES and t.get("id")]
    peer = _assistant() if todo else None
    for i in range(0, len(todo), PER_LETTER):
        part = todo[i:i + PER_LETTER]
        body = ("Новые дела по здоровью от health — вместо напоминаний в телефоне. Помоги их сделать: "
                "запиши, найди время, напомни, договорись — как сочтёшь нужным.\n\n"
                + "\n\n".join(_item(t) for t in part)
                + "\n\nКогда дело сделано или назначено, ответь на это письмо строкой "
                  "«задача #N: сделано» или «задача #N: назначено на ДД.ММ». "
                  "Решение — за тобой вместе с хозяином.")
        agent_mail.send([peer], f"health: дела ({len(part)})", body, kind="task",
                        task_ids=[t["id"] for t in part])
    return len(todo)


def nudge() -> int:
    """Анкеты и вопросы без ответа дольше tasks.nudge_after_days — одна просьба ассистенту."""
    if route() != "assistant":
        return 0
    days = config_db.get_config("tasks.nudge_after_days", 2)
    if isinstance(days, bool) or not isinstance(days, (int, float)) or days < 1:
        raise ValueError("tasks.nudge_after_days: ожидается число дней ≥ 1")
    before = (get_now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = health_db.get_unanswered_bot_tasks(before)
    if not rows:
        return 0
    peer = _assistant()
    health_db.mark_followup_sent([r["id"] for r in rows])
    kinds = {"assessment": "анкета", "question": "вопрос"}
    body = ("Хозяин не ответил в боте health:\n\n"
            + "\n".join(f"— {kinds[r['type']]} (задача #{r['id']}, пришла {str(r['sent_at'])[:10]}): "
                        f"{r['content'].splitlines()[0]}" for r in rows)
            + "\n\nНапомни ему, когда будет уместно. Ответ — в боте health, в том сообщении.")
    agent_mail.send([peer], f"health: напомни ответить ({len(rows)})", body, kind="nudge")
    return len(rows)


_DONE = re.compile(r"задач[аи]\s*#(\d+)\s*[:—-]\s*(сделано|выполнено|отменено|не нужно)", re.I)


def close_from_letter(letter: dict) -> list[int]:
    """«задача #N: сделано» в письме ассистента закрывает дело N (так просит письмо hand_over).
    Только от ассистента хозяина и только дела: анкеты и вопросы закрываются ответом в боте."""
    if letter.get("peer") not in agent_reply.allowed_peers():
        return []
    closed = []
    for num, word in _DONE.findall(letter.get("wrapped") or ""):
        task = health_db.get_task(int(num))
        if not task or task.get("status") != "open" or task.get("type") in BOT_TYPES:
            continue
        status = "completed" if word.lower() in ("сделано", "выполнено") else "dismissed"
        # Анализ закрывается результатом, не словом (п.4); отмену хозяина через ассистента — принимаем.
        if status == "completed" and task_agent.lab_task_closable_by_result(task):
            continue
        if health_db.resolve_task(int(num), f"ассистент {letter['peer']}: {word}", status=status):
            closed.append(int(num))
    return closed


def overdue_assistant_tasks() -> list[dict]:
    """Open handed-over deeds past deadline/grace or the no-deadline waiting period.

    Deadline is an ISO calendar date, evaluated after its end in UTC plus full
    grace days. No-deadline age uses the first request's UTC timestamp.
    """
    if route() != "assistant":
        return []
    grace = config_db.get_config("tasks.assistant_overdue_grace_days", 2)
    waiting = config_db.get_config("tasks.assistant_no_deadline_days", 14)
    for key, value, minimum in (("tasks.assistant_overdue_grace_days", grace, 0),
                                ("tasks.assistant_no_deadline_days", waiting, 1)):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < minimum or value != int(value)):
            raise ValueError(f"{key}: expected whole days >= {minimum}")
    now = get_now(timezone.utc)
    result = []
    for task_id, handed_at in agent_mail.task_handover_times().items():
        task = health_db.get_task(task_id)
        if (not task or task["status"] != "open" or task["type"] in BOT_TYPES
                or task.get("followup_sent_at")):
            continue
        deadline = task.get("deadline")
        if deadline:
            due = (now.date() - date.fromisoformat(deadline)).days > grace
        else:
            due = now.timestamp() - handed_at > waiting * 86400
        if due:
            result.append(task)
    return result
