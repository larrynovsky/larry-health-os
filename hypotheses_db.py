"""hypotheses_db.py — доменный модуль hypotheses. Вынесен из health_db.py (Поток C, strangler-фасад)."""
# INTENT: hypothesis_experiment — гипотеза как эксперимент, а не реплика в чате.
#          Замысел и инварианты — subsystem_intent.yaml, раздел hypothesis_experiment.
from __future__ import annotations

import json
import os
import logging
from datetime import date, timedelta
from pathlib import Path


log = logging.getLogger(__name__)


def save_hypothesis_outcome(memory_id: int, verdict: str,
                            confidence: float = None,
                            evidence: str = None,
                            reasoning: str = None,
                            coordinator_text: str = None):
    """
    Сохраняет новый outcome по гипотезе (история оценок, не upsert).
    Каждый запуск консилиума добавляет строку — UNIQUE(memory_id) снят в миграции.
    coordinator_text — полный текст консилиума для гарантированной доставки.
    """
    _hdb._ensure_hypothesis_outcomes()
    with _hdb.get_conn() as conn:
        conn.execute("""
            INSERT INTO hypothesis_outcomes
                (memory_id, verdict, confidence, evidence_json, reasoning,
                 coordinator_text, evaluated_at)
            VALUES (?, ?, ?, ?, ?, ?, date('now'))
        """, (memory_id, verdict, confidence, evidence, reasoning, coordinator_text))


def get_hypothesis_outcome(memory_id: int) -> dict | None:
    """Возвращает последний outcome для гипотезы или None."""
    _hdb._ensure_hypothesis_outcomes()
    with _hdb.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM hypothesis_outcomes WHERE memory_id=? ORDER BY id DESC LIMIT 1",
            (memory_id,)
        ).fetchone()
    return dict(row) if row else None


def get_hypotheses_awaiting_evaluation() -> list[dict]:
    """Гипотезы в status='testing', чей ТЕКУЩИЙ круг не получил outcome.

    Решение владельца 2026-08-30 («за эту версию, не вообще»): outcome считается
    только если его evaluated_at не старше даты замка eval_started_at — иначе
    консилиум, убитый рестартом дашборда, висел бы в testing навсегда, потому что
    прошлогодний outcome «уже есть». Без замка (старый путь бота) — прежнее
    правило: любой outcome исключает.
    """
    _hdb._ensure_hypothesis_outcomes()
    import json as _j
    rows = _hdb.get_memory(category="hypothesis", n=100, active_only=True)
    with _hdb.get_conn() as conn:
        last_eval = {
            r[0]: r[1] for r in
            conn.execute("SELECT memory_id, MAX(evaluated_at) FROM hypothesis_outcomes "
                         "GROUP BY memory_id").fetchall()
        }
    result = []
    for row in rows:
        try:
            payload = _j.loads(row["value"])
        except Exception:
            continue
        if payload.get("status") != "testing":
            continue
        last = last_eval.get(row["id"])
        started = (payload.get("eval_started_at") or "")[:10]
        if last and (not started or str(last)[:10] >= started):
            continue
        payload["memory_id"] = row["id"]
        result.append(payload)
    return result


def get_hypothesis_accuracy_stats() -> dict:
    """Статистика точности по source (auto_consilium, manual, и т.д.)."""
    _hdb._ensure_hypothesis_outcomes()
    import json as _j
    with _hdb.get_conn() as conn:
        rows = conn.execute("""
            SELECT ho.verdict, m.source, COUNT(*) as cnt
            FROM hypothesis_outcomes ho
            JOIN memory m ON m.id = ho.memory_id
            GROUP BY ho.verdict, m.source
        """).fetchall()
    stats: dict = {}
    for row in rows:
        src = row["source"] or "manual"
        if src not in stats:
            stats[src] = {"confirmed": 0, "partial": 0, "rejected": 0, "total": 0}
        v = row["verdict"]
        stats[src][v] = stats[src].get(v, 0) + row["cnt"]
        stats[src]["total"] += row["cnt"]
    return stats


def get_unsent_hypothesis_outcomes() -> list[dict]:
    """
    Возвращает outcomes, которые не были доставлены в Telegram.
    Условие: sent_at IS NULL AND coordinator_text IS NOT NULL AND
    (delivering_since IS NULL OR delivering_since старше 10 минут).
    """
    _hdb._ensure_hypothesis_outcomes()
    with _hdb.get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM hypothesis_outcomes
            WHERE sent_at IS NULL
              AND coordinator_text IS NOT NULL
              AND (
                delivering_since IS NULL
                OR delivering_since < datetime('now', '-10 minutes')
              )
            ORDER BY evaluated_at ASC
        """).fetchall()
    return [dict(r) for r in rows]


def _outcome_target_sql(outcome_id: int | None) -> tuple[str, tuple]:
    """Какую строку outbox метить. outcome_id — точная строка (outbox-цикл);
    без него — последняя по memory_id (вызов сразу после save, как раньше).
    Урок 2026-08-29: при двух outcome одной гипотезы метка «по MAX(id)»
    оставляла старшую строку sent_at=NULL навсегда → outbox слал её каждые 5 мин."""
    if outcome_id is not None:
        return "id=?", (outcome_id,)
    return "id=(SELECT MAX(id) FROM hypothesis_outcomes WHERE memory_id=?)", ()


def mark_hypothesis_outcome_delivering(memory_id: int, chat_id: int,
                                       outcome_id: int | None = None) -> None:
    """Отмечает что доставка началась (защита от двойной отправки при краше)."""
    _hdb._ensure_hypothesis_outcomes()
    target, extra = _outcome_target_sql(outcome_id)
    with _hdb.get_conn() as conn:
        conn.execute(f"""
            UPDATE hypothesis_outcomes
            SET delivering_since=datetime('now'), telegram_chat_id=?
            WHERE memory_id=? AND sent_at IS NULL AND {target}
        """, (chat_id, memory_id) + (extra or (memory_id,)))


def mark_hypothesis_outcome_sent(memory_id: int, outcome_id: int | None = None) -> None:
    """Отмечает что результат доставлен в Telegram."""
    _hdb._ensure_hypothesis_outcomes()
    target, extra = _outcome_target_sql(outcome_id)
    with _hdb.get_conn() as conn:
        conn.execute(f"""
            UPDATE hypothesis_outcomes
            SET sent_at=datetime('now'), delivering_since=NULL
            WHERE memory_id=? AND {target}
        """, (memory_id,) + (extra or (memory_id,)))


def save_cbcr_payload(
    memory_id: int,
    payload_json: str,
    structural_score: int,
    confidence_level: str,
    generated_by: str,
    model: str,
) -> None:
    """Записывает или обновляет CBCR-payload, привязанный к memory.id.

    payload_json — уже сериализованный JSON-string (17KB типично).
    """
    with _hdb.get_conn() as conn:
        conn.execute(
            """INSERT INTO hypotheses_cbcr
               (memory_id, payload, structural_score, confidence_level, generated_by, model)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(memory_id) DO UPDATE SET
                   payload = excluded.payload,
                   structural_score = excluded.structural_score,
                   confidence_level = excluded.confidence_level,
                   generated_by = excluded.generated_by,
                   model = excluded.model,
                   generated_at = datetime('now')""",
            (memory_id, payload_json, structural_score, confidence_level,
             generated_by, model),
        )


def get_cbcr_payload(memory_id: int) -> dict | None:
    """Возвращает CBCR-payload (parsed JSON) для memory.id или None."""
    import json as _json
    with _hdb.get_conn() as conn:
        row = conn.execute(
            "SELECT payload, structural_score, confidence_level, generated_by, "
            "model, generated_at FROM hypotheses_cbcr WHERE memory_id=?",
            (memory_id,),
        ).fetchone()
    if not row:
        return None
    try:
        payload = _json.loads(row["payload"])
    except Exception:
        return None
    return {
        "payload": payload,
        "structural_score": row["structural_score"],
        "confidence_level": row["confidence_level"],
        "generated_by": row["generated_by"],
        "model": row["model"],
        "generated_at": row["generated_at"],
    }


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
