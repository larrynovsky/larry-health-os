"""visual_db.py — доменный модуль visual-intake (symptom-intake). Тонкая обёртка над health_db.

ДОМЕН-АГНОСТИЧНО: никаких имён областей медицины/болезней здесь (§9 — домены,
регионы и параметры диалога живут в таблице visual_domains как ДАННЫЕ). Схема
создаётся в health_db._migrate_visual_intake (init_db). Запись — только primary
(get_conn guard, §8).
"""
from __future__ import annotations

import json as _json
import logging

import health_db as _hdb

log = logging.getLogger(__name__)


# ── visual_domains (§9-данные) ──────────────────────────────────────────────

def get_visual_domain(domain: str) -> dict | None:
    with _hdb.get_conn() as conn:
        row = conn.execute(
            "SELECT domain, region_options, dialog_params FROM visual_domains WHERE domain=?",
            (domain,),
        ).fetchone()
    return dict(row) if row else None


def upsert_visual_domain(domain: str, region_options=None, dialog_params=None) -> None:
    ro = _json.dumps(region_options, ensure_ascii=False) if region_options is not None else None
    dp = _json.dumps(dialog_params, ensure_ascii=False) if dialog_params is not None else None
    with _hdb.get_conn() as conn:
        conn.execute(
            """INSERT INTO visual_domains (domain, region_options, dialog_params)
               VALUES (?, ?, ?)
               ON CONFLICT(domain) DO UPDATE SET
                   region_options = excluded.region_options,
                   dialog_params  = excluded.dialog_params""",
            (domain, ro, dp),
        )


# ── visual_case ─────────────────────────────────────────────────────────────

def open_visual_case(chat_id: int, tenant: str | None = None,
                     domain: str | None = None, region: str | None = None) -> int:
    with _hdb.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO visual_case (chat_id, tenant, domain, region) VALUES (?, ?, ?, ?)",
            (chat_id, tenant, domain, region),
        )
        return cur.lastrowid


def get_open_visual_case(chat_id: int) -> dict | None:
    with _hdb.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM visual_case WHERE chat_id=? AND status='open' "
            "ORDER BY id DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
    return dict(row) if row else None


def update_visual_case_session(case_id: int, session_dict: dict) -> None:
    sj = _json.dumps(session_dict, ensure_ascii=False)
    with _hdb.get_conn() as conn:
        conn.execute(
            "UPDATE visual_case SET session_json=?, updated_at=datetime('now') WHERE id=?",
            (sj, case_id),
        )


def set_visual_case_domain(case_id: int, domain: str | None, region: str | None) -> None:
    with _hdb.get_conn() as conn:
        conn.execute(
            "UPDATE visual_case SET domain=?, region=?, updated_at=datetime('now') WHERE id=?",
            (domain, region, case_id),
        )


def link_hypothesis_to_case(case_id: int, memory_id: int) -> None:
    """Гипотеза собрана и ушла в needs_specialist — привязка + status='handed_off'."""
    with _hdb.get_conn() as conn:
        conn.execute(
            "UPDATE visual_case SET hypothesis_memory_id=?, status='handed_off', "
            "updated_at=datetime('now') WHERE id=?",
            (memory_id, case_id),
        )


def close_visual_case(case_id: int) -> None:
    with _hdb.get_conn() as conn:
        conn.execute(
            "UPDATE visual_case SET status='closed', updated_at=datetime('now') WHERE id=?",
            (case_id,),
        )


# ── visual_photo ────────────────────────────────────────────────────────────

def add_visual_photo(case_id: int, path: str, sha256: str,
                     exif_stripped: bool = False) -> int | None:
    """Добавляет снимок. Дедуп по sha256 (UNIQUE): повтор → None."""
    with _hdb.get_conn() as conn:
        row = conn.execute("SELECT id FROM visual_photo WHERE sha256=?", (sha256,)).fetchone()
        if row:
            return None
        cur = conn.execute(
            "INSERT INTO visual_photo (case_id, path, sha256, exif_stripped) VALUES (?, ?, ?, ?)",
            (case_id, path, sha256, 1 if exif_stripped else 0),
        )
        return cur.lastrowid


def get_case_photos(case_id: int) -> list[dict]:
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM visual_photo WHERE case_id=? ORDER BY taken_at", (case_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Сенсоры целостности/liveness (используются WP5; определены сейчас) ───────

def get_visual_orphans() -> dict:
    """Инвариант: фото без кейса; кейс без фото; handed_off без гипотезы."""
    with _hdb.get_conn() as conn:
        photo_no_case = conn.execute(
            "SELECT COUNT(*) FROM visual_photo p "
            "LEFT JOIN visual_case c ON p.case_id=c.id WHERE c.id IS NULL"
        ).fetchone()[0]
        case_no_photo = conn.execute(
            "SELECT COUNT(*) FROM visual_case c "
            "LEFT JOIN visual_photo p ON p.case_id=c.id WHERE p.id IS NULL"
        ).fetchone()[0]
        handed_no_hyp = conn.execute(
            "SELECT COUNT(*) FROM visual_case "
            "WHERE status='handed_off' AND hypothesis_memory_id IS NULL"
        ).fetchone()[0]
    return {"photo_no_case": photo_no_case, "case_no_photo": case_no_photo,
            "handed_off_no_hypothesis": handed_no_hyp}


def get_stale_visual_cases(hours: int = 72) -> list[dict]:
    """Открытые кейсы без движения дольше N часов (застрявший диалог, liveness)."""
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, chat_id, tenant, opened_at, updated_at FROM visual_case "
            "WHERE status='open' AND updated_at < datetime('now', ?)",
            (f"-{int(hours)} hours",),
        ).fetchall()
    return [dict(r) for r in rows]


# ── этап 2: follow-up серии ──────────────────────────────────────────────────

def get_cases_awaiting_followup_reminder(days: int) -> list[dict]:
    """handed_off кейсы, у которых самое свежее фото старше N дней и напоминание ещё
    не отправлено (followup_reminded_at пусто или старше последнего фото). Для джобы."""
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            """SELECT c.id, c.chat_id, c.tenant, c.region, c.hypothesis_memory_id,
                      MAX(p.taken_at) AS last_photo
               FROM visual_case c JOIN visual_photo p ON p.case_id=c.id
               WHERE c.status='handed_off'
               GROUP BY c.id
               HAVING last_photo < datetime('now', ?)
                  AND (c.followup_reminded_at IS NULL OR c.followup_reminded_at < last_photo)""",
            (f"-{int(days)} days",),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_followup_reminded(case_id: int) -> None:
    """Напоминание отправлено → ждём свежее фото (status=awaiting_followup)."""
    with _hdb.get_conn() as conn:
        conn.execute(
            "UPDATE visual_case SET status='awaiting_followup', "
            "followup_reminded_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
            (case_id,),
        )


def get_awaiting_followup_case(chat_id: int) -> dict | None:
    """Кейс этого чата, ждущий свежее фото (роутинг follow-up снимка). Фото разрешает
    кейс в ЛЮБОМ ожидающем статусе — и awaiting_followup, и awaiting_decision."""
    with _hdb.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM visual_case WHERE chat_id=? "
            "AND status IN ('awaiting_followup','awaiting_decision') "
            "ORDER BY id DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
    return dict(row) if row else None


def get_cases_awaiting_decision(after_days: int) -> list[dict]:
    """awaiting_followup кейсы, где напоминание отправлено дольше after_days назад и
    свежее фото так и не пришло → пора спросить «закрыть или продолжить»."""
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, chat_id, tenant, region FROM visual_case "
            "WHERE status='awaiting_followup' AND followup_reminded_at IS NOT NULL "
            "AND followup_reminded_at < datetime('now', ?)",
            (f"-{int(after_days)} days",),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_decision_requested(case_id: int) -> None:
    """Запрос «закрыть/продолжить» отправлен → ждём выбор (status=awaiting_decision)."""
    with _hdb.get_conn() as conn:
        conn.execute(
            "UPDATE visual_case SET status='awaiting_decision', "
            "decision_requested_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
            (case_id,),
        )


def get_cases_decision_expired(after_days: int) -> list[dict]:
    """awaiting_decision кейсы без выбора дольше after_days → авто-закрытие (дефолт «закрыть»)."""
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, chat_id, tenant, region FROM visual_case "
            "WHERE status='awaiting_decision' AND decision_requested_at IS NOT NULL "
            "AND decision_requested_at < datetime('now', ?)",
            (f"-{int(after_days)} days",),
        ).fetchall()
    return [dict(r) for r in rows]


def resume_case_after_followup(case_id: int) -> None:
    """Свежее фото пришло → снова handed_off (7-дневные часы перевзведутся от нового фото)."""
    with _hdb.get_conn() as conn:
        conn.execute(
            "UPDATE visual_case SET status='handed_off', updated_at=datetime('now') WHERE id=?",
            (case_id,),
        )
