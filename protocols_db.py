"""protocols_db.py — доменный модуль protocols.

Вынесен из health_db.py (Поток C рефакторинга, 2026-06-27, strangler-фасад).
health_db.py ре-экспортирует эти функции внизу — импортёры не затронуты.
"""
from __future__ import annotations

from _time_inject import get_today


def get_active_protocols(domain=None):
    with _hdb.get_conn() as conn:
        if domain:
            rows = conn.execute(
                "SELECT id,title,behavior,rationale,frequency,domain,"
                "reminder_days,linked_hypothesis_id,created_at,review_date "
                "FROM protocols WHERE status='active' AND domain=? ORDER BY id",
                (domain,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id,title,behavior,rationale,frequency,domain,"
                "reminder_days,linked_hypothesis_id,created_at,review_date "
                "FROM protocols WHERE status='active' ORDER BY id"
            ).fetchall()
    return [dict(r) for r in rows]


def unactionable_protocol_ids(protocols: list[dict]) -> list[int]:
    """Действующие протоколы без поведения или без даты пересмотра: их нельзя ни выполнить,
    ни спросить о выполнении, ни пересмотреть вовремя."""
    return [p["id"] for p in protocols
            if not str(p.get("behavior") or "").strip() or not p.get("review_date")]


def overdue_review_details(protocols: list[dict], today) -> list[str]:
    """Действующие протоколы с прошедшей датой пересмотра — по строке на каждый:
    «#id «название» — просрочен на N дн. (review_date)», самые старые первыми.
    Без имён и сроков предупреждение датчика нельзя превратить в вопрос владельцу
    (карточка 2026-11-12: «4 протоколов…» — и больше ничего)."""
    from datetime import date as _date
    out = []
    for p in protocols:
        rd = str(p.get("review_date") or "")[:10]
        try:
            days = (today - _date.fromisoformat(rd)).days
        except ValueError:
            continue  # silent-ok: нет даты или не разбирается — это unactionable_protocol_ids
        if days > 0:
            title = str(p.get("title") or "").strip() or "без названия"
            out.append((days, f"#{p['id']} «{title[:60]}» — просрочен на {days} дн. ({rd})"))
    return [line for _, line in sorted(out, key=lambda x: -x[0])]


def save_protocol(protocol: dict) -> int:
    """Сохраняет протокол в таблицу protocols.

    protocol может содержать:
      review_date (str YYYY-MM-DD) — абсолютная дата пересмотра.
      review_in_days (int)         — вычисляет review_date от сегодня.
    """
    from datetime import datetime as _dt, timedelta as _td
    if not str(protocol.get("behavior") or "").strip():
        raise ValueError("protocol without behavior is not saved")
    review_date = protocol.get("review_date")
    if protocol.get("review_in_days") and not review_date:
        review_date = str(get_today() + _td(days=int(protocol["review_in_days"])))
    with _hdb.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO protocols
               (title, behavior, rationale, frequency, linked_hypothesis_id,
                linked_experiment_id, reminder_days, status, created_at,
                review_date)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                protocol["title"],
                protocol["behavior"],
                protocol.get("rationale", ""),
                protocol.get("frequency", ""),
                protocol.get("linked_hypothesis_id"),
                protocol.get("linked_experiment_id"),
                protocol.get("reminder_days", "[]"),
                "active",
                _dt.now().isoformat(timespec="seconds"),
                review_date,
            )
        )
        return cur.lastrowid


def retire_protocol(protocol_id: int, note: str = "") -> bool:
    """Снимает протокол (status → retired)."""
    from datetime import datetime as _dt
    with _hdb.get_conn() as conn:
        conn.execute(
            "UPDATE protocols SET status='retired', retired_at=?, notes=? WHERE id=?",
            (_dt.now().isoformat(timespec="seconds"), note, protocol_id)
        )
    return True


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
