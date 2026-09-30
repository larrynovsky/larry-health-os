"""Аудит B26: утренний контекст судит анализ по референсу ТЕНАНТА (бланк строки → мода
бланков), а не по вшитому мужскому HGB 13.5–17.5. Краснеет, если литерал вернётся."""
from datetime import date

import hai_reports


def _ctx(monkeypatch, labs, bank):
    db = hai_reports.db
    monkeypatch.setattr(db, "get_stats", lambda *a, **k: {})
    monkeypatch.setattr(db, "get_day", lambda *a, **k: {})
    monkeypatch.setattr(db, "get_recent_checkins", lambda *a, **k: [])
    monkeypatch.setattr(db, "get_active_experiments", lambda *a, **k: [])
    monkeypatch.setattr(db, "get_recent_labs", lambda *a, **k: labs)
    monkeypatch.setattr(db, "get_lab_refs", lambda: bank)
    return hai_reports.build_context_block(date(2026, 9, 1))


def _line(text, name):
    return next(l for l in text.splitlines() if l.strip().startswith(name))


def test_hgb_по_бланку_строки_не_мужской_литерал(monkeypatch):
    lab = {"test_name": "HGB", "value": 12.6, "unit": "g/dL", "date": "2026-07-12",
           "ref_low": 12.0, "ref_high": 15.5}
    assert "⚠" not in _line(_ctx(monkeypatch, [lab], {}), "HGB")


def test_без_референса_нет_флага(monkeypatch):
    lab = {"test_name": "HGB", "value": 12.6, "unit": "g/dL", "date": "2026-07-12"}
    assert "⚠" not in _line(_ctx(monkeypatch, [lab], {}), "HGB")


def test_мода_бланков_флагует(monkeypatch):
    lab = {"test_name": "HGB", "value": 11.0, "unit": "g/dL", "date": "2026-07-12"}
    assert "⚠" in _line(_ctx(monkeypatch, [lab], {"HGB": (12.0, 15.5, "g/dL")}), "HGB")
