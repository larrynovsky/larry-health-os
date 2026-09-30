"""«Судить нечем» — оператору, не человеку (нить safety-cannot-judge, 2026-09-28).

Отсутствие отображения аналита или данных для тренда — техническая невозможность
суждения, а не тревога о здоровье. Например, карточка с неизвестным значением
не должна попадать в бриф как ухудшение. Долг отображения адресуется оператору.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

import integrity_tests as it
import safety_net as sn

pytestmark = pytest.mark.unit

_TREND = {"source": "lab_trend", "kind": sn.CANNOT_JUDGE, "level": sn.WARN, "metric": "CEA",
          "value": None, "direction": "up",
          "note": "тренд не построен: аналит не отображён в lab_name_loinc (loinc_match.py)"}
_NORM = {"source": "norm_unresolved", "kind": sn.CANNOT_JUDGE, "level": sn.WARN,
         "metric": "HGB", "value": 130, "direction": "unknown", "note": "судить нечем"}
_HRV = {"source": "lifestyle", "level": sn.WARN, "metric": "HRV", "value": 14,
        "direction": "?", "note": "HRV 14ms — падение 35%"}


def _run(monkeypatch, lab=(), trend=(), life=()):
    rec = {}
    monkeypatch.setattr(sn, "check_lab_alerts", lambda t: list(lab))
    monkeypatch.setattr(sn, "check_lab_trends", lambda t: list(trend))
    monkeypatch.setattr(sn, "check_lifestyle_alerts", lambda t: list(life))
    monkeypatch.setattr(sn, "_record_cannot_judge", lambda t, items: rec.update(items=items))
    return sn.run_safety_net(date(2026, 9, 28)), rec


def test_cannot_judge_never_reaches_person(monkeypatch):
    """alerts (карточки брифа), warn_summary (контекст GP), urgent (Telegram) — без долга."""
    res, rec = _run(monkeypatch, lab=[_NORM], trend=[_TREND], life=[_HRV])
    assert [a["metric"] for a in res["alerts"]] == ["HRV"]
    text = res["warn_summary"] + res["urgent_message"]
    assert "loinc" not in text and "судить нечем" not in text and "CEA" not in text
    assert {a["metric"] for a in res["cannot_judge"]} == {"CEA", "HGB"}
    assert {a["metric"] for a in rec["items"]} == {"CEA", "HGB"}


def test_unjudged_is_not_called_normal(monkeypatch):
    """Только долг, тревог нет → «в норме» не говорим: непроверенное ≠ нормальное."""
    res, _ = _run(monkeypatch, trend=[_TREND])
    assert res["alerts"] == [] and "в норме" not in res["warn_summary"]
    clean, _ = _run(monkeypatch)
    assert "в норме" in clean["warn_summary"]


def test_trend_without_mapping_is_tagged(monkeypatch):
    """Настоящий путь check_lab_trends: аналит мерялся, карты нет → вид CANNOT_JUDGE."""
    import labs_db
    import norm_documents
    monkeypatch.setattr(sn.db, "init_db", lambda: None)
    monkeypatch.setattr(sn, "_load_lab_trend_thresholds",
                        lambda: {"CEA": {"direction": "up", "n_readings": 2}})
    monkeypatch.setattr(norm_documents, "confirmation_metrics", lambda: set())
    monkeypatch.setattr(labs_db, "get_lab_trend_by_component",
                        lambda name, n=50: {"points": None, "n_rows": 2})
    out = sn.check_lab_trends(date(2026, 9, 28))
    assert len(out) == 1 and out[0]["kind"] == sn.CANNOT_JUDGE


def test_norm_without_reference_is_tagged(monkeypatch):
    """Настоящий путь check_lab_alerts: порог кратен референсу, референса нет нигде →
    вид CANNOT_JUDGE (не тревога о гемоглобине, а «судить нечем»)."""
    import config_db
    import labs_db
    monkeypatch.setattr(sn.db, "init_db", lambda: None)
    monkeypatch.setattr(sn.db, "get_recent_labs", lambda days: [
        {"test_name": "HGB", "value": 130, "date": "2026-09-01", "ref_low": None,
         "ref_high": None, "unit": "g/L"}])
    monkeypatch.setattr(sn, "_get_scheduled_lab_metrics", lambda d: {})
    monkeypatch.setattr(sn, "_load_lab_thresholds", lambda: {"HGB": [{"row": 1}]})
    monkeypatch.setattr(sn, "_resolve_thresholds", lambda rows, lo, hi: None)
    monkeypatch.setattr(config_db, "get_config", lambda k, d=None, conn=None: d)
    monkeypatch.setattr(labs_db, "get_modal_reference", lambda name, n: None)
    out = sn.check_lab_alerts(date(2026, 9, 28))
    assert len(out) == 1 and out[0]["kind"] == sn.CANNOT_JUDGE
    assert out[0]["source"] == "norm_unresolved"


def _tenant_db(path, rec):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE system_config (key TEXT PRIMARY KEY, value_text TEXT, "
                "value_num REAL, value_json TEXT, category TEXT, updated_at TEXT, source TEXT)")
    if rec is not None:
        con.execute("INSERT INTO system_config (key, value_json) VALUES (?, ?)",
                    (sn.CANNOT_JUDGE_KEY, json.dumps(rec, ensure_ascii=False)))
    con.commit()
    con.close()
    return path


def _ro(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def test_open_debts_freshness(tmp_path):
    item = {"metric": "CEA", "source": "lab_trend", "note": "x"}
    today = date(2026, 9, 28)
    fresh = _tenant_db(tmp_path / "a.db", {"date": "2026-09-27", "items": [item]})
    empty = _tenant_db(tmp_path / "b.db", {"date": "2026-09-28", "items": []})
    stale = _tenant_db(tmp_path / "c.db", {"date": "2026-09-01", "items": [item]})
    absent = _tenant_db(tmp_path / "d.db", None)
    assert sn.cannot_judge_open(_ro(fresh), today) == [item]
    assert sn.cannot_judge_open(_ro(empty), today) == []
    assert sn.cannot_judge_open(_ro(stale), today) is None
    assert sn.cannot_judge_open(_ro(absent), today) is None


def _sensor(tmp_path, monkeypatch, rec):
    dbp = _tenant_db(tmp_path / "health_partner" / "data" / "health.db", rec)
    monkeypatch.setattr(it, "_tenant_db_paths", lambda include_current=True: [str(dbp)])
    monkeypatch.setattr(it, "get_today", lambda: date(2026, 9, 28))
    it._warnings.clear()
    it.check_safety_net_can_judge()
    return [w for w in it._warnings if "судить нечем" in w[0]]


def test_sensor_rings_with_counts_only(tmp_path, monkeypatch):
    fired = _sensor(tmp_path, monkeypatch, {"date": "2026-09-28", "items": [
        {"metric": "CEA", "source": "lab_trend", "note": "x"},
        {"metric": "HGB", "source": "norm_unresolved", "note": "y"}]})
    assert fired
    detail = fired[0][0] + fired[0][1]
    assert "health_partner: 2" in detail
    assert "CEA" not in detail and "HGB" not in detail      # сиблингу — только счётчики


def test_sensor_silent_when_judged_or_unknown(tmp_path, monkeypatch):
    assert _sensor(tmp_path, monkeypatch, {"date": "2026-09-28", "items": []}) == []
    assert _sensor(tmp_path / "s", monkeypatch,
                   {"date": "2026-09-01", "items": [{"metric": "CEA"}]}) == []


def test_critical_level_advises_doctor_today_not_in_days():
    """CRITICAL (CTCAE grade 3) — «сегодня», а не «в ближайшие дни»; URGENT — прежний совет."""
    import safety_net as sn
    a = {"source": "lab", "metric": "PLT", "value": 40, "unit": "10^9/L", "date": "2026-09-28",
         "direction": "low", "level": sn.CRITICAL, "note": "x"}
    crit = sn.person_urgent_message([a], sn.CRITICAL, data_doubt=True)
    assert "сегодня" in crit and "в ближайшие дни" not in crit and "прежде чем тревожиться" not in crit
    urg = sn.person_urgent_message([dict(a, level=sn.URGENT)], sn.URGENT)
    assert "в ближайшие дни" in urg and "сегодня" not in urg


def test_no_text_forbids_calling_an_ambulance_and_one_card_has_one_deadline():
    """Решение владельца 29.09: «скорая не нужна» читалось как запрет на случай ухудшения;
    строка анализа и блок «Что делать» не называют два разных срока."""
    import yaml
    from pathlib import Path
    ru = yaml.safe_load((Path(__file__).resolve().parents[2] / "methodology/i18n/ru.yaml").read_text(encoding="utf-8"))
    assert not [k for k, v in ru.items() if "скорая не нужна" in str(v)]
    assert not [k for k, v in ru.items() if k.startswith("safety.line.lab_") and "без откладывания" in str(v)]
