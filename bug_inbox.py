#!/usr/bin/env python3.11
"""bug_inbox.py — приёмник отчётов о сбоях от чужих установок (только у владельца).

Нить bug-report, решения владельца 19.10: отдельный узел tailnet с публичным входом, отчёты
хранятся 90 дней, читает их ночной конвейер Claude. Вход анонимный, поэтому приёмник не верит
никому: схема проверяется здесь заново (bug_report.schema_problems — один дом схемы на обе
стороны), размер, частота и повтор судятся по базе, а не по памяти процесса (WSTG-BUSL-05).

Живёт в своём контейнере без томов с медданными и без ключей Health OS: взлом приёмника не
открывает ничего, кроме самих отчётов. Адрес клиента не сохраняется; в журнал не пишется тело.

Ответы: 201 {"number"} — новый; 200 {"number"} — повтор того же номера; 400 — не по схеме;
413 — велик; 429 — лимит; 404 — всё, кроме POST /report.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import bug_report

KEEP = timedelta(days=90)          # решение владельца 19.10
PER_INSTALL_DAY = 5
TOTAL_DAY = 200
log = logging.getLogger("bug_inbox")

_DDL = """CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY,
    report_id   TEXT NOT NULL UNIQUE,
    install     TEXT NOT NULL,
    received_at TEXT NOT NULL,
    body        TEXT NOT NULL)"""


def _path() -> Path:
    return Path(os.environ.get("BUG_INBOX_DB") or "/app/reports/reports.db")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(_path(), timeout=10, isolation_level=None)
    conn.execute(_DDL)
    return conn


def accept(raw: bytes, now: datetime | None = None) -> tuple[int, dict]:
    """Суд над одним телом запроса → (код, ответ). Вся логика приёма здесь, HTTP — обёртка."""
    now = now or datetime.now(timezone.utc)
    if len(raw) > bug_report.MAX_BYTES:
        return 413, {}
    try:
        report = json.loads(raw)
    except ValueError:
        return 400, {}
    if bug_report.schema_problems(report):
        return 400, {}
    rid = report["report_id"]
    install = rid.split(":", 1)[0]
    day = (now - timedelta(days=1)).isoformat()
    conn = _db()
    try:
        conn.execute("BEGIN IMMEDIATE")              # лимит и запись — одной транзакцией
        old = conn.execute("SELECT id FROM reports WHERE report_id=?", (rid,)).fetchone()
        if old:
            conn.execute("COMMIT")
            return 200, {"number": f"R-{old[0]}"}
        if (conn.execute("SELECT COUNT(*) FROM reports WHERE install=? AND received_at>?",
                         (install, day)).fetchone()[0] >= PER_INSTALL_DAY
                or conn.execute("SELECT COUNT(*) FROM reports WHERE received_at>?",
                                (day,)).fetchone()[0] >= TOTAL_DAY):
            conn.execute("COMMIT")
            return 429, {}
        cur = conn.execute("INSERT INTO reports(report_id, install, received_at, body) VALUES (?,?,?,?)",
                           (rid, install, now.isoformat(), json.dumps(report, ensure_ascii=False)))
        conn.execute("DELETE FROM reports WHERE received_at<?", ((now - KEEP).isoformat(),))
        conn.execute("COMMIT")
        return 201, {"number": f"R-{cur.lastrowid}"}
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def reports_since(after_id: int = 0) -> list[dict]:
    """Отчёты с id больше after_id — для ночного разбора. Тела уже прошли схему на входе.
    Только чтение: у разбора том приёмника смонтирован read-only. Базы ещё нет — пусто."""
    if not _path().exists():
        return []
    conn = sqlite3.connect(f"file:{_path()}?mode=ro", uri=True, timeout=10)
    try:
        return [{"id": i, "received_at": at, "report": json.loads(b)} for i, at, b in conn.execute(
            "SELECT id, received_at, body FROM reports WHERE id>? ORDER BY id", (after_id,))]
    finally:
        conn.close()


class _Handler(BaseHTTPRequestHandler):
    timeout = 10          # медленный клиент не держит поток вечно

    def do_POST(self):
        if self.path != "/report":
            return self._reply(404, {})
        try:
            n = int(self.headers.get("Content-Length") or -1)
        except ValueError:
            n = -1
        if n < 0 or n > bug_report.MAX_BYTES:
            return self._reply(413 if n > 0 else 400, {})
        try:
            code, body = accept(self.rfile.read(n))
        except Exception as e:  # noqa: BLE001 — сбой приёмника: клиент положит отчёт в очередь
            log.error("приёмник: сбой записи (%s)", type(e).__name__)
            code, body = 503, {}
        self._reply(code, body)

    def do_GET(self):
        self._reply(404, {})

    def _reply(self, code: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):   # адрес клиента в журнал не пишем
        log.info("%s %s", self.command, args[1] if len(args) > 1 else "")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    port = int(os.environ.get("BUG_INBOX_PORT") or 8766)
    _db().close()
    ThreadingHTTPServer(("0.0.0.0", port), _Handler).serve_forever()


if __name__ == "__main__":
    main()
