"""Contract + functional tests для save_alert / get_active_alerts (SX-17j)."""
from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.unit


def test_save_alert_signature():
    import health_db
    sig = inspect.signature(health_db.save_alert)
    required = [p for p in sig.parameters.values() if p.default is inspect.Parameter.empty]
    names = [p.name for p in required]
    assert "type_" in names or "type" in names, f"required args: {names}"
    assert "message" in names


def test_save_alert_then_get_active_returns_record(db):
    import health_db
    aid = health_db.save_alert(
        type_="medication_interaction",
        message="test alert",
        severity="high",
        source="survivorship_literature",
    )
    assert isinstance(aid, int) and aid > 0
    rows = health_db.get_active_alerts()
    assert any(r["id"] == aid for r in rows)
    assert next(r for r in rows if r["id"] == aid)["source"] == "survivorship_literature"


def test_get_active_alerts_filter_by_source_like(db):
    import health_db
    health_db.save_alert(type_="allergy", message="penicillin", source="patient_reported")
    health_db.save_alert(type_="medication_interaction", message="alc",
                         source="survivorship_literature")
    surv = health_db.get_active_alerts(source_like="survivorship%")
    assert all(r["source"].startswith("survivorship") for r in surv)
    assert len(surv) >= 1
