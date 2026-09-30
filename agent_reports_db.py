"""agent_reports_db.py — доменный модуль agent_reports. Вынесен из health_db.py (Поток C, strangler-фасад)."""
from __future__ import annotations

import logging


log = logging.getLogger(__name__)


def save_agent_report(agent_type: str, agent_name: str, date_str: str,
                      has_findings: bool, data_queried: list, pubmed_ids: list,
                      peers_reviewed: list, changes_summary: str,
                      findings: str, recommendations: str = None,
                      data_requests: str = None, raw_output: dict = None,
                      period_days: int = 7) -> int:
    """Сохраняет отчёт агента."""
    import json as _j
    with _hdb.get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO agent_reports
              (date, agent_type, agent_name, period_days, has_findings,
               data_queried, pubmed_ids, peers_reviewed, changes_summary,
               findings, recommendations, data_requests, raw_output)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (date_str, agent_type, agent_name, period_days,
              int(has_findings),
              _j.dumps(data_queried), _j.dumps(pubmed_ids),
              _j.dumps(peers_reviewed), changes_summary,
              findings, recommendations, data_requests,
              _j.dumps(raw_output) if raw_output else None))
        return cur.lastrowid


def get_agent_report(agent_name: str, date_str: str = None,
                     n: int = 1) -> list[dict]:
    """Последние N отчётов агента."""
    with _hdb.get_conn() as conn:
        if date_str:
            rows = conn.execute("""
                SELECT * FROM agent_reports WHERE agent_name=? AND date<=?
                ORDER BY date DESC LIMIT ?
            """, (agent_name, date_str, n)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM agent_reports WHERE agent_name=?
                ORDER BY date DESC LIMIT ?
            """, (agent_name, n)).fetchall()
    return [dict(r) for r in rows]


def get_reports_with_findings(date_from: str, agent_type: str = None) -> list[dict]:
    """Отчёты с находками за период — для сборки итогового отчёта. СВЕЖИЕ ПЕРВЫМИ.

    Порядок — дата по убыванию, затем id. Сортировка только по типу и имени
    агента не определяет порядок отчётов внутри группы. Читатели используют
    `reports[0]` как последний отчёт: например, при двух версиях заключения
    первой должна идти более новая. Общий дом этого порядка — здесь."""
    with _hdb.get_conn() as conn:
        if agent_type:
            rows = conn.execute("""
                SELECT * FROM agent_reports
                WHERE date >= ? AND has_findings=1 AND agent_type=?
                ORDER BY date DESC, id DESC
            """, (date_from, agent_type)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM agent_reports
                WHERE date >= ? AND has_findings=1
                ORDER BY date DESC, id DESC
            """, (date_from,)).fetchall()
    return [dict(r) for r in rows]


# health_db — В КОНЦЕ модуля (BL-TEST-COLLECT-ALONE-1, 2026-09-24): он ре-экспортирует функции
# этого модуля, и импорт наверху давал цикл, если модуль импортировали первым (28 из 29 доменных
# модулей). Имя _hdb нужно только внутри функций — к их вызову health_db уже загружен.
import health_db as _hdb  # noqa: E402
