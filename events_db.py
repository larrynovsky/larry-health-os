"""events_db.py — доменный модуль events. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import json
import os
import logging
from datetime import date, timedelta
from pathlib import Path


log = logging.getLogger(__name__)


# Типы событий медкарты, у которых есть диагностическая часть (diagnostic_events). ОДИН дом:
# до 07.10 тот же список жил в трёх местах (import_medical_events, api_events, шаблон медкарты).
# Эндоскопия и гистология — свои типы с 07.10 (решение владельца): до этого промпт разбора
# документов относил такие документы к «Визуализации».
DIAGNOSTIC_TYPES = ("lab_result", "imaging", "endoscopy", "pathology", "procedure")
# Вкладки медкарты, в порядке показа (подписи — i18n dashboard.medical_record.type.<тип>).
RECORD_TABS = ("encounter", "lab_result", "imaging", "endoscopy", "pathology", "procedure")


def save_event(
    event_type: str,
    effective_date: str,
    status: str = "completed",
    performer: str = None,
    performer_role: str = None,
    location: str = None,
    episode_id: int = None,
    recorded_by: str = "patient",
    attachments: list = None,
    notes: str = None,
    effective_time: str = None,
    encounter: dict = None,
    diagnostic: dict = None,
    problem_ids: list = None,
) -> int:
    """Создаёт событие в events + subtable атомарно. Возвращает event_id.

    encounter: dict с ключами class|specialty|reason_text|chief_complaint|
               duration_minutes|subjective|objective|assessment|plan
    diagnostic: dict с ключами type|modality|ordering_event_id|
                interpreted_report|abnormal_flags|raw_values_ref
    problem_ids: list of (problem_id_str, link_type_str)
    """
    import json as _json
    _att = _json.dumps(attachments or [])
    with _hdb.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO events
               (event_type, effective_date, effective_time, status,
                performer, performer_role, location, episode_id,
                recorded_by, attachments, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (event_type, effective_date, effective_time, status,
             performer, performer_role, location, episode_id,
             recorded_by, _att, notes)
        )
        evt_id = cur.lastrowid

        if encounter:
            conn.execute(
                """INSERT INTO encounters
                   (event_id, class, specialty, reason_text, chief_complaint,
                    duration_minutes, subjective, objective, assessment, plan)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (evt_id,
                 encounter.get("class"), encounter.get("specialty"),
                 encounter.get("reason_text"), encounter.get("chief_complaint"),
                 encounter.get("duration_minutes"),
                 encounter.get("subjective"), encounter.get("objective"),
                 encounter.get("assessment"), encounter.get("plan"))
            )

        if diagnostic:
            flags = diagnostic.get("abnormal_flags", [])
            refs  = diagnostic.get("raw_values_ref", [])
            conn.execute(
                """INSERT INTO diagnostic_events
                   (event_id, type, modality, ordering_event_id,
                    raw_values_ref, interpreted_report, abnormal_flags)
                   VALUES (?,?,?,?,?,?,?)""",
                (evt_id,
                 diagnostic.get("type"), diagnostic.get("modality"),
                 diagnostic.get("ordering_event_id"),
                 _json.dumps(refs if isinstance(refs, list) else []),
                 diagnostic.get("interpreted_report"),
                 _json.dumps(flags if isinstance(flags, list) else []))
            )

        if problem_ids:
            for pid, ltype in problem_ids:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO event_problem_links "
                        "(event_id, problem_id, link_type) VALUES (?,?,?)",
                        (evt_id, pid, ltype or "reason")
                    )
                except Exception:
                    pass  # silent-ok: duplicate link

        conn.commit()
    return evt_id


def get_events(
    n: int = 20,
    event_type: str = None,
    status: str = None,
    date_from: str = None,
    date_to: str = None,
    episode_id: int = None,
) -> list:
    """Последние N событий медкарты с деталями encounters/diagnostics.

    Возвращает list of dict с полями events + encounter.* + diagnostic.*
    (все поля с префиксом из subtable если применимо).
    """
    conds, params = ["1=1"], []
    if event_type:
        conds.append("e.event_type = ?"); params.append(event_type)
    if status:
        conds.append("e.status = ?");     params.append(status)
    if date_from:
        conds.append("e.effective_date >= ?"); params.append(date_from)
    if date_to:
        conds.append("e.effective_date <= ?"); params.append(date_to)
    if episode_id is not None:
        conds.append("e.episode_id = ?"); params.append(episode_id)
    where = " AND ".join(conds)
    params.append(n)

    with _hdb.get_conn() as conn:
        rows = conn.execute(f"""
            SELECT
                e.id, e.event_type, e.effective_date, e.effective_time,
                e.status, e.performer, e.performer_role, e.location,
                e.episode_id, e.recorded_by, e.notes,
                enc.class      AS enc_class,
                enc.specialty,
                enc.reason_text,
                enc.chief_complaint,
                enc.subjective, enc.objective, enc.assessment, enc.plan,
                diag.type      AS diag_type,
                diag.modality,
                diag.ordering_event_id,
                diag.interpreted_report,
                diag.abnormal_flags
            FROM events e
            LEFT JOIN encounters       enc  ON enc.event_id  = e.id
            LEFT JOIN diagnostic_events diag ON diag.event_id = e.id
            WHERE {where}
            ORDER BY e.effective_date DESC, e.id DESC
            LIMIT ?
        """, params).fetchall()
    return [dict(r) for r in rows]


def get_context_events(date_from: str, date_to: str = None,
                       category: str = None, source: str = None) -> list[dict]:
    """Возвращает события контекста за период."""
    if date_to is None:
        date_to = date_from
    with _hdb.get_conn() as conn:
        q = "SELECT * FROM context_events WHERE date BETWEEN ? AND ?"
        params = [date_from, date_to]
        if category:
            q += " AND category=?"
            params.append(category)
        if source:
            q += " AND source=?"
            params.append(source)
        q += " ORDER BY date, ts"
        rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def save_context_event(date_str: str, source: str, category: str,
                       key: str = None, value_num: float = None,
                       value_text: str = None, ts: str = None,
                       tags: list = None, period_id: int = None) -> int:
    """Сохраняет событие жизненного контекста."""
    import json as _j
    with _hdb.get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO context_events
              (date, ts, source, category, key, value_num, value_text, period_id, tags)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (date_str, ts, source, category, key, value_num, value_text,
              period_id, _j.dumps(tags or [])))
        return cur.lastrowid


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
