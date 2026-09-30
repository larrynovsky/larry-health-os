"""Триаж low-confidence pending staging (BL-LAB-CANON-2, #66).

Классифицирует ожидающие проверки строки staging в
review_status: `rejected` (мусор — авто-безопасно), `review` (человек), `gold`
(узкий авто-промоут-кандидат: agree+канон+ново+read+oracle-чисто, без кросс-ран
конфликта). Триаж только МЕТИТ; запись в канон — отдельный явный промоут (не тут),
по Primary-Based Protocol (единственный писатель канона — lab_promote/lab_specialized).

Публичный вход — `triage(run_ids, execute)`. dry-run по умолчанию.
"""
from __future__ import annotations

import collections

import health_db as _hdb
import lab_canon
import lab_oracles as _lo

norm = lab_canon.normalize
_CURRENCY = {"€", "eur", "nis", "₪", "$", "usd", "gbp", "£", "руб", "rub"}


def _impossible(cn: str, v, u) -> bool:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return False
    # Границы правдоподобия выбираются по идентичности с учётом единицы:
    # RDW-SD и RDW-CV имеют разные шкалы и не должны проверяться одной границей.
    cn = lab_canon.dimension_key(cn, u or "")
    # Конвертирует САМ оракул (`_o_bounds` unit-aware). До 2026-08-31 здесь стояла
    # своя конверсия, и в оракул уезжало УЖЕ conventional-значение с ИСХОДНОЙ
    # единицей — двойной пересчёт: креатинин (условно 88 мкмоль/л) → 1.0 mg/dL → ÷88.4 ещё
    # раз → 0.011 → «физиологически невозможно». Любая СИ-строка с правилом
    # конверсии отбрасывалась триажем как невозможная. Найдено дроем 2026-08-31.
    return bool(_lo._o_bounds({"canonical_name": cn, "value": v, "unit": u}))


def _values_agree(cn: str, v_a, u_a, v_b, u_b, rel_tol: float = 0.02) -> bool:
    """Совпадают ли два измерения ОДНОГО аналита, приведённые к одной шкале.

    Сравнение в conventional-единицах, а не в сырых: `Vitamin_D` в нмоль/л и
    в нг/мл — одно и то же измерение (÷2.496), и объявить их расхождением
    значило бы завести ложную тревогу на каждой строке в СИ.

    Незнакомая единица → `to_conventional` вернёт значение как есть, и величины
    разойдутся. Это ошибка В СТОРОНУ СТРОГОСТИ: строка уедет человеку, а не в
    отброс. Здесь так и надо — цена лишнего вопроса ниже цены потерянного анализа.
    """
    try:
        a, _ = lab_canon.to_conventional(cn, float(v_a), u_a or "")
        b, _ = lab_canon.to_conventional(cn, float(v_b), u_b or "")
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return False
    scale = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / scale <= rel_tol


def _classify(r: dict, canon_keys: set, canon_values: dict | None = None) -> tuple[str, str]:
    """→ (bucket, reason). bucket ∈ {rejected, review, gold}.

    `canon_values` — {(дата, norm(имя)): (значение, единица)} из канона.
    Ключ дедупа по дате и имени может столкнуть разные вещества, ошибочно
    сведённые к одному имени. Отбрасывание такой строки как дубля скрывает
    ошибку сопоставления; несовпадающие значения требуют отдельного разбора.

    Правило: совпал ключ, но РАЗОШЛОСЬ значение → это не дубль, а столкновение
    имён, и решает человек. Без `canon_values` поведение прежнее (совместимость
    с вызывающими, которые передают только множество ключей).
    """
    cn = r.get("canonical_name") or norm(r.get("raw_name") or "")
    # Идентичность = имя + размерность (ADR 2026-08-12): `RDW` в фл — RDW_SD, `NRBC`
    # в % — NRBC_pct. Ключ дедупа/коллизии ТЕМ ЖЕ правилом, иначе RDW_SD в фл
    # читается как коллизия с RDW-CV в % того же дня (дрой 2026-08-31).
    cn = lab_canon.dimension_key(cn, r.get("unit") or "")
    unit = (r.get("unit") or "").strip().lower()
    canonizable = cn in lab_canon.CANONICALS
    v = r.get("value")
    # GARBAGE → авто-reject
    if unit in _CURRENCY:
        return ("rejected", "валютная единица (инвойс)")
    # Дескриптор мочи (цвет/прозрачность) — описание, не измерение: дома нет по
    # построению, решение владельца 2026-08-31 (как страницы-комментарии 31.07).
    if (r.get("panel") or "").lower() == "urine" and lab_canon.is_urine_descriptor(r.get("raw_name")):
        return ("rejected", "дескриптор мочи (цвет/прозрачность) не импортируется")
    if (r.get("panel") or "").lower() == "urine" and lab_canon.is_urine_composite(r.get("raw_name")):
        return ("rejected", "композит микроскопии: находки несут строки по клеткам (Urine_WBC/RBC)")
    if v is not None and canonizable and _impossible(cn, v, r.get("unit")):
        return ("rejected", "физиологически невозможно")
    if (r.get("date"), norm(cn)) in canon_keys:
        got = (canon_values or {}).get((r.get("date"), norm(cn)))
        if got and got[0] is not None and v is not None \
                and not _values_agree(cn, v, r.get("unit"), got[0], got[1]):
            return ("review", "коллизия имени: значение расходится с каноном "
                              f"(канон {got[0]} {got[1] or ''}, строка {v} {r.get('unit') or ''})")
        return ("rejected", "уже в каноне (дубль)")
    # НЕ авто → человек
    if not canonizable:
        # Ни числа, ни текста результата — форме канона взять нечего, и человеку
        # решать не о чем (how-to lab_review_queue, группа 2, решение 2026-08-01).
        # Качественный результат (value_text) остаётся человеку: спец-слой его хранит.
        if v is None and not (r.get("value_text") or "").strip():
            return ("rejected", "немаппированный и без результата (ни числа, ни текста)")
        return ("review", "немаппированный аналит")
    if r.get("value_agreement") != "agree":
        return ("review", "модели разошлись/single")
    if r.get("date_source") != "read":
        return ("review", "дата не read (inherited/fallback)")
    return ("gold", "agree+канон+ново+read")


def triage(run_ids: list[str] | None = None, execute: bool = False) -> dict:
    """Классифицирует pending-строки и (при execute) проставляет review_status.
    gold с кросс-ран конфликтом значения (одна date+canonical, разные value) →
    понижается в review (Write-Write, не авто). Возвращает сводку."""
    _hdb.init_db()
    with _hdb.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM lab_results_staging WHERE review_status='pending'").fetchall()]
        # Значения канона нужны детектору коллизии (см. _classify): ключ дедупа
        # вещества не различает, и без значения «дубль» неотличим от столкновения.
        canon_values = {(d, norm(t)): (v, u) for d, t, v, u in conn.execute(
            "SELECT date, test_name, value, unit FROM lab_results").fetchall()}
        canon_keys = set(canon_values)
    if run_ids:
        rows = [r for r in rows if r.get("run_id") in run_ids]

    marks = {}  # id → (bucket, reason)
    gold_by_key = collections.defaultdict(list)  # (date,cn) → [(id,value)]
    for r in rows:
        bucket, reason = _classify(r, canon_keys, canon_values)
        marks[r["id"]] = (bucket, reason)
        if bucket == "gold":
            cn = r.get("canonical_name") or norm(r.get("raw_name") or "")
            gold_by_key[(r.get("date"), norm(cn))].append((r["id"], r.get("value")))
    # кросс-ран Write-Write: один ключ, разные значения → в review
    for key, items in gold_by_key.items():
        vals = {round(float(v), 4) for _, v in items if v is not None}
        if len(vals) > 1:
            for rid, _ in items:
                marks[rid] = ("review", "конфликт значения между прогонами (Write-Write)")

    buckets = collections.Counter(b for b, _ in marks.values())
    res = {"total": len(rows), "buckets": dict(buckets)}
    if not execute:
        return res
    with _hdb.get_conn() as conn:
        for rid, (bucket, _reason) in marks.items():
            conn.execute("UPDATE lab_results_staging SET review_status=?, "
                         "status_changed_at=datetime('now') WHERE id=?", (bucket, rid))
        conn.commit()
    res["executed"] = True
    return res
