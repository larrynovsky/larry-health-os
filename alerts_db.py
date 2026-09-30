"""alerts_db.py — доменный модуль alerts. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations



def save_alert(type_: str, message: str, severity: str = "medium",
               source: str = "manual", active: bool = True) -> int:
    """SX-1.8: insert into alerts. Возвращает id."""
    with _hdb.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO alerts (type, severity, message, active, source) "
            "VALUES (?, ?, ?, ?, ?)",
            (type_, severity, message, 1 if active else 0, source),
        )
        return cur.lastrowid


def get_active_alerts(source_like: str | None = None) -> list[dict]:
    """SX-1.8: list active alerts, опционально фильтр по source LIKE pattern.

    Пример: get_active_alerts(source_like='survivorship%') вернёт только
    survivorship-правила, исключая пациентские allergies.
    """
    with _hdb.get_conn() as conn:
        if source_like:
            rows = conn.execute(
                "SELECT id, type, severity, message, source, created_at "
                "FROM alerts WHERE active=1 AND source LIKE ? "
                "ORDER BY id DESC",
                (source_like,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, type, severity, message, source, created_at "
                "FROM alerts WHERE active=1 "
                "ORDER BY id DESC"
            ).fetchall()
    return [dict(r) for r in rows]


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
