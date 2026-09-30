#!/usr/bin/env python3
"""
hypothesis_resolution — применяет вердикт консилиума к lifecycle гипотезы.

v2 (2026-05-25 M2): история версий, version-safe UPDATE, apply revised_hypothesis,
confirmed/partial → status=open (гипотеза остаётся активной для повторного цикла).

Единственная задача: получить verdict_dict из hypothesis_consilium_eval
и исполнить next steps.

Зависимости: health_db, hai_hypotheses (только save_hypothesis + generate/save protocol).
"""
# INTENT: doctor_in_loop — врач в контуре: ответ врача возвращается ВХОДОМ.
#          Замысел и инварианты — subsystem_intent.yaml, раздел doctor_in_loop.

import json as _json
import i18n
import notify
import logging
import sqlite3
from datetime import date
from _time_inject import get_today  # seam
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

import health_db as db
from hai_hypotheses import (
    save_hypothesis,
    generate_protocol_from_hypothesis,
    save_protocol,
)

log = logging.getLogger(__name__)


class ConcurrentModificationError(Exception):
    pass


# ── Version-safe DB helper ────────────────────────────────────────────────────

def _update_hypothesis_versioned(
    memory_id: int,
    new_payload: dict,
    expected_version: int,
    new_active: int = 1,
) -> bool:
    """
    UPDATE memory WHERE id=? AND COALESCE(version,0) = expected_version.
    Returns True если запись обновлена, False если версия не совпала (конфликт).
    """
    new_value = _json.dumps(new_payload, ensure_ascii=False)
    with db.get_conn() as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        cur = conn.execute(
            """UPDATE memory
               SET value=?, active=?, updated_at=datetime('now')
               WHERE id=?
                 AND COALESCE(
                       CAST(json_extract(value, '$.version') AS INTEGER), 0
                     ) = ?""",
            (new_value, new_active, memory_id, expected_version),
        )
        return cur.rowcount > 0


def _read_hypothesis_row(memory_id: int) -> dict | None:
    """Читает строку из memory через health_db.get_conn (A++ R1/R2 guarded)."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id, key, value, confidence, source, active FROM memory WHERE id=?",
            (memory_id,),
        ).fetchone()
        return dict(row) if row else None


# ── Task creation from hypothesis test field ──────────────────────────────────

def _create_task_if_needed(test_text: str, memory_id: int) -> int | None:
    """Создаёт задачу из поля test, если такой ещё нет (идемпотентно)."""
    if not test_text or not test_text.strip():
        return None
    text = test_text.strip()[:500]
    with db.get_conn() as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        # Проверяем дубль
        existing = conn.execute(
            "SELECT id FROM tasks WHERE source='hypothesis_confirm' "
            "AND content=? AND status='open'",
            (text,),
        ).fetchone()
        if existing:
            log.info(f"Задача уже существует для гипотезы #{memory_id}, пропускаем")
            return existing[0]
        cur = conn.execute(
            """INSERT INTO tasks
               (source, source_date, type, content, priority, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
            ("hypothesis_confirm", get_today().isoformat(),
             "followup", text, "medium", "open"),
        )
        task_id = cur.lastrowid
        log.info(f"Задача #{task_id} создана из гипотезы #{memory_id}")
        return task_id


# ── G3 fresh-evidence guard ───────────────────────────────────────────────────

def _has_out_of_sample_lab(payload: dict) -> bool:
    """True, если есть лаб, взятый ПОСЛЕ выдвижения гипотезы (created_date).

    confirmed→протокол разрешаем только при out-of-sample подтверждении: гипотеза
    должна лечь на данные, которых не было в момент её рождения, а не на in-sample
    подгонку под уже существовавшую лабную историю (её же eval и читает через
    get_lab_history). Молодая гипотеза (version 0, дни от роду) такого лаба ещё
    не имеет → протокол ждёт следующего цикла, когда придут свежие анализы.

    Инвариант: блокируем ТОЛЬКО при доказанном отсутствии out-of-sample данных
    (created_date есть И ни одного лаба после него). Любая неоднозначность —
    created_date нет (легаси до появления поля) либо чтение лабов упало —
    трактуется в пользу вердикта панели (fail-open), чтобы не отзывать
    подтверждение без прямого доказательства in-sample-подгонки. Якорь —
    created_date (первое рождение), не дата последней ревизии: репликация нужна
    на данных вне исходного окна наблюдения.
    """
    created = (payload.get("created_date") or "").strip()
    if not created:
        log.warning("fresh-evidence guard: created_date отсутствует — пропускаю "
                    "(fail-open; вероятно легаси-гипотеза до появления поля)")
        return True
    try:
        rows = db.get_lab_history(3650)  # широкое окно; фактический фильтр — по дате ниже
    except Exception as e:  # noqa: BLE001 — не роняем resolve из-за чтения лабов
        log.warning(f"fresh-evidence guard: чтение лабов упало ({e}) — пропускаю (fail-open)")
        return True
    return any((r.get("date") or "") > created for r in rows)


# ── Основной resolve ──────────────────────────────────────────────────────────

def resolve_hypothesis(memory_id: int, verdict_dict: dict, *,
                       person_key: str | None = "common.error.our_side") -> dict:
    """
    Применяет вердикт консилиума к lifecycle гипотезы.

    confirmed / partial:
      - сохраняет текущие поля в history[]
      - применяет revised_hypothesis (обновлённые поля)
      - ставит status=open, active=1 (гипотеза идёт на следующий цикл)
      - version + 1
      - создаёт задачу из revised_hypothesis.test

    rejected:
      - сохраняет в history, ставит active=0, status=rejected
      - создаёт отдельную гипотезу если new_hypothesis в verdict_dict

    error:
      - ничего не меняет в БД, возвращает сообщение об ошибке

    Returns:
        dict: verdict, task_id, protocol_id, new_hypothesis_id,
              experiment_closed, message
    """
    verdict    = verdict_dict.get("verdict", "partial")
    reasoning  = verdict_dict.get("reasoning", "")
    new_h_data = verdict_dict.get("new_hypothesis")
    revised    = verdict_dict.get("revised_hypothesis") or {}
    confidence = verdict_dict.get("confidence", 0.5)

    result: dict = {
        "memory_id":         memory_id,
        "verdict":           verdict,
        "task_id":           None,
        "protocol_id":       None,
        "new_hypothesis_id": None,
        "experiment_closed": False,
        "protocol_withheld": False,
        "message":           "",
    }

    # ── error: ничего не меняем ──────────────────────────────────────────────
    if verdict == "error":
        # Reasoning may contain clinical content; the stored outcome is identified by ID.
        result["message"] = notify.fault(
            f"hypothesis_resolution: evaluation failed memory_id={memory_id}", person_key=person_key)
        return result

    # ── Читаем текущий payload ───────────────────────────────────────────────
    row = _read_hypothesis_row(memory_id)
    if not row:
        result["message"] = notify.fault(f"hypothesis_resolution: missing row memory_id={memory_id}", person_key=person_key)
        return result

    try:
        payload = _json.loads(row["value"] or "{}")
    except (TypeError, ValueError):
        payload = {}

    current_version = int(payload.get("version") or 0)

    # ── Сохраняем текущую версию в историю ──────────────────────────────────
    history = payload.get("history") or []
    history.append({
        "version":     current_version,
        "date":        get_today().isoformat(),
        "verdict":     verdict,
        "confidence":  confidence,
        "observation": payload.get("observation") or "",
        "mechanism":   payload.get("mechanism") or "",
        "prediction":  payload.get("prediction") or "",
        "test":        payload.get("test") or "",
        "reasoning":   reasoning[:200] if reasoning else "",
    })
    payload["history"] = history

    # ── confirmed / partial ──────────────────────────────────────────────────
    if verdict in ("confirmed", "partial"):
        # Применяем revised_hypothesis
        if revised:
            payload["observation"] = revised.get("observation") or payload.get("observation", "")
            payload["mechanism"]   = revised.get("mechanism")   or payload.get("mechanism", "")
            payload["prediction"]  = revised.get("prediction")  or payload.get("prediction", "")
            payload["test"]        = revised.get("test")        or payload.get("test", "")

        payload["status"]           = "open"
        payload["version"]          = current_version + 1
        payload["last_verdict"]     = verdict
        payload["last_confidence"]  = confidence
        payload.pop("resolved_as", None)   # убираем resolved_as (consilium сбрасывает action-time маркер)

        ok = _update_hypothesis_versioned(
            memory_id, payload,
            expected_version=current_version,
            new_active=1,
        )
        if not ok:
            raise ConcurrentModificationError(
                f"hypothesis_resolution: concurrent modification memory_id={memory_id}"
            )

        # Задача из обновлённого test
        test_text = (payload.get("test") or "").strip()
        task_id = _create_task_if_needed(test_text, memory_id)
        result["task_id"] = task_id

        # Для confirmed — ещё протокол, НО только при out-of-sample подтверждении.
        # G3 fresh-evidence guard: не эскалируем в протокол, если гипотеза
        # подтверждена лишь на данных, что уже были в момент её рождения
        # (in-sample). Требуем лаб, взятый после created_date. Свежая гипотеза
        # такого лаба ещё не имеет → протокол ждёт следующего цикла.
        if verdict == "confirmed":
            if _has_out_of_sample_lab(payload):
                try:
                    protocol    = generate_protocol_from_hypothesis(payload)
                    protocol_id = save_protocol(protocol)
                    result["protocol_id"] = protocol_id
                    log.info(f"Гипотеза #{memory_id}: confirmed → протокол #{protocol_id}")
                except Exception as e:
                    log.warning(f"Протокол не создан: {e}")
            else:
                result["protocol_withheld"] = True
                log.info(
                    f"Гипотеза #{memory_id}: confirmed, протокол ОТЛОЖЕН — нет лаба "
                    f"после выдвижения ({payload.get('created_date')}); ждём "
                    "out-of-sample данные."
                )

        _close_experiment(memory_id)
        result["experiment_closed"] = True
        result["message"] = (
            i18n.t("cards.hypothesis.updated_confirmed" if verdict == "confirmed"
                   else "cards.hypothesis.updated_partial", memory_id=memory_id, confidence=confidence)
            + (i18n.t("cards.hypothesis.task_created", task_id=task_id) if task_id else "")
            + (i18n.t("cards.hypothesis.protocol_saved", protocol_id=result['protocol_id']) if result["protocol_id"] else "")
            + (i18n.t("hypotheses.reply.protocol_waits")
               if result.get("protocol_withheld") else "")
        )
        log.info(f"Гипотеза #{memory_id}: {verdict} → v{payload['version']}, "
                 f"status=open, task={task_id}")

    # ── rejected ─────────────────────────────────────────────────────────────
    elif verdict == "rejected":
        payload["status"]          = "rejected"
        payload["version"]         = current_version + 1
        payload["rejection_reason"] = reasoning
        payload["active"]          = 0

        ok = _update_hypothesis_versioned(
            memory_id, payload,
            expected_version=current_version,
            new_active=0,
        )
        if not ok:
            raise ConcurrentModificationError(
                f"hypothesis_resolution: concurrent modification memory_id={memory_id}"
            )

        _close_experiment(memory_id)
        result["experiment_closed"] = True

        if new_h_data and isinstance(new_h_data, dict):
            # E2-fix (2026-07-18): наследуем resolution_type родителя, НЕ понижая
            # медицинский маршрут. Отвергнутая needs_specialist-гипотеза,
            # перерождённая консилиумом, обязана сохранить «к врачу» — иначе молча
            # уходит в self_managed (дефолт save_hypothesis) и выпадает и из
            # consult_prep (материал к визиту), и из patient-notify. Единственный
            # путь создания гипотезы, где resolution_type не прокидывался.
            _parent_rt = payload.get("resolution_type") or "self_managed"
            _child_rt  = "needs_specialist" if _parent_rt == "needs_specialist" else _parent_rt
            try:
                new_id = save_hypothesis(
                    observation = new_h_data.get("observation", ""),
                    mechanism   = new_h_data.get("mechanism", ""),
                    prediction  = new_h_data.get("prediction", ""),
                    test        = new_h_data.get("test", ""),
                    trigger     = "consilium_revision",
                    status      = "open",
                    resolution_type = _child_rt,
                )
                result["new_hypothesis_id"] = new_id
                log.info(f"Гипотеза #{memory_id}: rejected → новая #{new_id}")
            except Exception as e:
                log.warning(f"Новая гипотеза не сохранена: {e}")

        result["message"] = (
            i18n.t("cards.hypothesis.disproved", memory_id=memory_id)
            + (i18n.t("cards.hypothesis.revised", memory_id=result['new_hypothesis_id'])
               if result["new_hypothesis_id"] else "")
        )

    else:
        log.warning(f"Неизвестный вердикт '{verdict}' для гипотезы #{memory_id}")
        result["message"] = notify.fault(f"hypothesis_resolution: unknown verdict memory_id={memory_id}", person_key=person_key)

    return result


def _close_experiment(memory_id: int):
    """No-op (BL-EXP-1, 2026-07-10): конвейер экспериментов ретайрится.

    Раньше закрывал linked_experiment по вердикту консилиума. Гипотезы больше
    не привязываются к экспериментам; сходимость идёт через версионирование
    самой гипотезы (version++/history в resolve_hypothesis). Вызовы оставлены
    как no-op, чтобы не трогать поток resolve; удалятся на стадии data-слоя.
    Раскцепляет resolve_hypothesis от experiments_db.complete_experiment.
    """
    return None


# ── Полный цикл вне процесса дашборда (2026-08-30) ───────────────────────────

def set_eval_error(memory_id: int, message: str) -> None:
    """Ошибка консилиума видна в карточке: eval_error в payload, замок
    eval_started_at снят. Содержание гипотезы (поля, version, history) не
    трогаем — инвариант error_no_mutation (doctor_in_loop)."""
    row = _read_hypothesis_row(memory_id)
    if not row:
        return
    try:
        payload = _json.loads(row["value"] or "{}")
    except (TypeError, ValueError):
        payload = {}
    payload["eval_error"] = str(message)[:500]
    payload.pop("eval_started_at", None)
    with db.get_conn() as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            "UPDATE memory SET value=?, updated_at=datetime('now') WHERE id=?",
            (_json.dumps(payload, ensure_ascii=False), memory_id),
        )


def run_consilium_and_resolve(memory_id: int) -> dict:
    """Консилиум + применение вердикта — ОДНИМ вызовом, для отдельного процесса.

    Почему отдельный процесс, а не задача в event loop дашборда (урок 30.08):
    post-commit хук перезапускает дашборд на КАЖДОМ коммите (5 за день), и
    фоновые задачи гибли на Раунде B — ~60 LLM-вызовов в корзину, карточка
    «идёт» до TTL. Здесь результат пишется в БД тем же resolve_hypothesis;
    карточка и outbox бота читают БД и о процессе не знают.
    """
    import asyncio
    from hypothesis_consilium_eval import evaluate_hypothesis_via_consilium
    try:
        verdict_dict = asyncio.run(evaluate_hypothesis_via_consilium(memory_id))
        if verdict_dict.get("verdict") == "error":
            msg = verdict_dict.get("reasoning") or i18n.t("hypothesis_resolution.error.consilium")
            set_eval_error(memory_id, msg)
            return {"memory_id": memory_id, "verdict": "error", "message": msg}
        return resolve_hypothesis(memory_id, verdict_dict)
    except Exception as exc:
        log.exception(f"Consilium #{memory_id} FAILED")
        set_eval_error(memory_id, i18n.t("hypothesis_resolution.error.detail", error=exc))
        return {"memory_id": memory_id, "verdict": "error", "message": str(exc)}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Консилиум по гипотезе + применение вердикта")
    ap.add_argument("--eval", metavar="MEMORY_ID", type=int, required=True)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _res = run_consilium_and_resolve(args.eval)
    print(_json.dumps({k: _res.get(k) for k in ("memory_id", "verdict", "task_id", "message")},
                      ensure_ascii=False))
    sys.exit(0 if _res.get("verdict") != "error" else 1)
