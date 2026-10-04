#!/usr/bin/env python3.11
"""
memory_consolidation — SHADOW-движок консолидации memory_facts (R3, 2026-07-04).

⚠️ SHADOW-ONLY на этом этапе: propose() ПРЕДЛАГАЕТ действия и пишет их в
memory_consolidation_proposals со status='pending'. НИЧЕГО НЕ ПРИМЕНЯЕТ к
memory_facts. Применение — отдельный человек-гейт (R3-d), после ревью качества.

Два яруса (см. дизайн R3):
  1. DEFER-to-authority: генетика→геномный пайплайн, диагноз/онко→patient_profile,
     анализы→lab_results. Медицинские/генетические противоречия НЕ примиряются
     LLM'ом (иначе риск выбрать не тот генотип) — помечаются DEFER + medical_flag.
  2. LLM-консолидация только для чат-родных lifestyle-фактов (устройства, мелатонин,
     среда сна, кофе): MERGE разросшихся ключей, выбор текущего значения.

Действия: KEEP | MERGE | DEFER | STALE | SUPERSEDE.
"""
from __future__ import annotations
import llm_client

import json as _json
import logging

import health_db as _hdb
import hai_core
import i18n

log = logging.getLogger(__name__)

_MODEL_ROLE = "sonnet"  # задача-суждение, не haiku


def _ensure_proposals_table():
    with _hdb.get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS memory_consolidation_proposals (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            fact_id          INTEGER,
            current_key      TEXT,
            current_value    TEXT,
            action           TEXT NOT NULL,
            canonical_key    TEXT,
            proposed_value   TEXT,
            authority_source TEXT,
            medical_flag     INTEGER DEFAULT 0,
            rationale        TEXT,
            status           TEXT DEFAULT 'pending',
            created_at       TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_mcp_status ON memory_consolidation_proposals(status);
        """)
        # additive: source_keys — список исходных ключей для entity-level MERGE (JSON)
        cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(memory_consolidation_proposals)").fetchall()]
        if "source_keys" not in cols:
            conn.execute("ALTER TABLE memory_consolidation_proposals ADD COLUMN source_keys TEXT")
        # additive (28.09.2026): квитанция отправки карточки человек-гейта. Без неё
        # неотвеченная карточка уходила заново каждую ночь (см. run_nightly).
        if "sent_at" not in cols:
            conn.execute("ALTER TABLE memory_consolidation_proposals ADD COLUMN sent_at TEXT")


SYSTEM = (
    "Ты консолидируешь персональную медицинскую память, извлечённую из чата. "
    "Она разрослась: десятки ключей об одном и том же, с противоречиями. Твоя задача — "
    "предложить действие для каждого факта. Ты НЕ применяешь ничего, только предлагаешь. "
    "Безопасность важнее полноты: при сомнении — KEEP или DEFER, не SUPERSEDE."
)

PROMPT = """Факты (mem_class='fact', key | value):
{facts}

Для КАЖДОГО факта предложи ОДНО действие. Верни JSON-массив объектов:
{{"current_key": "...", "action": "KEEP|MERGE|DEFER|STALE|SUPERSEDE",
  "canonical_key": "<для MERGE — единый ключ>", "proposed_value": "<для MERGE — текущее значение>",
  "authority_source": "<для DEFER: genome|patient_profile|lab_results>",
  "medical_flag": true/false, "rationale": "кратко почему"}}

ПРАВИЛА (жёсткие):
1. DEFER (не примиряй сам!) — если факт дублирует авторитетный источник:
   - генетика/генотип/варианты (APOE, CYP2C19, FTO, genotype_*, genetic_*) → authority_source=genome
   - диагноз, химиотерапия, онкостатус, стадия → authority_source=patient_profile
   - значения анализов крови (b12, ferritin, CEA, CA19-9, гомоцистеин) → authority_source=lab_results
   Всегда medical_flag=true для DEFER. НЕ выбирай, какой генотип верный — это решает геном.
2. MERGE — разросшиеся ключи про одну lifestyle-сущность (все device_*/<имя прибора>_* про один прибор;
   melatonin_* про мелатонин; sleep_environment_* про среду сна). Дай один canonical_key и
   ВЫБЕРИ текущее значение (при противоречии доз/настроек — самое свежее/«maintained»),
   противоречие опиши в rationale. medical_flag=true если касается лечения/дозы.
3. STALE — явно устаревшее (прошедшие поездки/даты: travel_* за прошлые месяцы, next_*_date в прошлом).
4. KEEP — актуальный чат-родной lifestyle-факт без дубля и без разрастания.
5. SUPERSEDE — только если факт прямо противоречит ДРУГОМУ чат-факту и явно устарел; при
   медицинском содержании обязательно medical_flag=true (пойдёт на человек-гейт, не авто).

Только JSON-массив, без пояснений вокруг."""


def consolidation_card_text(prop: dict) -> str:
    """Текст карточки человек-гейта — с точки зрения пользователя, не БД. БЕЗ жаргона
    (valid_to/Ключ/Значение/консолидация): называет действие, показывает обратимость.
    Чистая функция → копия под регресс-тестом (жаргон не вернётся тихо). Живёт в домене
    консолидации (не в jobs/scheduled с telegram+секретами) → импортируется в тестах."""
    med = "⚕️ " if prop.get("medical_flag") else ""
    topic = i18n.t("cards.memory.topic", topic=prop.get('current_key')) if prop.get("current_key") else ""
    return i18n.t("cards.memory.review", med=med, value=(prop.get('current_value') or '')[:120],
                  topic=topic, reason=(prop.get('rationale') or '')[:220])


def _facts_for_consolidation() -> list[dict]:
    """Активные fact-строки для LLM-консолидации без authoritative-топиков:
    ими управляет назначенный источник, а не LLM по свежести текста.
    Исключаемые ключи берутся из beliefs.authoritative_keys() (анти-splitbrain),
    чтобы консолидация не предлагала заменить данные авторитетного источника."""
    import memory_facts_db as _mf
    import beliefs
    auth = beliefs.authoritative_keys()
    facts = sorted(_mf.get_facts("fact"), key=lambda f: (f.get("key") or ""))
    return [f for f in facts if (f.get("key") or "") not in auth]


def propose(persist: bool = True, batch_size: int = 40) -> list[dict]:
    """SHADOW: LLM предлагает действия для активных fact-строк. Пишет в proposals
    (status='pending'), НЕ трогает memory_facts. Возвращает список предложений.
    Батчами (иначе ответ на 112 фактов обрезается по max_tokens); сортировка по key
    группирует разросшиеся кластеры (<прибор>_*/genetic_*) в один батч для MERGE."""
    facts = _facts_for_consolidation()
    if not facts:
        return []
    _ensure_proposals_table()
    by_key = {f["key"]: f for f in facts}
    client = hai_core.get_client()

    proposals: list[dict] = []
    for i in range(0, len(facts), batch_size):
        chunk = facts[i:i + batch_size]
        facts_txt = "\n".join(f"{f['key']} | {f['value']}" for f in chunk)
        resp = client.messages.create(task="memory_consolidation.propose",
            model=hai_core.get_model(_MODEL_ROLE),
            max_tokens=8192,
            system=SYSTEM + hai_core.answer_language(),
            messages=[{"role": "user", "content": PROMPT.format(facts=facts_txt)}],
        )
        raw = llm_client.answer_text(resp).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            raw = raw[4:] if raw.startswith("json") else raw
        try:
            proposals.extend(_json.loads(raw))
        except Exception as e:
            log.warning(f"Консолидация batch@{i}: не распарсил JSON: {e}", exc_info=True)
            continue

    if persist:
        with _hdb.get_conn() as conn:
            asked = {r[0] for r in conn.execute(
                "SELECT fact_id FROM memory_consolidation_proposals WHERE action='SUPERSEDE' "
                "AND fact_id IS NOT NULL AND (status='rejected' OR "
                "(status='pending' AND sent_at IS NOT NULL))")}
            for p in proposals:
                ck = p.get("current_key")
                src = by_key.get(ck) or {}
                if _scalar(p.get("action")) == "SUPERSEDE" and src.get("id") in asked:
                    continue   # человек уже спрошен или ответил «оставить» — не переспрашиваем
                conn.execute(
                    """INSERT INTO memory_consolidation_proposals
                       (fact_id, current_key, current_value, action, canonical_key,
                        proposed_value, authority_source, medical_flag, rationale)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (src.get("id"), ck, src.get("value"), _scalar(p.get("action")),
                     _scalar(p.get("canonical_key")), _scalar(p.get("proposed_value")),
                     _scalar(p.get("authority_source")), 1 if p.get("medical_flag") else 0,
                     _scalar(p.get("rationale"))),
                )
    return proposals


def _scalar(v):
    """Граница доверия «ответ LLM»: MERGE-промпт сам просит СТРУКТУРИРОВАННЫЕ значения
    (JSON-фасеты), и модель законно возвращает dict в proposed_value — а sqlite ждёт
    скаляр. До 2026-08-13 один такой ответ ронял ВЕСЬ ночной джоб (две ночи подряд:
    «Error binding parameter 6»), и все предложения — включая SUPERSEDE для человека —
    не доезжали. Не-скаляр сериализуется в JSON-строку: фасетные значения в state и так
    живут JSON-текстом, читатели (карточка Telegram, apply) получают то же представление."""
    if isinstance(v, (dict, list)):
        return _json.dumps(v, ensure_ascii=False)
    return v


MERGE_SYSTEM = (
    "Ты сводишь разросшиеся lifestyle-факты в МИНИМУМ канонических сущностей со "
    "СТРУКТУРИРОВАННЫМ значением (JSON-фасеты). Железные правила: (1) не терять "
    "информацию — каждая деталь исходников попадает в фасет; (2) противоречия "
    "разрешать ЯВНО — текущее значение + краткая история в отдельном фасете."
)

MERGE_PROMPT = """Исходные lifestyle-факты (key | value):
{facts}

Сведи их в МИНИМУМ канонических сущностей (например: ОДИН прибор, ОДИН melatonin,
одна sleep_environment, одна sleep_aids). Для КАЖДОЙ сущности верни объект:
{{"canonical_key": "sauna",
  "value": {{"current_duration_min": 20, "current_temp_c": 80, "timing": "...",
             "start_date": "...", "history": "10→15→20 мин", "status": "active"}},
  "current_note": "если были противоречия доз/настроек — какое текущее и почему",
  "medical_flag": true/false,
  "source_keys": ["sauna_settings", "current_sauna_duration", ...]}}

ТРЕБОВАНИЯ:
- value — JSON-ОБЪЕКТ с именованными фасетами, НЕ одна строка. Сохрани ВСЮ информацию
  (при 30-vs-15 — оба, current= самое свежее/«maintained», прошлое в history).
- Одна сущность на прибор, не четыре. Точно так же melatonin, sleep_*.
- source_keys — ВСЕ исходные ключи, вошедшие в сущность.
Только JSON-массив, без пояснений."""


def propose_merges(persist: bool = True) -> list[dict]:
    """Переделывает MERGE как entity-level: одна сущность на канонический ключ со
    структурированным JSON-значением (сохраняет все фасеты, резолвит противоречия).
    Заменяет прежние фрагментированные MERGE-предложения. Применяет НОЛЬ."""
    import memory_facts_db as _mf
    _ensure_proposals_table()
    with _hdb.get_conn() as conn:
        keys = [r[0] for r in conn.execute(
            "SELECT DISTINCT current_key FROM memory_consolidation_proposals "
            "WHERE action='MERGE' AND status='pending'").fetchall()]
    facts = {f["key"]: f["value"] for f in _mf.get_facts("fact")}
    src = [(k, facts[k]) for k in keys if k in facts]
    if not src:
        return []
    facts_txt = "\n".join(f"{k} | {v}" for k, v in src)

    client = hai_core.get_client()
    resp = client.messages.create(task="memory_consolidation.propose_merges",
        model=hai_core.get_model(_MODEL_ROLE),
        max_tokens=4096,
        system=MERGE_SYSTEM,
        messages=[{"role": "user", "content": MERGE_PROMPT.format(facts=facts_txt)}],
    )
    raw = llm_client.answer_text(resp).strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        raw = raw[4:] if raw.startswith("json") else raw
    try:
        entities = _json.loads(raw)
    except Exception as e:
        log.warning(f"Merge: не распарсил JSON: {e}", exc_info=True)
        return []

    if persist:
        with _hdb.get_conn() as conn:
            conn.execute("DELETE FROM memory_consolidation_proposals "
                         "WHERE action='MERGE' AND status='pending'")
            for e in entities:
                conn.execute(
                    """INSERT INTO memory_consolidation_proposals
                       (current_key, action, canonical_key, proposed_value,
                        source_keys, medical_flag, rationale)
                       VALUES (?, 'MERGE', ?, ?, ?, ?, ?)""",
                    (e.get("canonical_key"), e.get("canonical_key"),
                     _json.dumps(e.get("value"), ensure_ascii=False),
                     _json.dumps(e.get("source_keys", []), ensure_ascii=False),
                     1 if e.get("medical_flag") else 0, e.get("current_note")),
                )
    return entities


def _authority_contains(conn, authority_source: str | None, text: str | None) -> bool:
    """D-guard (finding 4): True ТОЛЬКО если авторитет ДЕМОНСТРАТИВНО содержит факт.
    При любом сомнении False → DEFER не применяется, факт остаётся видимым (потеря
    нового медфакта дороже, чем дубль). Проверяемо для genome (ген-токен в
    genetic_variants) и lab (имя аналита в lab_results); patient_profile — свободный
    текст диагноза, containment не доказать → False (держим видимым, консервативно).
    `text` = key + value факта: идентичность гена/аналита часто в КЛЮЧЕ (genotype_MTHFR),
    а не в value (p.Trp500Ter) — матчим по обоим."""
    if not text:
        return False
    src = (authority_source or "").lower()
    low = text.lower()
    up = text.upper()
    try:
        if "genom" in src or "genetic" in src:
            genes = conn.execute(
                "SELECT DISTINCT UPPER(gene) FROM genetic_variants WHERE gene IS NOT NULL"
            ).fetchall()
            return any(g[0] and g[0] in up for g in genes)
        if "lab" in src:
            names = conn.execute(
                "SELECT DISTINCT LOWER(test_name) FROM lab_results WHERE test_name IS NOT NULL"
            ).fetchall()
            return any(n[0] and n[0] in low for n in names)
    except Exception:
        return False
    # patient_profile / неизвестный источник — не доказать → консервативно keep
    return False


def apply_safe(dry_run: bool = True) -> dict:
    """Применяет ТОЛЬКО DEFER+STALE (низкий риск, обратимо): помечает исходные
    memory_facts active=0 + valid_to (не delete → откат тривиален). MERGE и
    SUPERSEDE НЕ трогает — они на человек-гейт (R3-d, вариант 2). Аудит: статус
    proposal → 'applied'. dry_run=True — только считает, ничего не пишет."""
    _ensure_proposals_table()
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT p.id, p.fact_id, p.action, p.authority_source, "
            "mf.key AS mkey, mf.value AS mval "
            "FROM memory_consolidation_proposals p "
            "LEFT JOIN memory_facts mf ON mf.id = p.fact_id "
            "WHERE p.status='pending' AND p.action IN ('DEFER','STALE') "
            "AND p.fact_id IS NOT NULL"
        ).fetchall()
        if dry_run:
            from collections import Counter
            return dict(Counter(r["action"] for r in rows))
        applied = {"DEFER": 0, "STALE": 0, "DEFER_kept_no_authority": 0}
        for r in rows:
            # D-guard (finding 4): DEFER прячет факт, полагая что он ДУБЛИРУЕТ
            # авторитет. Если авторитет его НЕ содержит (новый медфакт, ещё не в
            # genome/profile/labs) — прятать нельзя (потеря). Держим видимым.
            if r["action"] == "DEFER" and not _authority_contains(
                    conn, r["authority_source"],
                    f"{r['mkey'] or ''} {r['mval'] or ''}"):
                conn.execute(
                    "UPDATE memory_consolidation_proposals "
                    "SET status='deferred_kept' WHERE id=?", (r["id"],))
                applied["DEFER_kept_no_authority"] += 1
                continue
            cur = conn.execute(
                "UPDATE memory_facts SET active=0, valid_to=date('now'), "
                "updated_at=datetime('now') WHERE id=? AND valid_to IS NULL",
                (r["fact_id"],),
            )
            if cur.rowcount:
                applied[r["action"]] += 1
                conn.execute(
                    "UPDATE memory_consolidation_proposals SET status='applied' WHERE id=?",
                    (r["id"],),
                )
        return applied


def apply_merges(dry_run: bool = True) -> dict:
    """Применяет entity-level MERGE (человек-гейт, после ревью): создаёт канонический
    факт (value=структурный JSON, source='consolidation') + помечает исходные ключи
    active=0+valid_to (supersede, не delete → откат). status→applied. dry_run — считает."""
    _ensure_proposals_table()
    with _hdb.get_conn() as conn:
        props = conn.execute(
            "SELECT id, canonical_key, proposed_value, source_keys, medical_flag "
            "FROM memory_consolidation_proposals WHERE action='MERGE' "
            "AND status='pending' AND source_keys IS NOT NULL"
        ).fetchall()
        if dry_run:
            n_src = sum(len(_json.loads(p["source_keys"] or "[]")) for p in props)
            return {"entities": len(props), "source_keys": n_src}
        created, retired = 0, 0
        for p in props:
            conn.execute(
                "INSERT INTO memory_facts (mem_class, key, value, confidence, source, "
                "subject, critical_flag) VALUES ('fact', ?, ?, 0.9, 'consolidation', "
                "'self', ?)",
                (p["canonical_key"], p["proposed_value"], p["medical_flag"] or 0),
            )
            created += 1
            for k in _json.loads(p["source_keys"] or "[]"):
                cur = conn.execute(
                    "UPDATE memory_facts SET active=0, valid_to=date('now'), "
                    "updated_at=datetime('now') WHERE key=? AND mem_class='fact' "
                    "AND valid_to IS NULL AND source!='consolidation'", (k,))
                retired += cur.rowcount
            conn.execute("UPDATE memory_consolidation_proposals SET status='applied' "
                         "WHERE id=?", (p["id"],))
        return {"created": created, "retired": retired}


def summary() -> dict:
    """Сводка pending-предложений по действиям (для ревью)."""
    _ensure_proposals_table()
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT action, COUNT(*) FROM memory_consolidation_proposals "
            "WHERE status='pending' GROUP BY action"
        ).fetchall()
    return {r[0]: r[1] for r in rows}


# ── Непрерывная консолидация (A): nightly прогон + человек-гейт SUPERSEDE ──────

def pending_supersedes() -> list[dict]:
    """SUPERSEDE-предложения на человек-гейт, ещё НЕ отправленные (медицинские — не авто)."""
    _ensure_proposals_table()
    with _hdb.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, current_key, current_value, rationale, medical_flag "
            "FROM memory_consolidation_proposals WHERE status='pending' AND action='SUPERSEDE' "
            "AND sent_at IS NULL ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def mark_supersede_sent(proposal_id: int) -> None:
    """Квитанция: карточка ушла человеку — больше её не шлём, ждём ответа кнопкой."""
    with _hdb.get_conn() as conn:
        conn.execute("UPDATE memory_consolidation_proposals SET sent_at=datetime('now') "
                     "WHERE id=?", (proposal_id,))


def apply_supersede(proposal_id: int) -> bool:
    """Применить ОДНУ SUPERSEDE после одобрения в Telegram: пометить факт active=0+
    valid_to (обратимо, не delete). Возвращает True если факт снят."""
    with _hdb.get_conn() as conn:
        p = conn.execute(
            "SELECT fact_id, current_key, status FROM memory_consolidation_proposals "
            "WHERE id=?", (proposal_id,)).fetchone()
        if not p or p["status"] != "pending":
            return False
        if p["fact_id"]:
            cur = conn.execute(
                "UPDATE memory_facts SET active=0, valid_to=date('now'), "
                "updated_at=datetime('now') WHERE id=? AND valid_to IS NULL", (p["fact_id"],))
        else:
            cur = conn.execute(
                "UPDATE memory_facts SET active=0, valid_to=date('now'), "
                "updated_at=datetime('now') WHERE key=? AND mem_class='fact' "
                "AND valid_to IS NULL", (p["current_key"],))
        conn.execute("UPDATE memory_consolidation_proposals SET status='applied' WHERE id=?",
                     (proposal_id,))
        return cur.rowcount > 0


def reject(proposal_id: int) -> bool:
    """Отклонить предложение (человек сказал 'нет'): status→rejected, факт не тронут."""
    _ensure_proposals_table()
    with _hdb.get_conn() as conn:
        cur = conn.execute(
            "UPDATE memory_consolidation_proposals SET status='rejected' "
            "WHERE id=? AND status='pending'", (proposal_id,))
        return cur.rowcount > 0


def run_nightly() -> dict:
    """Непрерывная консолидация (launchd nightly). propose() над активными фактами;
    авто-применяет DEFER+STALE (обратимо); SUPERSEDE оставляет pending на Telegram-гейт.
    Чистит прежние pending, чтобы набор был свежий. Возвращает сводку для доставки."""
    # Партнёрская база могла родиться до появления таблицы: DELETE до ensure ронял
    # весь джоб «no such table» две ночи подряд (12–13.08) — консолидация у тенанта
    # не работала вовсе, а heartbeat честно старел.
    _ensure_proposals_table()
    with _hdb.get_conn() as conn:
        # Отправленную карточку, ждущую ответа, не стираем: до 28.09 её пересоздавали
        # под новым id и слали снова каждую ночь, а кнопки старой указывали в пустоту.
        conn.execute("DELETE FROM memory_consolidation_proposals WHERE status='pending' "
                     "AND NOT (action='SUPERSEDE' AND sent_at IS NOT NULL)")
    proposed = propose(persist=True)
    applied = apply_safe(dry_run=False)
    sup = pending_supersedes()
    return {"proposed": len(proposed), "auto_applied": applied,
            "pending_supersede": len(sup)}
