"""Живость партнёрского крана литературы (включён решением владельца 27.09).

Три свойства: (1) кран не заведён — датчик молчит (до 27.09 он был выключен осознанно);
(2) заведён и отчёта нет вовсе — крик; (3) заведён, отчёт есть, плановый запуск не отметился —
крик; сиблингу без дат (C-19).
"""
import sqlite3
from datetime import datetime

import integrity_tests as it

LABEL = it.LITERATURE_SEARCH_PARTNER_LABEL


def _tenant(tmp_path, rows):
    home = tmp_path / "health_partner" / "data"
    home.mkdir(parents=True)
    dbp = home / "health.db"
    con = sqlite3.connect(dbp)
    con.execute("CREATE TABLE agent_reports (date TEXT, agent_type TEXT, created_at TEXT)")
    con.executemany("INSERT INTO agent_reports VALUES (?, 'literature_search', ?)", rows)
    con.commit()
    con.close()
    return dbp


def _run(tmp_path, monkeypatch, dbp, plist=True, covered=True):
    la = tmp_path / "Library" / "LaunchAgents"
    la.mkdir(parents=True)
    if plist:
        (la / f"{LABEL}.plist").write_text("<plist/>")
    monkeypatch.setenv("HEALTH_LAUNCHAGENTS_DIR", str(la))   # один дом каталога плистов (2026-09-29)
    monkeypatch.setattr(it, "_tenant_db_paths", lambda include_current=True: [str(dbp)])
    monkeypatch.setattr(it, "_schedule_covers", lambda label, art, now=None:
                        (covered, datetime(2026, 9, 27, 4, 10)))
    it._warnings.clear()
    it.check_literature_freshness_partner()
    return [w for w in it._warnings if "health_partner" in w[0]]


def test_tap_not_installed_is_silent(tmp_path, monkeypatch):
    dbp = _tenant(tmp_path, [])
    assert _run(tmp_path, monkeypatch, dbp, plist=False) == []


def test_installed_without_any_report_rings(tmp_path, monkeypatch):
    dbp = _tenant(tmp_path, [])
    fired = _run(tmp_path, monkeypatch, dbp)
    assert fired and "ни разу" in fired[0][0]


def test_missed_scheduled_run_rings_without_dates(tmp_path, monkeypatch):
    dbp = _tenant(tmp_path, [("2026-09-13", "2026-09-13 01:10:00")])
    fired = _run(tmp_path, monkeypatch, dbp, covered=False)
    assert fired and "не запускался" in fired[0][0]
    assert "2026-09-13" not in fired[0][0] + fired[0][1]      # C-19: сиблингу без дат


def test_fresh_run_is_silent(tmp_path, monkeypatch):
    dbp = _tenant(tmp_path, [("2026-09-27", "2026-09-27 01:10:00")])
    assert _run(tmp_path, monkeypatch, dbp, covered=True) == []
