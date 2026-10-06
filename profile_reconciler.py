#!/usr/bin/env python3.11
"""
profile_reconciler.py — синхронизирует event-based поля patient_profile.

Область: поля, которые меняются при появлении новых events (процедуры,
обследования). Labs НЕ трогает — они инжектируются напрямую через
patient_context._labs_snapshot() без промежуточного хранения.

Запускается:
  - вручную: python3.11 profile_reconciler.py
  - автоматически: перед generate_constitutions.py
  - по расписанию: ежедневно через launchd (TODO)
"""
from __future__ import annotations
from _time_inject import get_now  # seam

import sqlite3
from datetime import datetime
from pathlib import Path

import health_db as _db

DB = _db.DB_PATH  # A++ R1/R2 guard — поднимается из health_db._validate_db_path
NOW = get_now().isoformat()
UPDATED_BY = "profile_reconciler"


def _upsert(conn: sqlite3.Connection, key: str, value_text: str, category: str) -> str:
    existing = conn.execute(
        "SELECT key FROM patient_profile WHERE key=?", (key,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE patient_profile SET value_text=?, category=?, updated_at=?, updated_by=? WHERE key=?",
            (value_text, category, NOW, UPDATED_BY, key),
        )
        return "upd"
    else:
        conn.execute(
            "INSERT INTO patient_profile (key, value_text, category, updated_at, updated_by) "
            "VALUES (?,?,?,?,?)",
            (key, value_text, category, NOW, UPDATED_BY),
        )
        return "new"


def _reconcile_port(conn: sqlite3.Connection) -> tuple[str, str] | None:
    """Порт-катетер: последнее событие из events."""
    row = conn.execute("""
        SELECT event_type, effective_date, location, notes
        FROM events
        WHERE event_type LIKE '%port%' OR event_type LIKE '%Port%'
           OR event_type LIKE '%catheter%' OR notes LIKE '%Port-A-Cath%'
        ORDER BY effective_date DESC
        LIMIT 1
    """).fetchone()
    if not row:
        return None
    etype, edate, location, notes = row
    etype_lo  = (etype  or "").lower()
    notes_lo  = (notes  or "").lower()
    loc_str   = f" ({location})" if location else ""
    if any(w in etype_lo or w in notes_lo for w in ("remov", "удал", "explant")):
        val = f"удалён {edate}{loc_str}"
    elif any(w in etype_lo or w in notes_lo for w in ("insert", "instal", "plac", "установ")):
        val = f"установлен {edate}{loc_str}"
    else:
        val = f"{etype} {edate}{loc_str}"
    return "medical.port_catheter", val


def _reconcile_pet(conn: sqlite3.Connection) -> tuple[str, str] | None:
    """Последний PET-CT: только дата. Результат остаётся мануальным."""
    # До 04.10.2026 бралось ПОСЛЕДНЕЕ событие, где «PET» встречался хоть где-то в notes, —
    # туда попадал и пересказ расчётного листа страховой. Теперь обследованием считается
    # событие, у которого PET в имени САМОГО документа и документ не финансовый
    # (import_all.is_financial — существующий дом признака). Нет такого — профиль не трогаем.
    from import_all import is_financial
    has_att = "attachments" in {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    rows = conn.execute(f"""
        SELECT effective_date, notes, {'attachments' if has_att else 'NULL'}
        FROM events
        WHERE event_type LIKE '%PET%' OR event_type LIKE '%pet-ct%'
           OR notes LIKE '%PET%' OR notes LIKE '%ПЭТ%'
           {"OR attachments LIKE '%PET%'" if has_att else ''}
        ORDER BY effective_date DESC
    """).fetchall()
    for edate, notes, attachments in rows:
        name = document_name(notes, attachments)
        if name and ("pet" in name.lower() or "пэт" in name.lower()) \
                and not is_financial(Path(name)):
            return "medical.last_pet_ct", edate
    return None


def document_name(notes, attachments) -> str | None:
    """Имя файла-источника события: attachments.source_file, иначе notes, если это путь."""
    import json
    att = attachments
    for _ in range(2):  # attachments бывает JSON внутри JSON-строки
        if isinstance(att, str):
            try:
                att = json.loads(att)
            except ValueError:
                break
    if isinstance(att, dict) and att.get("source_file"):
        return str(att["source_file"])
    n = (notes or "").strip()
    if "/" in n and "\n" not in n and n.lower().endswith((".pdf", ".jpg", ".jpeg", ".png")):
        return n
    return None


def _reconcile_weight(conn: sqlite3.Connection) -> tuple[str, str] | None:
    """Вес из живого источника (Fitdays → daily_metrics.weight), СО ШТАМПОМ ДАТЫ.

    Дата обязательна: источник может перестать обновляться, а ручное значение
    профиля — отстать от последнего измерения. Без даты нельзя отличить устаревшую
    запись профиля от паузы в поступлении данных.
    Ср. §18 — утверждение о предмете вне носителя несёт дату замера.
    """
    row = conn.execute("""
        SELECT date, weight FROM daily_metrics
        WHERE weight IS NOT NULL AND weight > 0
        ORDER BY date DESC LIMIT 1
    """).fetchone()
    if not row:
        return None
    d, w = row
    return "identity.weight_kg", f"{w:g} (замер {d})"


# Единый список сверок: раньше кортеж был продублирован в двух ветках reconcile(),
# и третья сверка потребовала бы правки обоих мест — классическая точка расхождения.
_RECONCILERS = (
    (_reconcile_port,   "medical"),
    (_reconcile_pet,    "medical"),
    (_reconcile_weight, "identity"),
)


def reconcile(db_path: Path | None = None) -> list[tuple[str, str, str]]:
    """Возвращает список (action, key, value) для каждого изменения.

    db_path параметр сохранён для тестов (override). При None — через
    health_db.get_conn() (A++ R1/R2 guard).
    """
    changes: list[tuple[str, str, str]] = []
    if db_path is None:
        with _db.get_conn() as conn:
            for fn, category in _RECONCILERS:
                result = fn(conn)
                if result:
                    key, val = result
                    action = _upsert(conn, key, val, category)
                    changes.append((action, key, val))
    else:
        # Test override path — прямой sqlite3.connect к указанному файлу.
        conn = sqlite3.connect(str(db_path))
        try:
            for fn, category in _RECONCILERS:
                result = fn(conn)
                if result:
                    key, val = result
                    action = _upsert(conn, key, val, category)
                    changes.append((action, key, val))
            conn.commit()
        finally:
            conn.close()
    return changes


def drift(conn: sqlite3.Connection) -> list[str]:
    """Ключи, где хранимый patient_profile разошёлся с живым источником.

    Периметр НЕ список — это сами _RECONCILERS: каждый ремонтник умеет пересчитать
    живое значение, датчик сверяет его с сохранённым (§17: периметр вычисляется, не
    описывается; новый ремонтник попадает под сторож без правки датчика).

    Возвращает ТОЛЬКО имена ключей — значение профиля это health-данные, им нельзя в
    алерт (§16/§19). Read-only: зовёт SELECT-ветки ремонтников, _upsert не трогает.
    Ключ без живого значения (ремонтник вернул None — источник молчит) НЕ расхождение:
    сверять не с чем, а протухание молчащего источника стережёт штамп даты в значении.
    """
    diverged: list[str] = []
    for fn, _category in _RECONCILERS:
        result = fn(conn)
        if not result:
            continue
        key, live = result
        row = conn.execute(
            "SELECT value_text FROM patient_profile WHERE key=?", (key,)
        ).fetchone()
        stored = row[0] if row else None
        if stored != live:
            diverged.append(key)
    return diverged


if __name__ == "__main__":
    changes = reconcile()
    if changes:
        print(f"profile_reconciler: {len(changes)} изменений")
        for action, key, val in changes:
            print(f"  [{action}] {key} = {val}")
    else:
        print("profile_reconciler: нет изменений")
