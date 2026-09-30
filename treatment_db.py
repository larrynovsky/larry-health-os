"""treatment_db.py — доменный модуль treatment. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import json
import os
import logging
from datetime import date, timedelta
from pathlib import Path


log = logging.getLogger(__name__)


def get_medications(confirmation: tuple = ("confirmed", "manual"),
                    include_proposed: bool = False) -> list[dict]:
    """Список режимов лечения. По умолчанию только канонические (гейт пройден)."""
    states = list(confirmation)
    if include_proposed and "proposed" not in states:
        states = states + ["proposed"]
    placeholders = ",".join("?" * len(states))
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM medications WHERE COALESCE(confirmation,'proposed') "
            f"IN ({placeholders}) ORDER BY COALESCE(start_date,'') ASC, id ASC",
            states,
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_medication(
    name: str,
    modality: str = None,
    intent: str = None,
    cycles_completed: int = None,
    cycles_planned: int = None,
    agents: list = None,
    indication_problem_id: str = None,
    prescribing_event_id: int = None,
    start_date: str = None,
    end_date: str = None,
    status: str = "completed",
    source: str = "extractor",
    confirmation: str = "proposed",
    notes: str = None,
) -> int | None:
    """Идемпотентный upsert режима лечения в medications.

    Ключ дедупликации: (нормализованный режим + indication_problem_id).
    Накопление: cycles_completed = MAX(старое, новое) — снимки одного режима
    НЕ складываются. Человек-гейт: confirmed/manual не перезаписывается extractor'ом.
    Возвращает id строки (новой или обновлённой)."""
    import json as _json
    canon = _hdb._normalize_regimen(name)
    if not canon:
        return None
    agents_json = _json.dumps(agents, ensure_ascii=False) if agents else None
    with _hdb.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM medications WHERE name=? AND "
            "COALESCE(indication_problem_id,'')=COALESCE(?,'')",
            (canon, indication_problem_id),
        ).fetchone()
        if row is None:
            cur = conn.execute(
                """INSERT INTO medications
                   (name, modality, intent, cycles_completed, cycles_planned,
                    agents, indication_problem_id, prescribing_event_id,
                    start_date, end_date, status, source, confirmation, notes, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
                (canon, modality, intent, cycles_completed, cycles_planned,
                 agents_json, indication_problem_id, prescribing_event_id,
                 start_date, end_date, status, source, confirmation, notes),
            )
            return cur.lastrowid
        # Существует. Человек-гейт: подтверждённое/ручное не трогаем extractor'ом.
        if (row["confirmation"] in ("confirmed", "manual")
                and _hdb._src_rank_med(source) < 2):
            return row["id"]
        # Накопление циклов: MAX, не сумма.
        new_cycles = row["cycles_completed"]
        if cycles_completed is not None:
            new_cycles = max(int(cycles_completed), int(row["cycles_completed"] or 0))
        _ends = [d for d in (end_date, row["end_date"]) if d]
        new_end = max(_ends) if _ends else row["end_date"]
        conn.execute(
            """UPDATE medications SET
                 modality=COALESCE(?,modality),
                 intent=COALESCE(?,intent),
                 cycles_completed=?,
                 cycles_planned=COALESCE(?,cycles_planned),
                 agents=COALESCE(?,agents),
                 prescribing_event_id=COALESCE(?,prescribing_event_id),
                 start_date=COALESCE(?,start_date),
                 end_date=?,
                 status=COALESCE(?,status),
                 source=?,
                 notes=COALESCE(?,notes),
                 updated_at=datetime('now')
               WHERE id=?""",
            (modality, intent, new_cycles, cycles_planned, agents_json,
             prescribing_event_id, start_date, new_end, status, source, notes,
             row["id"]),
        )
        return row["id"]


def get_episodes(status: str = None, problem_id: str = None) -> list:
    """Возвращает эпизоды, опционально фильтруя по статусу и/или проблеме."""
    conds, params = ["1=1"], []
    if status:
        conds.append("status = ?"); params.append(status)
    if problem_id:
        conds.append("primary_problem_id = ?"); params.append(problem_id)
    where = " AND ".join(conds)
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM episodes_of_care WHERE {where} ORDER BY start_date DESC",
            params
        ).fetchall()
    return [dict(r) for r in rows]


def set_medication_confirmation(med_id: int, confirmation: str) -> None:
    """Человек-гейт: подтвердить/отклонить извлечённый режим."""
    with _hdb.get_conn() as conn:
        conn.execute(
            "UPDATE medications SET confirmation=?, updated_at=datetime('now') WHERE id=?",
            (confirmation, med_id),
        )


def get_proposed_medications() -> list[dict]:
    """Режимы, ждущие подтверждения человеком (для гейт-карточек)."""
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM medications WHERE confirmation='proposed' ORDER BY id ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_unsent_proposed_medications() -> list[dict]:
    """Proposed-режимы, по которым ещё НЕ отправлена карточка гейта."""
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM medications WHERE confirmation='proposed' "
            "AND gate_sent_at IS NULL ORDER BY id ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def mark_medication_gate_sent(med_id: int) -> None:
    """Помечает, что карточка подтверждения режима отправлена (не слать повторно)."""
    with _hdb.get_conn() as conn:
        conn.execute(
            "UPDATE medications SET gate_sent_at=datetime('now') WHERE id=?",
            (med_id,),
        )


def save_episode(
    title: str,
    start_date: str,
    primary_problem_id: str = None,
    end_date: str = None,
    status: str = "active",
    managing_organization: str = None,
    notes: str = None,
) -> int:
    """Создаёт эпизод помощи. Возвращает episode_id."""
    with _hdb.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO episodes_of_care
               (primary_problem_id, title, start_date, end_date,
                status, managing_organization, notes)
               VALUES (?,?,?,?,?,?,?)""",
            (primary_problem_id, title, start_date, end_date,
             status, managing_organization, notes)
        )
        conn.commit()
    return cur.lastrowid


def complete_planned_event(event_id: int, interpreted_report: str = None,
                           abnormal_flags: list = None) -> None:
    """Переводит planned event → completed. Обновляет diagnostic_events если есть."""
    import json as _json
    with _hdb.get_conn() as conn:
        conn.execute(
            "UPDATE events SET status='completed' WHERE id=? AND status='planned'",
            (event_id,)
        )
        if interpreted_report is not None:
            conn.execute(
                "UPDATE diagnostic_events SET interpreted_report=?, abnormal_flags=? WHERE event_id=?",
                (interpreted_report,
                 _json.dumps(abnormal_flags or []),
                 event_id)
            )
        conn.commit()


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
