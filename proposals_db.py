"""proposals_db.py — доменный модуль proposals. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import json
import i18n
import os
import logging
from datetime import date, timedelta
from pathlib import Path

from _time_inject import get_today

log = logging.getLogger(__name__)


def get_pending_proposals() -> list[dict]:
    """Возвращает все неподтверждённые пропозалы."""
    with _hdb.get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM problem_list_proposals
            WHERE status='pending'
            ORDER BY created_at DESC
        """).fetchall()
    return [dict(r) for r in rows]


# Ритм outbox предложений (job бота). Один дом: его читают регистрация job'а и датчик
# недоставки — порог датчика производный от ритма, своего числа у датчика нет.
DELIVERY_EVERY_S = 300


def get_undelivered_proposals() -> list[dict]:
    """Ждущие решения человека и ещё не доставленные ему (delivered_at IS NULL), старые первыми."""
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM problem_list_proposals WHERE status='pending' AND delivered_at IS NULL "
            "ORDER BY created_at, id").fetchall()
    return [dict(r) for r in rows]


def stuck_undelivered_proposals() -> tuple[int, list[int]]:
    """(ждут и не доставлены всего, id застрявших дольше двух ритмов outbox).

    Чистое ядро датчика integrity_tests.check_proposals_delivered (сам integrity_tests
    импортировать нельзя — импорт исполняет весь ночной монитор). Порог производный от
    DELIVERY_EVERY_S: один пропущенный запуск плюс запас на рестарт бота."""
    limit_days = 2 * DELIVERY_EVERY_S / 86400
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, julianday('now') - julianday(created_at) AS age "
            "FROM problem_list_proposals WHERE status='pending' AND delivered_at IS NULL"
        ).fetchall()
    return len(rows), [r["id"] for r in rows if (r["age"] or 0) > limit_days]


def mark_proposal_delivered(proposal_id: int, tg_message_id: int | None) -> None:
    """Квитанция доставки — ставится ПОСЛЕ успешной отправки (отказ оставляет в outbox)."""
    with _hdb.get_conn() as conn:
        conn.execute("UPDATE problem_list_proposals SET delivered_at=datetime('now'), "
                     "tg_message_id=? WHERE id=?", (tg_message_id, proposal_id))


_FIELD_LABELS = {"notes": "proposals.field.notes", "description": "proposals.field.description", "watch_trigger": "proposals.field.watch_trigger",
                 "watch_deadline": "proposals.field.watch_deadline", "status": "proposals.field.status", "priority": "proposals.field.priority"}


def _clip(v, n: int = 400) -> str:
    t = " ".join(str(v if v is not None else "—").split())
    return t if len(t) <= n else t[:n - 1] + "…"


def format_proposal_card(prop: dict) -> str:
    """Текст карточки предложения — ПРОСТОЙ текст, без разметки. Один дом для outbox и /report.

    Разметки нет намеренно (2026-09-23): первая живая доставка отбилась у всех 14 карточек
    «Can't parse entities» — в тексте модели бывают «_», «*», «`».

    Правка существующей проблемы называет проблему по названию, поле — по-русски,
    и показывает «сейчас» ИЗ БАЗЫ, а не old_value модели. Независимо выдуманный
    пример формы: «Демо-карточка; описание; сейчас: черновик → предложено: уточнить».
    Одного идентификатора и обрезанного текста недостаточно для различения правки
    и нового предложения."""
    import json as _j
    from _fmt_helpers import fmt_label
    rep = prop.get("repeats") or 1
    rep_s = i18n.t("proposals.repeat", count=rep) if rep > 1 else ""
    changes = _j.loads(prop["proposed"])
    if isinstance(changes, dict):
        changes = [changes]
    # Предложение куратора литературы — не правка списка, а статья к обсуждению: своя шапка
    # и свой текст (01.10: карточка такого типа выходила пустой — «предложение ничего не изменит»,
    # суть/обоснование/статья не печатались вовсе, а «Применить» список не меняет).
    lit = bool(changes) and all(c.get("action") == "literature_review_required" for c in changes)
    head = "proposals.lit.heading" if lit else "proposals.heading"
    lines = [i18n.t(head, source=prop['source'], repeat=rep_s), ""]
    with _hdb.get_conn() as conn:
        for ch in changes:
            action = ch.get("action", "")
            pid = ch.get("problem_id", "?")
            row = conn.execute("SELECT * FROM problem_list WHERE problem_id=?", (pid,)).fetchone()
            row = dict(row) if row else None
            if action == "literature_review_required":
                if row:
                    lines.append(i18n.t("proposals.lit.about", title=_clip(row.get("title"), 90)))
                lines.append(i18n.t("proposals.lit.summary", summary=_clip(ch.get("summary"), 400)))
                if ch.get("rationale"):
                    lines.append(i18n.t("proposals.reason", reason=_clip(ch["rationale"], 300)))
                if ch.get("source_title") or ch.get("source_pmid"):
                    lines.append(i18n.t("proposals.lit.source", title=_clip(ch.get("source_title"), 150),
                                        pmid=ch.get("source_pmid") or "—"))
                continue
            if action == "add":
                nv = ch.get("new_value") if isinstance(ch.get("new_value"), dict) else {}
                lines.append(i18n.t("proposals.new", title=ch.get('title') or nv.get('title', '')))
                _plain = ch.get("plain_summary") or nv.get("plain_summary")
                if _plain:
                    lines.append(i18n.t("proposals.plain", summary=_clip(_plain, 300)))
            elif row is None:
                lines.append(i18n.t("proposals.missing", problem_id=pid, action=action,
                                    old=_clip(ch.get('old_value'), 120), new=_clip(ch.get('new_value'), 200)))
            else:
                closed = i18n.t("proposals.closed") if row.get("status") == "resolved" else ""
                lines.append(f"📝 «{_clip(row.get('title'), 90)}»{closed}")
                if action == "resolve":
                    lines.append(i18n.t("proposals.resolve"))
                else:
                    fld = ch.get("field") or ("status" if action == "update_status" else "")
                    lines.append(i18n.t("proposals.change",
                                        field=i18n.t(_FIELD_LABELS[fld]) if fld in _FIELD_LABELS else (fld or action)))
                    current = fmt_label(row.get(fld), "problems.status") if fld == "status" else _clip(row.get(fld))
                    lines.append(i18n.t("proposals.current", value=current))
                    whole = i18n.t("proposals.replace_all") if fld in ("notes", "description", "watch_trigger") else ""
                    proposed = fmt_label(ch.get('new_value'), "problems.status") if fld == "status" else _clip(ch.get('new_value'))
                    lines.append(i18n.t("proposals.proposed", whole=whole, value=proposed))
            if ch.get("reason"):
                lines.append(i18n.t("proposals.reason", reason=_clip(ch['reason'], 300)))
    if lit:
        lines += ["", i18n.t("proposals.lit.buttons")]
    return "\n".join(lines)


def apply_proposal(proposal_id: int) -> int:
    """
    Применяет пропозал: вносит изменения в problem_list.
    Возвращает количество применённых изменений.
    """
    import json as _j
    from datetime import datetime as _dt

    with _hdb.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM problem_list_proposals WHERE id=?", (proposal_id,)
        ).fetchone()
        if not row:
            return 0

        changes = _j.loads(dict(row)["proposed"])
        applied = 0
        now = str(get_today())

        for ch in changes:
            action = ch.get("action")

            if action == "update_status":
                conn.execute(
                    "UPDATE problem_list SET status=?, last_updated=? WHERE problem_id=?",
                    (ch["new_value"], now, ch["problem_id"])
                )
                applied += 1

            elif action == "update_field":
                field = ch.get("field")
                # Whitelist безопасных полей
                allowed = {"status", "priority", "watch_trigger",
                           "watch_deadline", "notes", "description",
                           "review_date"}
                if field in allowed:
                    val = ch["new_value"]
                    if field == "review_date" and isinstance(val, int):
                        # GP передал кол-во дней — вычисляем абсолютную дату
                        from datetime import timedelta as _td2
                        val = str(get_today() + _td2(days=val))
                    conn.execute(
                        f"UPDATE problem_list SET {field}=?, last_updated=? "
                        "WHERE problem_id=?",
                        (val, now, ch["problem_id"])
                    )
                    applied += 1

            elif action == "add":
                # GP может положить поля либо напрямую в ch, либо в ch["new_value"]
                _nv = ch.get("new_value")
                _src = dict(ch)
                if isinstance(_nv, dict):
                    _src.update(_nv)  # new_value перекрывает top-level если есть
                # Дубль записи (2026-09-01): не только тот же problem_id (id придумывает модель —
                # P007/<состояние>_<год>/new_<аналит>_gap за одну и ту же проблему), но и
                # живая проблема с тем же условием clinical_kb / заголовком.
                import problems_db as _pdb
                dup = _pdb.existing_problem_for(ch, conn)
                # problem_id придумывает модель и переиспользует (один id у двух разных проблем):
                # занятый id — не дубль ПРОБЛЕМЫ, а коллизия имени → свой slug из заголовка.
                _pid = _src.get("problem_id") or _src.get("id") or ""
                _taken = conn.execute("SELECT 1 FROM problem_list WHERE problem_id=?", (_pid,)).fetchone()
                if not dup and (not _pid or _taken):
                    import re as _re
                    _base = _re.sub(r"[^a-zа-яё0-9]+", "_", str(_src.get("title") or "problem").lower())[:50].strip("_") or "problem"
                    _pid, _k = _base, 2
                    while conn.execute("SELECT 1 FROM problem_list WHERE problem_id=?", (_pid,)).fetchone():
                        _pid, _k = f"{_base}_{_k}", _k + 1
                    _src["problem_id"] = _pid
                exists = dup
                if not exists:
                    from datetime import timedelta as _td3
                    _rd = _src.get("review_date")
                    _rid = _src.get("review_in_days")
                    if _rid and not _rd:
                        _rd = str(get_today() + _td3(days=_rid))
                    conn.execute("""
                        INSERT INTO problem_list
                        (problem_id, title, description, status, priority,
                         domain, first_seen, last_updated, watch_trigger,
                         watch_deadline, notes, review_date, plain_summary)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        _src.get("problem_id"), _src.get("title"),
                        _src.get("description"), _src.get("status", "active_monitoring"),
                        _src.get("priority", 3), _src.get("domain", "other"),
                        now, now,
                        _src.get("watch_trigger"), _src.get("watch_deadline"),
                        _src.get("reason", ""), _rd, _src.get("plain_summary")
                    ))
                    applied += 1

            elif action == "resolve":
                conn.execute(
                    "UPDATE problem_list SET status='resolved', last_updated=? "
                    "WHERE problem_id=?",
                    (now, ch["problem_id"])
                )
                applied += 1

        conn.execute(
            "UPDATE problem_list_proposals SET status='approved', reviewed_at=datetime('now') "
            "WHERE id=?",
            (proposal_id,)
        )
        return applied


def reject_proposal(proposal_id: int, note: str = None):
    """Отклоняет пропозал без изменений в problem_list."""
    with _hdb.get_conn() as conn:
        conn.execute(
            "UPDATE problem_list_proposals "
            "SET status='rejected', reviewed_at=datetime('now'), review_note=? "
            "WHERE id=?",
            (note, proposal_id)
        )


# Source-aware пороги expiry (2026-07-06): literature_curator — низкоставочные
# advisory-предложения, релевантность статьи протухает быстрее → короче срок.
# Остальные источники — общий default (параметр days).
SOURCE_EXPIRY_DAYS = {"literature_curator": 30}


def expire_aged_proposals(days: int = 60) -> int:
    """Lifecycle: pending старше порога → 'expired'. Возврат — сколько истекло.

    Системный дренаж очереди (2026-07-04): досушивает то, что supersede-at-generation
    не ловит (literature-advisory с problem_id=null, заброшенные). Порог — source-aware
    (SOURCE_EXPIRY_DAYS, иначе days): literature релевантность протухает быстрее.
    Обратимо (статус, запись цела). Запускается в survivorship-пайплайне.
    """
    with _hdb.get_conn() as conn:
        # Возраст — от ПОСЛЕДНЕГО предложения (2026-09-01): то, что предлагают восьмую неделю,
        # не «устарело»; created_at — для строк до миграции.
        rows = conn.execute(
            "SELECT id, source, julianday('now') - julianday(COALESCE(last_proposed_at, created_at)) AS age "
            "FROM problem_list_proposals WHERE status='pending'"
        ).fetchall()
        expired = 0
        for r in rows:
            threshold = SOURCE_EXPIRY_DAYS.get(r["source"], days)
            if r["age"] is not None and r["age"] > threshold:
                conn.execute(
                    "UPDATE problem_list_proposals "
                    "SET status='expired', reviewed_at=datetime('now'), review_note=? "
                    "WHERE id=?",
                    (f'авто-expiry: >{threshold}д без реакции (lifecycle)', r["id"]),
                )
                expired += 1
        return expired


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
