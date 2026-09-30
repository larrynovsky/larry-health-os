"""consultations_db.py — приёмы врача. Вынесен из health_db.py (Поток C, strangler-фасад).

Дом приёма — events(event_type='encounter') + encounters (медкарта). Таблица consultations —
архив: когда промпты читали эту копию, а медкарта — оригинал, приёмы, отсутствовавшие
в копии, не доходили в промпты (BL-CONSULT-SECOND-HOME-1).
API прежний (date/specialist_type/specialist_name/platform/key_findings/source_file), чтобы
читатели не менялись; строки теперь собираются из медкарты.
"""
from __future__ import annotations

_SELECT = (
    "SELECT e.id, e.effective_date AS date, n.specialty AS specialist_type, "
    "e.performer AS specialist_name, e.location AS platform, "
    "n.assessment AS key_findings, e.notes AS source_file "
    "FROM events e JOIN encounters n ON n.event_id = e.id "
    "WHERE e.event_type = 'encounter' "
)


def specialty_key(s: str | None) -> str:
    """Одна специальность под разными словами сводится к общему ключу.

    Вымышленный пример: 'exampleologist' / 'Exampleology' / 'exampleology' → 'exampleology'.
    Без нормализации /visit пропускал бы более новую запись примера под другим написанием
    и выбирал старый визит. Суффикс -ist → -y покрывает врач/область."""
    k = (s or "").strip().lower()
    return k[:-3] + "y" if k.endswith("ist") else k


def _rows(specialist_type: str | None, n: int) -> list[dict]:
    with _hdb.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            _SELECT + "ORDER BY e.effective_date DESC, e.id DESC").fetchall()]
    if specialist_type:
        want = specialty_key(specialist_type)
        rows = [r for r in rows if specialty_key(r["specialist_type"]) == want]
    return rows[:n]


def get_consultations(n: int = 10, specialist_type: str = None) -> list:
    """Последние N приёмов врача (из медкарты)."""
    return _rows(specialist_type, n)


def get_last_consultation(specialist_type: str = None) -> dict | None:
    """Последний приём врача, опционально по специальности (написания сводятся specialty_key)."""
    rows = _rows(specialist_type, 1)
    return rows[0] if rows else None


def consultation_exists(date_str: str, specialist_type: str) -> bool:
    """Есть ли в медкарте приём этой даты и специальности — ключ идемпотентности импортёров."""
    want = specialty_key(specialist_type)
    with _hdb.get_conn() as conn:
        rows = conn.execute(_SELECT + "AND e.effective_date = ?", (date_str,)).fetchall()
    return any(specialty_key(r["specialist_type"]) == want for r in rows)


def save_consultation(date_str: str, specialist_type: str,
                      specialist_name: str = None, platform: str = None,
                      key_findings: str = None, source_file: str = None) -> int:
    """Записывает приём врача в медкарту (events + encounters) — в тот же вид, что
    _migrate_consultations_to_events. Возвращает id события."""
    with _hdb.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO events
               (event_type, effective_date, status, performer, performer_role,
                location, recorded_by, attachments, notes)
               VALUES ('encounter', ?, 'completed', ?, 'specialist', ?, 'provider', '[]', ?)""",
            (date_str, specialist_name or specialist_type, platform or "", source_file or ""))
        evt_id = cur.lastrowid
        conn.execute("INSERT INTO encounters (event_id, specialty, assessment) VALUES (?, ?, ?)",
                     (evt_id, specialist_type, key_findings or ""))
        return evt_id


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
