"""protocol_fit.py — впишется ли новый протокол в жизнь: спросить ассистента хозяина до карточки.

# INTENT: protocol_fit — замысел и инварианты в subsystem_intent.yaml.

Решение владельца 11.11 (нить protocol-fit, этап Э3 плана assistant-loop): у рождённого протокола
карточка приходит владельцу одна — с ответом ассистента («впишется» / «не впишется — почему») или,
если ответа нет за protocols.fit_wait_hours, с пометкой «не ответил». Отдельного эха переписки в
Telegram нет (решение владельца 06.11: почтамт он видит сам) — ответ едет внутри карточки.

Ответ ассистента — улика, а не действие (C-142): код из письма не пишет в protocols ничего,
протокол не снимается и не меняется. Хранится с source='assistant_report'.
Ожидание живёт в базе, поэтому переживает рестарт; карточка уходит ровно один раз.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import config_db
import health_db
import i18n
from _time_inject import get_now

SOURCE = "assistant_report"
FIT_HINT = "проверка #P: впишется | проверка #P: не впишется — почему"
_FIT = re.compile(r"проверка\s*#\s*(\d+)\s*:\s*(не\s+впишется|впишется)\s*[—–:,-]?\s*([^\n]*)",
                  re.IGNORECASE)
_REASON_MAX = 500
_WORDS_MAX = 1500   # слова ассистента в карточке: Telegram держит 4096 знаков на сообщение
_STAMP = "%Y-%m-%dT%H:%M:%S"


def _wait() -> timedelta:
    value = config_db.get_config("protocols.fit_wait_hours")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError("protocols.fit_wait_hours: ожидается положительное число часов")
    return timedelta(hours=float(value))


def _card(protocol_id: int, protocol: dict) -> str:
    return "\n".join([
        i18n.t("protocols.reply.created", protocol_id=protocol_id), "",
        str(protocol.get("title") or ""), "",
        i18n.t("protocols.reply.behavior", behavior=protocol.get("behavior") or "—"),
        i18n.t("protocols.reply.frequency", frequency=protocol.get("frequency") or "—"),
        i18n.t("protocols.reply.rationale", rationale=protocol.get("rationale") or "—"),
    ])


def _item(protocol_id, protocol: dict) -> str:
    return i18n.t("protocol_fit.letter.item", protocol_id=protocol_id, title=protocol.get("title") or "",
                  behavior=protocol.get("behavior") or "—", frequency=protocol.get("frequency") or "—")


def request(protocol_id: int, protocol: dict, card: str | None = None) -> bool:
    """Спросить ассистента хозяина и придержать карточку. False — спрашивать некого: карточку
    вызывающий показывает сразу, как раньше. Сбой отправки — исключение вызывающему (громко)."""
    import agent_mail
    import agent_reply
    peers = sorted(agent_reply.allowed_peers())
    if not peers:
        return False
    others = [p for p in health_db.get_active_protocols() if p["id"] != protocol_id]
    body = "\n\n".join([
        i18n.t("protocol_fit.letter.intro"),
        _item(protocol_id, protocol),
        i18n.t("protocol_fit.letter.others") if others else i18n.t("protocol_fit.letter.no_others"),
        *(_item(p["id"], p) for p in others),
        i18n.t("protocol_fit.letter.ask", hint=FIT_HINT.replace("#P", f"#{protocol_id}")),
    ])
    key = agent_mail.send([peers[0]], i18n.t("protocol_fit.letter.subject", protocol_id=protocol_id),
                          body, kind="protocol_fit")
    with health_db.get_conn() as conn:
        conn.execute("INSERT INTO protocol_fit_checks (protocol_id, peer, letter_key, card, asked_at, source) "
                     "VALUES (?,?,?,?,?,?)", (protocol_id, peers[0], key, card or _card(protocol_id, protocol),
                                              get_now().strftime(_STAMP), SOURCE))
    return True


def parse_fit(text: str) -> list[dict]:
    """Строки «проверка #P: впишется» / «проверка #P: не впишется — почему». Прочее — мимо."""
    out = []
    for pid, word, reason in _FIT.findall(text or ""):
        verdict = "conflict" if word.lower().startswith("не") else "fits"
        out.append(dict(protocol_id=int(pid), verdict=verdict, reason=reason.strip()[:_REASON_MAX] or None))
    return out


def _letter_text(wrapped: str) -> str:
    """Слова ассистента без конверта почтамта (<письмо …> … </письмо> и инструкций после него)."""
    # Заголовок конверта — одна строка; внутри атрибута message-id="<…>" тоже есть «>» (живое письмо 11.11).
    m = re.search(r"<письмо\b[^\n]*>\n(.*?)\s*</письмо>", wrapped or "", re.DOTALL)
    return (m.group(1) if m else (wrapped or "")).strip()


def record_fit_from_letter(letter: dict) -> list[dict]:
    """Только ассистент хозяина и только открытая проверка этого протокола. В protocols не пишет.

    Строка формата FIT_HINT даёт вердикт. Живой ответ 11.11 пришёл своими словами («Частично
    впишется…», без строки формата) — поэтому письмо, где назван номер протокола с открытой
    проверкой, тоже ответ: в карточку идут его слова как есть, код их не классифицирует."""
    import agent_reply
    if letter.get("peer") not in agent_reply.allowed_peers():
        return []
    wrapped = letter.get("wrapped") or ""
    now, saved = get_now().strftime(_STAMP), []
    with health_db.get_conn() as conn:
        for r in parse_fit(wrapped):
            cur = conn.execute(
                "UPDATE protocol_fit_checks SET verdict=?, reason=?, answered_at=?, answer_letter=? "
                "WHERE protocol_id=? AND answered_at IS NULL AND delivered_at IS NULL",
                (r["verdict"], r["reason"], now, letter.get("key"), r["protocol_id"]))
            if cur.rowcount:
                saved.append(r)
        text = _letter_text(wrapped)
        mentioned = f"{letter.get('subject') or ''}\n{text}"
        for (pid,) in conn.execute("SELECT protocol_id FROM protocol_fit_checks "
                                   "WHERE answered_at IS NULL AND delivered_at IS NULL").fetchall():
            if not re.search(rf"#\s*{pid}\b", mentioned) or not text:
                continue
            words = text[:_WORDS_MAX]
            conn.execute("UPDATE protocol_fit_checks SET reason=?, answered_at=?, answer_letter=? "
                         "WHERE protocol_id=? AND answered_at IS NULL AND delivered_at IS NULL",
                         (words, now, letter.get("key"), pid))
            saved.append(dict(protocol_id=pid, verdict=None, reason=words))
    return saved


def _evidence(row) -> str:
    if row["verdict"] == "fits":
        return i18n.t("protocol_fit.card.fits")
    if row["verdict"] == "conflict":
        return i18n.t("protocol_fit.card.conflict", reason=row["reason"] or "—")
    if row["answered_at"]:
        return i18n.t("protocol_fit.card.words", text=row["reason"] or "—")
    return i18n.t("protocol_fit.card.no_answer", hours=round(_wait().total_seconds() / 3600, 1))


def due_cards(now: datetime | None = None) -> list[dict]:
    """Карточки к отправке: ответ пришёл или срок ожидания вышел. Отправленные не повторяются."""
    cutoff = ((now or get_now()) - _wait()).strftime(_STAMP)
    with health_db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM protocol_fit_checks WHERE delivered_at IS NULL "
                            "AND (answered_at IS NOT NULL OR asked_at <= ?) ORDER BY id", (cutoff,)).fetchall()
    return [dict(id=r["id"], protocol_id=r["protocol_id"], text=f"{r['card']}\n\n{_evidence(r)}") for r in rows]


def mark_delivered(check_id: int) -> bool:
    with health_db.get_conn() as conn:
        return conn.execute("UPDATE protocol_fit_checks SET delivered_at=? WHERE id=? AND delivered_at IS NULL",
                            (get_now().strftime(_STAMP), check_id)).rowcount > 0


def awaiting(protocol_id: int) -> bool:
    """Карточка этого протокола придержана до ответа ассистента."""
    with health_db.get_conn() as conn:
        return conn.execute("SELECT 1 FROM protocol_fit_checks WHERE protocol_id=? AND delivered_at IS NULL",
                            (protocol_id,)).fetchone() is not None


def fit_line(protocol_id: int) -> str | None:
    """Последний ответ ассистента по протоколу — подписан как пересказ (дашборд, пакет консилиума)."""
    with health_db.get_conn() as conn:
        row = conn.execute("SELECT * FROM protocol_fit_checks WHERE protocol_id=? AND answered_at IS NOT NULL "
                           "ORDER BY id DESC LIMIT 1", (protocol_id,)).fetchone()
    if not row:
        return None
    return i18n.t("protocol_fit.line", protocol_id=protocol_id, date=str(row["answered_at"])[:10],
                  evidence=_evidence(row))


def overdue(now: datetime | None = None) -> list[int]:
    """Проверки, чья карточка не ушла через срок + запас доставки — датчик. Нет таблицы — пусто."""
    with health_db.get_conn() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='protocol_fit_checks'").fetchone():
            return []
    slack = config_db.get_config("protocols.fit_delivery_slack_hours")
    if isinstance(slack, bool) or not isinstance(slack, (int, float)) or slack <= 0:
        raise ValueError("protocols.fit_delivery_slack_hours: ожидается положительное число часов")
    cutoff = ((now or get_now()) - _wait() - timedelta(hours=float(slack))).strftime(_STAMP)
    with health_db.get_conn() as conn:
        return [r[0] for r in conn.execute("SELECT id FROM protocol_fit_checks WHERE delivered_at IS NULL "
                                           "AND asked_at <= ?", (cutoff,))]
