"""problems_db.py — доменный модуль problems. Вынесен из health_db.py (Поток C, strangler-фасад)."""
# INTENT: problem_list_proposals — правка медкарты как единица решения владельца.
#          Замысел и инварианты — subsystem_intent.yaml, раздел problem_list_proposals.
from __future__ import annotations
import llm_client

import json
import os
import logging
from datetime import date, timedelta
from pathlib import Path

from _time_inject import get_today

log = logging.getLogger(__name__)


def get_problem_list(status: str = None) -> list[dict]:
    """Возвращает problem list, опционально фильтруя по статусу."""
    with _hdb.get_conn() as conn:
        if status:
            rows = conn.execute("""
                SELECT * FROM problem_list WHERE status=?
                ORDER BY priority, last_updated DESC
            """, (status,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM problem_list
                ORDER BY
                    CASE status WHEN 'active' THEN 1 WHEN 'monitoring' THEN 2 ELSE 3 END,
                    priority, last_updated DESC
            """).fetchall()
    return [dict(r) for r in rows]


def upsert_problem(problem_id: str, title: str, description: str = None,
                   status: str = 'active', priority: int = 2, domain: str = None,
                   watch_trigger: str = None, watch_deadline: str = None,
                   notes: str = None, review_date: str = None,
                   review_in_days: int = None) -> int:
    """Создаёт или обновляет проблему в problem list.

    review_date: YYYY-MM-DD — абсолютная дата пересмотра.
    review_in_days: если задан вместо review_date — вычисляет дату от сегодня.
    """
    from datetime import timedelta as _td
    now = str(get_today())
    if review_in_days is not None and review_date is None:
        review_date = str(get_today() + _td(days=review_in_days))
    with _hdb.get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM problem_list WHERE problem_id=?", (problem_id,)
        ).fetchone()
        if existing:
            conn.execute("""
                UPDATE problem_list SET
                    title=?, description=?, status=?, priority=?, domain=?,
                    watch_trigger=?, watch_deadline=?, notes=?, review_date=?,
                    last_updated=?
                WHERE problem_id=?
            """, (title, description, status, priority, domain,
                  watch_trigger, watch_deadline, notes, review_date,
                  now, problem_id))
            return existing["id"]
        else:
            cur = conn.execute("""
                INSERT INTO problem_list
                  (problem_id, title, description, status, priority, domain,
                   first_seen, last_updated, watch_trigger, watch_deadline,
                   notes, review_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (problem_id, title, description, status, priority, domain,
                  now, now, watch_trigger, watch_deadline, notes, review_date))
            return cur.lastrowid


def _add_text(ch: dict) -> str:
    """Текст предложения «добавить проблему»: title+description с любого уровня вложенности
    (GP кладёт поля либо в ch, либо в ch["new_value"])."""
    src = dict(ch)
    nv = ch.get("new_value")
    if isinstance(nv, dict):
        src.update(nv)
    return " ".join(str(src.get(k) or "") for k in ("title", "description", "reason")).strip()


def _norm_title(text: str) -> str:
    import re as _re
    return _re.sub(r"[^a-zа-яё0-9]+", " ", (text or "").lower()).strip()[:80]


def proposal_dedup_key(ch: dict, source: str, conn=None) -> str | None:
    """Идентичность правки (2026-09-01, решение владельца: «одна правка — одна строка, повтор — счётчик»).

    update_*/resolve → (source, problem_id, action, field): та же правка того же поля.
    add → УСЛОВИЕ из clinical_kb/_index.yaml, к которому подходит текст предложения
    (тот же problem_regex, что потом активирует строки таблицы питания).
    Разные формулировки одного standing-условия дают ОДНО предложение;
    совпадение не подтверждает клиническую правоту модели. Условия нет →
    нормализованный title. Без problem_id и без текста (literature-advisory) → None (не дедупим).
    """
    if not isinstance(ch, dict):
        return None
    action = ch.get("action") or ""
    if action == "add":
        text = _add_text(ch)
        if not text:
            return None
        conds = _standing_conditions_for(text, conn)
        if conds:
            return f"add|cond:{'+'.join(conds)}"
        return f"add|title:{_norm_title(text)}" if _norm_title(text) else None
    pid = ch.get("problem_id")
    if pid is None:
        return None
    return f"{source}|{pid}|{action}|{ch.get('field') or ''}"


def _standing_conditions_for(text: str, conn=None) -> list[str]:
    """Условия clinical_kb, подходящие тексту, — ТОЛЬКО standing (состояние: дефицит железа,
    дисгликемия — одно на пациента). durable (диагноз, резекция, онкология) — каждое событие
    своё: «рецидив в другом органе» не дубль первичной опухоли, хотя оба матчат oncology."""
    try:
        import clinical_kb
        c = conn if conn is not None else _hdb.get_conn()
        conds = clinical_kb.active_conditions(c, med_text=text)
        if not conds:
            return []
        rows = c.execute("SELECT id, temporal_class FROM clinical_kb_conditions").fetchall()
        tclass = {(r[0] if not hasattr(r, "keys") else r["id"]):
                  (r[1] if not hasattr(r, "keys") else r["temporal_class"]) for r in rows}
        return sorted(cid for cid in conds if tclass.get(cid) == "standing")
    except Exception:  # noqa: BLE001 — реестр не засеян → ключ по заголовку
        return []


def existing_problem_for(ch: dict, conn=None) -> dict | None:
    """Проблема медкарты, которую предложение «добавить» ДУБЛИРУЕТ: живая (не resolved) строка
    problem_list с тем же условием clinical_kb, что и текст предложения, либо с тем же
    нормализованным заголовком. Решение владельца 2026-09-01: дубли записей не допускаются."""
    if not isinstance(ch, dict) or ch.get("action") != "add":
        return None
    text = _add_text(ch)
    if not text:
        return None
    key = proposal_dedup_key(ch, "", conn)
    c = conn if conn is not None else _hdb.get_conn()
    rows = c.execute("SELECT problem_id, title, description, notes, status FROM problem_list "
                     "WHERE status IS NULL OR status NOT IN ('resolved')").fetchall()
    for r in rows:
        r = dict(r)
        ptext = " ".join(str(r.get(k) or "") for k in ("title", "description"))
        pkey = proposal_dedup_key({"action": "add", "title": ptext}, "", conn)
        if key and pkey and key == pkey:
            return r
    return None


_PLAIN_SYSTEM = (
    "Перескажи медицинскую проблему для самого человека одной-двумя короткими фразами простым "
    "русским языком: без латыни, аббревиатур и терминов, без чисел анализов и дат. Не добавляй "
    "ничего, чего нет в тексте, и не давай советов. Ответь только этим текстом.")


def _plain_llm(text: str) -> str:
    """Один короткий вызов модели через единственный выход во внешний LLM (llm_client)."""
    import hai_core
    model = hai_core.get_model("haiku")
    resp = llm_client.guarded_client().messages.create(task="problems_db._plain_llm",
        model=model, max_tokens=200, system=_PLAIN_SYSTEM + __import__("hai_core").answer_language(),
        messages=[{"role": "user", "content": text[:1500]}])
    try:
        import api_spend_log
        api_spend_log.log_call(agent="problem_plain", model=resp.model or model,
                               tokens_in=resp.usage.input_tokens, tokens_out=resp.usage.output_tokens)
    except Exception as e:  # noqa: BLE001 — журнал трат не должен ронять предложение
        log.warning("problem_plain: журнал трат не записан: %s", e)
    return llm_client.answer_text(resp).strip()


def _plain_for_add(ch: dict, source: str, prev_payload: str | None = None) -> str | None:
    """Простое описание новой проблемы — в самой карточке, ДО решения владельца (27.09).

    Раньше новые проблемы приходили без plain_summary, и датчик check_problem_plain_summary
    видел пропуск уже в медкарте. Порядок: готовое поле → прошлый текст той же правки (повтор
    не зовёт модель заново) → слова человека (онбординг — он сам так сказал) → модель.
    Неудача модели → None: карточка уходит без него, пропуск увидит датчик."""
    src = dict(ch)
    if isinstance(ch.get("new_value"), dict):
        src.update(ch["new_value"])
    if (src.get("plain_summary") or "").strip():
        return src["plain_summary"].strip()
    if prev_payload:
        try:
            prev = json.loads(prev_payload)[0].get("plain_summary")
            if prev:
                return prev
        except Exception as e:  # noqa: BLE001 — битый прошлый payload → считаем заново
            log.warning("problem_plain: прошлый текст правки не прочитан: %s", e)
    if source == "onboarding":
        return (src.get("title") or "").strip() or None
    text = " — ".join(str(src.get(k)) for k in ("title", "description") if src.get(k))
    if not text:
        return None
    try:
        return _plain_llm(text) or None
    except Exception as e:  # noqa: BLE001 — нет модели/сети → без простого текста, датчик увидит
        log.warning("problem_plain: простой текст не получен (%s): %s", type(e).__name__, e)
        return None


def save_problem_proposal(source: str, changes: list, conn=None) -> int:
    """Сохраняет предложения по изменению problem list — ОДНА ПРАВКА = ОДНА СТРОКА.

    До 2026-09-01 пачка из 6–8 правок жила одной строкой и одобрялась/отклонялась целиком, а
    новая недельная пачка того же источника гасила прошлую (supersede) по пересечению problem_id —
    «добавить <состояние>» GP предлагал неделями подряд и ни разу не дожил до владельца.
    Теперь: та же правка (proposal_dedup_key) от того же источника в pending → НЕ новая строка и
    НЕ superseded, а repeats+1, last_proposed_at=now, текст обновляется свежим. «Добавить», уже
    покрытое живой проблемой медкарты, → не сохраняется (дубль записи), причина в логе.
    Возвращает id первой сохранённой строки (0, если нечего сохранять).
    """
    import json as _j
    first_id = 0
    c = conn if conn is not None else _hdb.get_conn()
    for ch in changes or []:
        if not isinstance(ch, dict):
            continue
        dup = existing_problem_for(ch, c)
        if dup is not None:
            log.info("proposal add пропущено — дубль живой проблемы %s (%s)",
                     dup.get("problem_id"), (dup.get("title") or "")[:50])
            continue
        key = proposal_dedup_key(ch, source, c)
        row = None
        if key:
            row = c.execute(
                "SELECT id, repeats, proposed FROM problem_list_proposals WHERE status='pending' "
                "AND source=? AND dedup_key=? ORDER BY id LIMIT 1", (source, key)).fetchone()
        if ch.get("action") == "add":
            ch = dict(ch)
            ch["plain_summary"] = _plain_for_add(ch, source, row[2] if row else None)
        payload = _j.dumps([ch], ensure_ascii=False)
        if row:
            c.execute("UPDATE problem_list_proposals SET proposed=?, repeats=COALESCE(repeats,1)+1, "
                      "last_proposed_at=datetime('now') WHERE id=?", (payload, row[0]))
            rid = row[0]
        else:
            cur = c.execute(
                "INSERT INTO problem_list_proposals (source, proposed, dedup_key, repeats, "
                "last_proposed_at) VALUES (?,?,?,1,datetime('now'))", (source, payload, key))
            rid = cur.lastrowid
        first_id = first_id or rid
    try:
        c.commit()
    except Exception:  # silent-ok: conn уже в транзакции вызывающего
        pass
    return first_id


# ── Решения врача по наблюдению (нить treatment-facts, 2026-08-30) ─────────────────────
# Схема — health_db (surveillance_decisions). Здесь писатель и читатель.

def record_surveillance_decision(topic: str, title: str, decision: str, decided_by: str,
                                 source: str, decided_on: str = None, valid_until: str = None,
                                 rationale: str = None) -> int:
    """Записать решение врача/владельца по теме наблюдения. Прежнее активное решение по той же
    теме уходит в retired (история сохраняется, versioned_not_destructive)."""
    now = str(get_today())
    with _hdb.get_conn() as conn:
        conn.execute("UPDATE surveillance_decisions SET retired_at=? WHERE topic=? AND retired_at IS NULL",
                     (now, topic))
        cur = conn.execute(
            "INSERT INTO surveillance_decisions (topic, title, decision, decided_by, decided_on, "
            "valid_until, rationale, source) VALUES (?,?,?,?,?,?,?,?)",
            (topic, title, decision, decided_by, decided_on or now, valid_until, rationale, source))
        return cur.lastrowid


def active_surveillance_decisions(on_date: str = None) -> list[dict]:
    """Действующие решения: не retired и (valid_until IS NULL или ≥ on_date)."""
    d = on_date or str(get_today())
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM surveillance_decisions WHERE retired_at IS NULL "
            "AND (valid_until IS NULL OR valid_until >= ?) ORDER BY decided_on DESC", (d,)).fetchall()
    return [dict(r) for r in rows]


def format_surveillance_decisions(decs: list[dict]) -> list[str]:
    """Строки для промпта (GP, куратор): одна на решение, с автором, сроком и провенансом."""
    out = []
    for dc in decs:
        until = f" до {dc['valid_until']}" if dc.get("valid_until") else " (бессрочно)"
        why = f" — {dc['rationale']}" if dc.get("rationale") else ""
        out.append(f"  • {dc['title']}: {dc['decision']}{until}; решил {dc['decided_by']} "
                   f"{dc['decided_on']} [{dc['source']}]{why}")
    return out


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
