"""
generated_food_rules.py — СКЛАД сгенерированных диет-правил (тир 2, нить data-in-code-9).

Отдельная полка (решение владельца 2026-07-15): диет-правило ≠ гипотеза (гипотезу проверяют и
закрывают, правило применяют к брифам), поэтому своя таблица, а не memory/hypotheses_cbcr.
Механику версий/провенанса переиспользуем от гипотез: у каждого правила версия+история
(supersede прежней + insert новой), провенанс (кто/модель/дата/источники), статус.

Статусы: shadow (сгенерировано, НЕ применяется — режим инкремента 2) · active (применяется
брифом) · superseded (заменено новой версией) · rejected (не прошло пол/критик).

Правило хранится как illness-script-рычаг (DESIGN §2): condition/fault/lever/frame/evidence —
в payload_json. Пол безопасности (food_floor) проверяет frame ПЕРЕД сохранением active; здесь
только фиксируем результат (floor_ok) — сам склад решений не принимает.
"""
from __future__ import annotations

import json

_TABLE = "generated_food_rules"

_VALID_STATUS = {"shadow", "active", "superseded", "rejected"}


def _db():
    import health_db
    return health_db


def ensure_table(conn=None) -> None:
    c = conn or _db().get_conn()
    c.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_key           TEXT NOT NULL,
            version            INTEGER NOT NULL DEFAULT 1,
            status             TEXT NOT NULL DEFAULT 'shadow',
            payload_json       TEXT NOT NULL,
            floor_ok           INTEGER NOT NULL DEFAULT 0,
            critique           TEXT NOT NULL DEFAULT '',
            generated_by       TEXT NOT NULL DEFAULT '',
            model              TEXT NOT NULL DEFAULT '',
            model_version_date TEXT NOT NULL DEFAULT '',
            data_sources       TEXT NOT NULL DEFAULT '[]',
            created_at         TEXT DEFAULT (datetime('now')),
            updated_at         TEXT DEFAULT (datetime('now'))
        )
    """)
    c.execute(f"CREATE INDEX IF NOT EXISTS idx_genfood_key ON {_TABLE}(rule_key, status)")
    try:
        c.commit()
    except Exception:  # silent-ok: conn уже в транзакции вызывающего
        pass


def save_rule(rule_key: str, payload: dict, *, provenance: dict | None = None,
              floor_ok: bool = False, critique: str = "", status: str = "shadow",
              conn=None) -> int:
    """Сохранить новую ВЕРСИЮ правила rule_key: прежние не-superseded → superseded, insert новой.
    Возвращает номер новой версии. Провенанс: generated_by/model/model_version_date/data_sources."""
    if status not in _VALID_STATUS:
        raise ValueError(f"недопустимый status: {status!r}")
    c = conn or _db().get_conn()
    ensure_table(c)
    prov = provenance or {}
    row = c.execute(
        f"SELECT COALESCE(MAX(version), 0) FROM {_TABLE} WHERE rule_key=?", (rule_key,)).fetchone()
    new_version = int(row[0]) + 1
    # Прежняя ТЕНЕВАЯ версия этого ключа → superseded (история сохраняется строками). Active НЕ
    # трогаем: 1-цикл лаг (DESIGN §6.1) — бриф применяет прошломесячный рулбук, пока promote_shadow_
    # to_active не сменит его. Иначе новая генерация обнулила бы живое правило до промоушена.
    c.execute(
        f"UPDATE {_TABLE} SET status='superseded', updated_at=datetime('now') "
        f"WHERE rule_key=? AND status='shadow'", (rule_key,))
    c.execute(
        f"INSERT INTO {_TABLE} (rule_key, version, status, payload_json, floor_ok, critique, "
        f"generated_by, model, model_version_date, data_sources) "
        f"VALUES (?,?,?,?,?,?,?,?,?,?)",
        (rule_key, new_version, status, json.dumps(payload, ensure_ascii=False),
         1 if floor_ok else 0, critique,
         prov.get("generated_by", ""), prov.get("model", ""),
         prov.get("model_version_date", ""),
         json.dumps(prov.get("data_sources", []), ensure_ascii=False)))
    try:
        c.commit()
    except Exception:  # silent-ok: conn уже в транзакции вызывающего
        pass
    return new_version


def save_rejected(rule_key: str, payload: dict, reasons: list[str], *,
                  provenance: dict | None = None, conn=None) -> None:
    """Отклонённое критиком предложение — в склад со status='rejected', БЕЗ супersede живых
    версий ключа (прошлый рулбук держим). Зачем хранить (решение владельца 2026-09-01,
    вопрос 3 «А», без ретеншена): сводка показывает, чего генератор хотел и почему не прошло;
    повторяющийся отказ по одному условию — сигнал завести строку в clinical_kb руками."""
    c = conn or _db().get_conn()
    ensure_table(c)
    prov = provenance or {}
    c.execute(
        f"INSERT INTO {_TABLE} (rule_key, version, status, payload_json, floor_ok, critique, "
        f"generated_by, model, model_version_date, data_sources) "
        f"VALUES (?,?,?,?,?,?,?,?,?,?)",
        (rule_key, 0, "rejected", json.dumps(payload, ensure_ascii=False), 0,
         "; ".join(reasons), prov.get("generated_by", ""), prov.get("model", ""),
         prov.get("model_version_date", ""),
         json.dumps(prov.get("data_sources", []), ensure_ascii=False)))
    try:
        c.commit()
    except Exception:  # silent-ok: conn уже в транзакции вызывающего
        pass


def _row_to_dict(r) -> dict:
    return {"id": r[0], "rule_key": r[1], "version": r[2], "status": r[3],
            "payload": json.loads(r[4]), "floor_ok": bool(r[5]), "critique": r[6],
            "generated_by": r[7], "model": r[8], "model_version_date": r[9],
            "data_sources": json.loads(r[10] or "[]"), "created_at": r[11]}


_COLS = ("id, rule_key, version, status, payload_json, floor_ok, critique, "
         "generated_by, model, model_version_date, data_sources, created_at")


def get_rules(status: str | None = None, rule_key: str | None = None, conn=None) -> list[dict]:
    """Правила склада с фильтром по статусу/ключу. Пустой результат при отсутствии таблицы."""
    try:
        c = conn or _db().get_conn()
        where, params = [], []
        if status is not None:
            where.append("status=?"); params.append(status)
        if rule_key is not None:
            where.append("rule_key=?"); params.append(rule_key)
        sql = f"SELECT {_COLS} FROM {_TABLE}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY rule_key, version"
        return [_row_to_dict(r) for r in c.execute(sql, params).fetchall()]
    except Exception:  # silent-ok: таблицы ещё нет → склад пуст
        return []


def get_active_rules(conn=None) -> list[dict]:
    """Правила, применяемые брифом (status=active)."""
    return get_rules(status="active", conn=conn)


def promote_shadow_to_active(conn=None) -> int:
    """ФЛИП (инкремент-финал): floor-passing shadow-правила → active (начинают применяться брифом).
    Прежние active того же ключа → superseded. Вызывается ежемесячно ПЕРЕД новой генерацией: так
    промоутится ПРОШЛЫЙ теневой рулбук (1-цикл лаг, DESIGN §6.1), а свежий остаётся в shadow до
    следующего цикла — владелец успевает увидеть его в месячной сводке. Возвращает число промоутнутых.
    Пол — предохранитель: не-floor_ok в active не пускаем (в apply он и так fail-closed)."""
    c = conn or _db().get_conn()
    ensure_table(c)
    rows = c.execute(
        f"SELECT id, rule_key FROM {_TABLE} WHERE status='shadow' AND floor_ok=1").fetchall()
    n = 0
    promoted: list[str] = []
    for rid, key in rows:
        c.execute(f"UPDATE {_TABLE} SET status='superseded', updated_at=datetime('now') "
                  f"WHERE rule_key=? AND status='active'", (key,))
        c.execute(f"UPDATE {_TABLE} SET status='active', updated_at=datetime('now') WHERE id=?", (rid,))
        n += 1
        promoted.append(key)
    # Поколение ЗАМЕНЯЕТ рулбук (2026-09-01): active-ключи, которых в новом shadow нет, →
    # superseded. Иначе склад только рос (7→14 за два месяца: модель каждый месяц даёт новые slug
    # для тех же состояний), а «будет снято» в сводке не снимало ничего. Исключение — решение
    # владельца «при провале держим прошлый рулбук»: промоутить нечего → active не трогаем.
    if promoted:
        marks = ",".join("?" * len(promoted))
        c.execute(f"UPDATE {_TABLE} SET status='superseded', updated_at=datetime('now') "
                  f"WHERE status='active' AND rule_key NOT IN ({marks})", promoted)
    try:
        c.commit()
    except Exception:  # silent-ok: conn уже в транзакции вызывающего
        pass
    return n
