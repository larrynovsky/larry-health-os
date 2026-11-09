"""Tenant side of docs/reference/agent_mail_bridge.md (transport only).

Owns agent_mail rows; bodies live only in the bridge directory. No model or
post-office calls. Taking a letter (state 'shown') is persisted before publishing its ack;
health does not echo letters to Telegram (owner decision 06.11: he sees the post office himself).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import tempfile
import uuid

from _time_inject import get_now
import config_db
import health_db
import mcp_tools
import notify
import secret_guard


def default_root() -> Path:
    """<каталог данных тенанта>/agent_mail — одно правило для контейнера владельца (туда смонтирован
    каталог моста хоста) и нативного процесса партнёра (обычная папка хоста). AGENT_MAIL_DIR — только тесты."""
    return Path(os.environ.get("AGENT_MAIL_DIR") or (Path(health_db.DB_PATH).parent.parent / "agent_mail")).expanduser().resolve()


def mail_dir() -> Path:
    """Process tenant's mount; a missing/incomplete mount is an error, not an empty inbox."""
    root = default_root()
    if not root.is_dir():
        raise FileNotFoundError("agent_mail: tenant bridge directory is missing; see docs/how-to/agent_mail.md")
    for name in ("out", "sent", "in", "ack", "acked"):
        if not (root / name).is_dir() or (root / name).resolve().parent != root:
            raise FileNotFoundError(f"agent_mail: invalid bridge directory {name}")
    return root


def _now() -> float:
    return get_now(timezone.utc).timestamp()


def _file(area: str, name: str) -> Path:
    parent = mail_dir() / area
    path = parent / name
    if path.is_symlink() or path.resolve().parent != parent:
        raise ValueError("agent_mail: unsafe bridge filename")
    return path


def _atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".mail-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _text(value, field: str, *, empty=False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()) or "\x00" in value:
        raise ValueError(f"agent_mail: invalid {field}")
    return value


def _key(key: str) -> str:
    if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{24}", key):   # номер почтамта: sha256[:24] (post.py::_key)
        raise ValueError("agent_mail: invalid inbox key")
    return key


def visible_names() -> frozenset[str]:
    """Формы имени, которые хозяин разрешил оставлять в письмах своему ассистенту
    (system_config agent_mail.name_visible, через запятую, в БД тенанта — не в коде).
    Решение владельца 06.11: «имя можно оставлять». Пусто — прячем всё, как раньше."""
    raw = config_db.get_config("agent_mail.name_visible") or ""
    return frozenset(p.strip().lower() for p in str(raw).split(",") if p.strip())


def send(to: list[str], subject: str, body: str, *, kind: str, reply_to=None, task_ids: list[int] | None = None) -> str:
    """Persist requested metadata, then atomically publish a guarded request; return its id.

    Publication failure is explicit (request_failed + raise). No automatic resend:
    a crash at publication has an ambiguous outcome; inspect id in out/ and sent/.
    """
    mail_dir()
    if not isinstance(to, list) or not to or any(
            not isinstance(peer, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", peer)
            for peer in to):
        raise ValueError("agent_mail: invalid recipients")
    _text(subject, "subject")
    _text(body, "body", empty=True)
    if len(body) > 8000:
        raise ValueError("agent_mail: body exceeds 8000 characters")
    if kind not in ("probe", "state", "lab_due", "regimen", "answer", "task", "nudge"):
        raise ValueError("agent_mail: invalid kind")
    if reply_to is not None:
        _text(reply_to, "reply_to")
    if task_ids is not None and (kind != "task" or not isinstance(task_ids, list)
            or not task_ids or any(type(i) is not int or i <= 0 for i in task_ids)):
        raise ValueError("agent_mail: invalid task_ids")
    keep = visible_names()
    body = mcp_tools.redact(body, keep)
    subject = mcp_tools.redact(subject, keep)
    if len(body) > 8000:
        raise ValueError("agent_mail: redacted body exceeds 8000 characters")
    request = dict(to=to, subject=subject, body=body, kind=kind, reply_to=reply_to)
    payload = json.dumps(request, ensure_ascii=False)
    # Scan values before JSON escaping too (a secret may contain non-ASCII characters).
    if secret_guard.find_secret_values("\n".join([*to, subject, body, reply_to or "", payload])):
        raise ValueError("agent_mail: secret guard blocked request")
    key, now = uuid.uuid4().hex, _now()
    with health_db.get_conn() as conn:
        conn.execute("INSERT INTO agent_mail "
                     "(id,direction,peer,subject,kind,state,detail,created_at,updated_at) "
                     "VALUES (?,'out',?,?,?,'requested',?,?,?)",
                     (key, json.dumps(to), subject, kind, json.dumps({"reply_to": reply_to, "task_ids": task_ids}), now, now))
    try:
        _atomic(_file("out", key + ".json"), payload)
    except OSError:
        with health_db.get_conn() as conn:
            conn.execute("UPDATE agent_mail SET state='request_failed',updated_at=? WHERE id=?",
                         (_now(), key))
        raise
    return key


def poll_sent() -> None:
    """Consume bridge receipts. Repeated polls report a failed letter only once."""
    for path in sorted((mail_dir() / "sent").glob("*.json")):
        key = path.stem
        if not re.fullmatch(r"[0-9a-f]{32}", key):
            raise ValueError("agent_mail: invalid receipt filename")
        receipt = json.loads(_file("sent", path.name).read_text(encoding="utf-8"))
        if not isinstance(receipt, dict) or receipt.get("id") != key:
            raise ValueError("agent_mail: receipt identity mismatch")
        statuses, refused = receipt.get("statuses"), receipt.get("refused")
        allowed = {"delivered", "held", "undelivered", "rejected"}
        reasons = {"foreign_owner", "unknown_rcpt", "from_field", "bad_json", "too_long"}
        if (not isinstance(statuses, dict) or any(not isinstance(v, str) or v not in allowed for v in statuses.values())
                or (refused is not None and (not isinstance(refused, str) or refused not in reasons))):
            raise ValueError("agent_mail: invalid receipt status")
        with health_db.get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM agent_mail WHERE id=? AND direction='out'", (key,)).fetchone()
            if row is None:
                raise ValueError("agent_mail: receipt without request")
            peers = set(json.loads(row["peer"]))
            if not refused and set(statuses) != peers:
                raise ValueError("agent_mail: incomplete recipient receipt")
            mid = receipt.get("message_id")
            if not refused:
                _text(mid, "message_id")
            bad = bool(refused) or any(s != "delivered" for s in statuses.values())
            detail = json.loads(row["detail"] or "{}")
            report = bad and not detail.get("fault_reported")
            detail.update(statuses=statuses, refused=refused,
                          fault_reported=bool(detail.get("fault_reported") or report))
            state = "refused" if refused else "sent:" + ("delivered" if not bad else "failed")
            conn.execute("UPDATE agent_mail SET message_id=?,state=?,detail=?,updated_at=? WHERE id=?",
                         (mid, state, json.dumps(detail), _now(), key))
        # At-most-once journal attempt: persist the flag before calling fault.
        if report:
            reason = refused or ",".join(sorted(set(statuses.values()) - {"delivered"}))
            notify.fault(f"agent_mail: request {key}: {reason}", person_key=None)


def pull_inbox() -> list[dict]:
    """Ingest idempotently; return all still-received letters, including retries.

    wrapped is transient, never written to SQLite. Shown receipts retry only ack.
    Malformed input raises; no file is deleted or acknowledged on validation failure.
    """
    pending = []
    seen = set()
    for path in sorted((mail_dir() / "in").glob("*.json"), key=lambda p: p.stem):
        key = _key(path.stem)
        item = json.loads(_file("in", path.name).read_text(encoding="utf-8"))
        if not isinstance(item, dict) or item.get("key") != key or item.get("auto_submitted") is not False:
            raise ValueError("agent_mail: invalid incoming envelope")
        for field in ("message_id", "from", "subject", "wrapped"):
            _text(item.get(field), field, empty=field in ("subject", "wrapped"))
        at = item.get("at")
        if isinstance(at, bool) or not isinstance(at, (float, int)) or not math.isfinite(at):
            raise ValueError("agent_mail: invalid incoming time")
        with health_db.get_conn() as conn:
            conn.execute("INSERT INTO agent_mail "
                         "(id,direction,message_id,peer,subject,kind,state,created_at,updated_at) "
                         "VALUES (?,'in',?,?,?,'','received',?,?) "
                         "ON CONFLICT(direction,message_id) DO NOTHING",
                         ("in:" + key, item["message_id"], item["from"], item["subject"], _now(), _now()))
            row = conn.execute("SELECT * FROM agent_mail WHERE direction='in' AND message_id=?",
                               (item["message_id"],)).fetchone()
        if row["id"] != "in:" + key:
            raise ValueError("agent_mail: message_id reused with different key")
        if row["state"] == "shown":
            mark_shown(key)
        elif row["state"] == "received" and item["message_id"] not in seen:
            pending.append({**item, "peer": row["peer"]})
            seen.add(item["message_id"])
    return pending


def record_taken(key: str) -> None:
    """Persist that health took the letter BEFORE ack. State keeps its historical name 'shown'
    (until 06.11 it meant a Telegram card); the hourly budget counts detail.shown_at."""
    _key(key)
    now = _now()
    with health_db.get_conn() as conn:
        changed = conn.execute("UPDATE agent_mail SET state='shown',detail=?,updated_at=? "
                               "WHERE id=? AND direction='in' AND state='received'",
                               (json.dumps(dict(shown_at=now)),
                                now, "in:" + key)).rowcount
        if not changed:
            raise ValueError("agent_mail: letter is not awaiting delivery")


def mark_shown(key: str) -> None:
    """Publish ack only for a persisted intake (record_taken); safe to repeat after a crash."""
    _key(key)
    with health_db.get_conn() as conn:
        row = conn.execute("SELECT * FROM agent_mail WHERE id=? AND direction='in'", ("in:" + key,)).fetchone()
        if row is None or row["state"] not in ("shown", "acked"):
            raise ValueError("agent_mail: cannot ack a letter health has not taken")
        if not json.loads(row["detail"] or "{}").get("shown_at"):
            raise ValueError("agent_mail: missing persisted receipt")
        if row["state"] == "acked":
            return
        if not _file("acked", key).exists():
            _atomic(_file("ack", key), "")
        conn.execute("UPDATE agent_mail SET state='acked',updated_at=? WHERE id=?", (_now(), "in:" + key))


def budget_left() -> int:
    """Rolling hour, persisted receipts; missing/invalid configuration refuses delivery."""
    value = config_db.get_config("agent_mail.max_per_hour")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or value != int(value):
        raise ValueError("agent_mail: invalid agent_mail.max_per_hour")
    with health_db.get_conn() as conn:
        n = conn.execute("SELECT count(*) FROM agent_mail WHERE direction='in' "
                         "AND state IN ('shown','acked') AND json_extract(detail,'$.shown_at') > ?",
                         (_now() - 3600,)).fetchone()[0]
    return max(0, int(value) - n)


@contextmanager
def delivery_slot():
    """One tenant delivery pass at a time, including across overlapping bot processes."""
    mail_dir()
    with (Path(health_db.DB_PATH).parent / "agent_mail_delivery.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            try:
                yield True
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


def unacked_count() -> int:
    """Received/shown letters older than 30 min; read model for integrity_tests."""
    with health_db.get_conn(read_only=True) as conn:
        return conn.execute("SELECT count(*) FROM agent_mail WHERE direction='in' "
                            "AND state IN ('received','shown') AND created_at < ?",
                            (_now() - 1800,)).fetchone()[0]


def sent_count(kind: str, seconds: float) -> int:
    """Сколько заявок этого вида создано за последние seconds (предел ответов agent_reply)."""
    with health_db.get_conn(read_only=True) as conn:
        return conn.execute("SELECT count(*) FROM agent_mail WHERE direction='out' AND kind=? AND created_at > ?",
                            (kind, _now() - seconds)).fetchone()[0]


def answered(message_id: str) -> bool:
    """Есть ли уже заявка-ответ на это письмо (повторный показ/рестарт не даёт второго ответа)."""
    with health_db.get_conn(read_only=True) as conn:
        return conn.execute("SELECT 1 FROM agent_mail WHERE direction='out' AND kind='answer' "
                            "AND json_extract(detail,'$.reply_to') = ? AND state != 'request_failed' LIMIT 1",
                            (message_id,)).fetchone() is not None


def task_handover_times() -> dict[int, float]:
    """First hand_over request time per task; receipt/ack state is irrelevant.

    Task IDs are persisted before spool publication, so transport failure cannot
    erase the fallback's link. Legacy requests without task_ids cannot be inferred.
    """
    with health_db.get_conn(read_only=True) as conn:
        rows = conn.execute("SELECT detail,created_at FROM agent_mail "
                            "WHERE direction='out' AND kind='task' ORDER BY created_at").fetchall()
    handed = {}
    for row in rows:
        for task_id in json.loads(row["detail"] or "{}").get("task_ids") or []:
            handed.setdefault(task_id, row["created_at"])
    return handed
