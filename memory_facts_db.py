#!/usr/bin/env python3.11
"""
memory_facts_db — типизированная разговорная память (Фаза 1, 2026-07-04).

ОТДЕЛЬНАЯ от legacy `memory` (там подсистема гипотез). Таблица memory_facts:
  mem_class ∈ fact|state|preference|question|recommendation|experiment.
  Актуальность через valid_to IS NULL (би-темпоральность), не через delete.

Публичные функции:
  save_fact()   — записать/обновить типизированный факт (upsert по mem_class+key).
  get_facts()   — прочитать актуальные факты класса.
  get_profile() — собрать профиль для потребителей (факты + открытые вопросы),
                  subject='self' only (R11: чужие факты не в профиль).

Консолидация (supersede/дедуп/подтверждение) — Фаза 2, здесь только storage+read.
"""
# INTENT: memory_temporal_axis — память с осью времени (durable/standing/transient).
#          Замысел и инварианты — subsystem_intent.yaml, раздел memory_temporal_axis.
from __future__ import annotations

# logging — модульный, а не локальный внутри функции (починка 2026-09-14, нить
# question-discard-axis). `retire_fact` звал logging без импорта и падал NameError
# РОВНО тогда, когда действительно что-то снимал: UPDATE к тому моменту уже был
# зафиксирован, поэтому строка снималась, а вызывающий получал исключение и обрывал
# партию. Снаружи это выглядело как «подъём вопросов иногда не доходит до конца».
# Поймано первым же тестом, который позвал НАСТОЯЩИЙ retire_fact, а не мок (§20).
import logging
import re as _re

import health_db as _hdb

_VALID_CLASSES = {"fact", "state", "preference", "question", "recommendation", "experiment"}

_EVENT_DATE = _re.compile(
    r'\d{4}-\d{2}-\d{2}|\bday\s*\d|\bдень\s*\d|'
    r'\b\d{1,2}\s+(?:марта|апреля|мая|января|февраля|июня|июля|августа|сентября|октября|ноября|декабря)|'
    r'\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d',
    _re.I)


_REL_DAY = _re.compile(r'\b(сегодня|вчера|today|yesterday)\b', _re.I)


def _derive_temporal_class(value: str, critical_flag: int) -> str:
    """Ф1: ось временнóй валидности ВЫВОДИМ из структуры, не спрашиваем LLM (Ф0: LLM↔эвристика
    согласны лишь 48% — авто-судье доверять нельзя). durable=critical (генетика/анатомия/статус
    из salience); transient=привязано к конкретной дате ИЛИ относительному дню (разовое событие,
    тускнеет); standing=консервативный дефолт (не тускнеет по возрасту — риск асимметричен,
    теряем факт хуже мусора). «сегодня/вчера» = момент → transient (ловит легаси с замороженным
    «сегодня», напр. #601, до Ф4-миграции). «сейчас/now» НЕ берём — это может быть стоячий статус."""
    if critical_flag:
        return "durable"
    if value and (_EVENT_DATE.search(value) or _REL_DAY.search(value)):
        return "transient"
    return "standing"


def derive_temporal_class(value: str, critical_flag: int = 0) -> str:
    """Публичный вход к структурному детектору класса (Ф1).

    Нужен писателям, которые знают СТРУКТУРУ строки лучше, чем она видна из
    итогового value. Независимо выдуманный немедицинский пример: запись
    «получили посылку 16 февраля? → да, получили». Дата в вопросе не должна
    автоматически определять класс отдельно сохраняемого ответа.
    Класс по-прежнему считает эта функция: суждение модели сюда не проникает
    (class_from_structure цел), меняется только вход."""
    return _derive_temporal_class(value, critical_flag)


def save_fact(mem_class: str, value: str, key: str | None = None,
              confidence: float = 0.8, source: str = "conversation",
              subject: str = "self", critical_flag: int = 0,
              temporal_class: str | None = None) -> int:
    """Записать типизированный факт. Upsert по (mem_class, key) когда key задан:
    старая актуальная строка помечается valid_to (supersede, не delete), пишется новая.
    Без key — просто append (наблюдения/вопросы). Возвращает id новой строки."""
    if mem_class not in _VALID_CLASSES:
        raise ValueError(f"неизвестный mem_class={mem_class!r}, ожидался из {_VALID_CLASSES}")
    # L3: авторитет-топик может писать только назначенный ему источник.
    # Запись из разговора на ключ, принадлежащий другому источнику, отклоняется
    # в точке записи (L1/L2 фильтруют чтение): defense-in-depth у источника.
    # Fail-open: сбой гварда не рушит запись.
    try:
        import beliefs as _bel
        _auth = _bel.authoritative_source_for_key(key)
        if _auth and source != _auth:
            logging.getLogger(__name__).info(
                "save_fact: отклонена запись source=%s на авторитет-топик key=%s (только '%s')",
                source, key, _auth)
            return -1  # не создано: конфаб на авторитетный топик не пишем
    except Exception:
        pass  # silent-ok: сбой гварда не должен рушить запись (fail-open by design)
    # Ф4.5 сетка (A): устойчивый медфакт (генетика/онкостатус/план лечения),
    # ошибочно попавший в state, → critical_flag=1 (never-decay). Главный риск из
    # аудита данных: наивный decay states снёс бы онкостатус/генотип/план лечения. Явный
    # critical_flag=1 не перетираем. Изолировано: сбой детектора не рушит запись.
    # never-decay durable имеет смысл только для state/fact; question/recommendation/
    # experiment живут своим циклом (вопрос, упоминающий ген, — не устойчивый факт).
    if not critical_flag and mem_class in ("state", "fact"):
        try:
            import memory_salience
            # key+value: идентичность гена/аналита часто в КЛЮЧЕ (genotype_<ГЕН>),
            # не в value (сам вариант) — как в D-guard и бэкофилле.
            if memory_salience.is_durable_fact(f"{key or ''} {value or ''}"):
                critical_flag = 1
        except Exception:
            pass
    # Ф1: ось временнóй валидности (только для state/fact; question/rec/experiment — свой цикл)
    # temporal_class от вызывающего принимается, только если он посчитан ЭТИМ же
    # детектором на более точном входе (derive_temporal_class) — см. её докстринг.
    if mem_class in ("state", "fact"):
        if critical_flag:
            # never-decay сильнее любого входа: переданный класс не должен
            # рассинхронить флаг и ось (critical=1 при standing читалось бы
            # как «вечный факт, который тускнеет»).
            temporal_class = "durable"
        elif temporal_class is None:
            temporal_class = _derive_temporal_class(value, critical_flag)
    else:
        temporal_class = None
    _hdb._ensure_memory_facts_table()
    with _hdb.get_conn() as conn:
        if key is not None:
            # supersede прежнюю актуальную версию того же ключа (не delete)
            conn.execute(
                """UPDATE memory_facts SET valid_to = date('now'), active = 0,
                          updated_at = datetime('now')
                   WHERE mem_class = ? AND key = ? AND subject = ? AND valid_to IS NULL""",
                (mem_class, key, subject),
            )
        elif mem_class in ("state", "fact"):
            # A-t1 (2026-07-07): дедуп-при-записи, КОНСЕРВАТИВНО — точное совпадение value.
            # Гасим прежнюю активную с идентичным текстом (одно событие не копится, как тройка
            # кольца). Фаззи-дедуп по (дата+тип метрики) — шире, но рискует схлопнуть разное:
            # отдельный follow-up. Только точное совпадение — безопасно.
            conn.execute(
                """UPDATE memory_facts SET valid_to = date('now'), active = 0,
                          updated_at = datetime('now')
                   WHERE mem_class = ? AND value = ? AND subject = ? AND valid_to IS NULL""",
                (mem_class, value, subject),
            )
        cur = conn.execute(
            """INSERT INTO memory_facts (mem_class, key, value, confidence, source,
                                         subject, critical_flag, temporal_class)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (mem_class, key, value, confidence, source, subject, critical_flag, temporal_class),
        )
        return cur.lastrowid


def get_facts(mem_class: str | None = None, subject: str = "self",
              active_only: bool = True, since_days: int | None = None) -> list[dict]:
    """Актуальные факты (valid_to IS NULL). mem_class=None → все классы.
    since_days: если задан — только записи с valid_from не старше N дней (свежесть)."""
    _hdb._ensure_memory_facts_table()
    q = "SELECT * FROM memory_facts WHERE subject = ?"
    params: list = [subject]
    if active_only:
        q += " AND valid_to IS NULL AND active = 1"
    if mem_class is not None:
        q += " AND mem_class = ?"
        params.append(mem_class)
    if since_days is not None:
        q += " AND valid_from >= date('now', ?)"
        params.append(f"-{int(since_days)} days")
    q += " ORDER BY updated_at DESC"
    with _hdb.get_conn() as conn:
        return [dict(r) for r in conn.execute(q, params).fetchall()]


def retire_fact(fact_id: int, reason: str) -> bool:
    """Снять факт с активных (active=0), не удаляя. True — если что-то изменилось.

    Дом ретайра один и он здесь, а не у вызывающего: `get_facts` уже фильтрует по
    `active=1`, значит выключатель существует — нужен был публичный вход к нему.
    Ручной UPDATE из соседнего модуля был бы вторым писателем у поля, которое решает
    видимость факта для ВСЕХ читателей памяти.

    Почему не DELETE: отбор кандидатов в вопросы считает «уже разобранным» то, чего
    нет среди активных. Удали строку — и кандидат вернётся следующим подъёмом, потому
    что забвение и решение «этот не нужен» для механизма выглядят одинаково.
    """
    # Схему обеспечиваем как соседи по модулю (`get_facts`, `save_fact`): эта
    # функция была единственной без такого вызова, и до 15.09 это не стреляло
    # только потому, что она ничего не писала, кроме уже существующего `active`.
    # Теперь пишет `retire_reason`, который заводит ИМЕННО ensure — без него на
    # свежей базе ретайр падал бы «no such column» (поймано оракулом, не чтением).
    _hdb._ensure_memory_facts_table()
    # Причина снятия хранится вместе с фактом, а не только в ротируемом логе.
    # Иначе после ротации нельзя восстановить основание решения.
    # Аргумент reason уже содержит причину; retire_reason даёт ей постоянный дом.
    with _hdb.get_conn() as conn:
        cur = conn.execute(
            "UPDATE memory_facts SET active = 0, updated_at = datetime('now'), "
            "retire_reason = ? WHERE id = ? AND active = 1", (reason, fact_id))
        changed = cur.rowcount > 0
    if changed:
        logging.getLogger(__name__).info(
            f"retire_fact: #{fact_id} снят с активных — {reason}")
    return changed


def mark_receipts_shown(fact_ids: list[int]) -> int:
    """Отметить, что квитанция «записал так, поправь» по этим фактам ДОСТАВЛЕНА.

    Зовётся ТОЛЬКО после подтверждённой доставки брифа (read-your-writes, как
    brief_state.commit): упал бриф → отметки нет → квитанция придёт завтра. Обратный
    порядок терял бы её навсегда, а потеря дороже повтора (риск Р3 нити brief-repeat).
    Идемпотентно: пишем только там, где ещё пусто. Возвращает число отмеченных."""
    ids = [int(i) for i in (fact_ids or [])]
    if not ids:
        return 0
    _hdb._ensure_memory_facts_table()
    with _hdb.get_conn() as conn:
        cur = conn.execute(
            "UPDATE memory_facts SET receipt_shown_at = datetime('now'), "
            "updated_at = datetime('now') "
            f"WHERE id IN ({','.join('?' * len(ids))}) AND receipt_shown_at IS NULL",
            ids,
        )
        return cur.rowcount or 0


def get_profile() -> dict:
    """Профиль для потребителей (бот, консилиум). Только subject='self' (R11).
    Возвращает: facts (актуальные, mem_class='fact'), states (свежие наблюдения),
    open_questions, preferences. Потребитель решает, что рендерить."""
    return {
        "facts":          get_facts("fact"),
        "states":         get_facts("state"),
        "preferences":    get_facts("preference"),
        "open_questions": get_facts("question"),
    }
