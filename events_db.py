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


# Кто может ССЫЛАТЬСЯ на событие из чужой таблицы. Такое событие delete_event не трогает:
# удалить его = молча оборвать ссылку у лекарства, анализа или правила мониторинга.
_DEPENDENTS = (
    ("лекарства",            "SELECT COUNT(*) FROM medications WHERE prescribing_event_id=?", None),
    ("анализы",              "SELECT COUNT(*) FROM lab_results WHERE event_id=?", None),
    ("назначенные события",  "SELECT COUNT(*) FROM diagnostic_events "
                             "WHERE ordering_event_id=? AND event_id<>?", "twice"),
    ("правила мониторинга",  "SELECT COUNT(*) FROM lab_monitoring_schedule WHERE source=?", "encounter"),
)


def _event_dependents(conn, event_id: int) -> dict:
    """{что: сколько} — внешние ссылки на событие. Пусто = событие можно удалить целиком."""
    import sqlite3
    out = {}
    for label, sql, shape in _DEPENDENTS:
        args = ((event_id, event_id) if shape == "twice"
                else (f"encounter:{event_id}",) if shape == "encounter" else (event_id,))
        try:
            n = conn.execute(sql, args).fetchone()[0]
        except sqlite3.OperationalError as e:
            if "no such" not in str(e):
                raise
            continue  # silent-ok: нет таблицы/колонки — ссылаться нечему
        if n:
            out[label] = n
    return out


def delete_event(event_id: int, reason: str, recorded_by: str = "dashboard") -> dict:
    """Удаляет ошибочно созданное событие целиком: строку events и её подтаблицы.

    Зачем (2026-10-18, фидбэк с GitHub): создать событие в медкарте было можно, убрать —
    нельзя; неверный приём оставался в медкарте и в контексте моделей навсегда.

    Почему удаление, а не status='entered-in-error': ни один читатель events (контекст GP,
    консилиум, экстракторы, медкарта) статус не фильтрует, и «мягко удалённый» приём
    продолжал бы ехать в промпт. Старая строка не теряется — она в data_repair_log.

    Fail-closed: на событие ссылаются лекарства/анализы/правила → ValueError с перечнем,
    ничего не удалено. Нет события → LookupError.
    """
    with _hdb.get_conn() as conn:
        row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if row is None:
            raise LookupError(f"события #{event_id} нет")
        deps = _event_dependents(conn, event_id)
        if deps:
            raise ValueError("на событие ссылаются: " +
                             ", ".join(f"{k} — {v}" for k, v in deps.items()))
        old = dict(row)
        # В журнал — и подтаблицы: у приёма смысл (причина, заключение) живёт в encounters.
        snapshot = {"event": old}
        for sub in ("encounters", "diagnostic_events"):
            r = conn.execute(f"SELECT * FROM {sub} WHERE event_id=?", (event_id,)).fetchone()
            if r is not None:
                snapshot[sub] = dict(r)
        conn.execute("BEGIN")
        try:
            _hdb.log_repair(conn, run_id=f"delete_event:{recorded_by}", home="sqlite:events",
                            entity=str(event_id), field="row",
                            old_value=json.dumps(snapshot, ensure_ascii=False), new_value=None,
                            reason=reason or "удалено пользователем как ошибочное")
            for sql in ("DELETE FROM encounters WHERE event_id=?",
                        "DELETE FROM diagnostic_events WHERE event_id=?",
                        "DELETE FROM event_problem_links WHERE event_id=?",
                        "DELETE FROM event_relationships WHERE parent_event_id=? OR child_event_id=?",
                        "DELETE FROM events WHERE id=?"):
                conn.execute(sql, (event_id,) * sql.count("?"))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    log.info(f"delete_event #{event_id} ({old.get('event_type')} {old.get('effective_date')}): {reason}")
    return old


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
