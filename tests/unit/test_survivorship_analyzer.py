"""survivorship_analyzer — metric_drift, shadow_check findings (SX-17e)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _setup(tmp_path, monkeypatch):
    cat = tmp_path / "instruments"
    cat.mkdir()
    cfg = tmp_path / "survivorship_config.yaml"
    cfg.write_text("pilot_flags:\n  analyzer_dry_run: true\n")
    (cat / "isi.json").write_text(json.dumps({
        "id": "isi", "name": "ISI", "cadence_days": 30,
        "items": [{"id": "q1", "text": "?", "subscale": "total"}],
        "subscales": [{"id": "total", "items": ["q1"],
                        "direction": "symptom_higher_worse"}],
        "shadow_rules": [{"item_or_subscale": "total",
                           "proxies": ["hrv"],
                           "min_window_days": 7,
                           "divergence_threshold": 0.5}],
    }, ensure_ascii=False))
    import survivorship_analyzer as sa
    monkeypatch.setattr(sa, "INSTRUMENTS_DIR", cat)
    monkeypatch.setattr(sa, "CONFIG", cfg)
    return sa


def test_analyzer_returns_no_pro_yet_when_empty(db, tmp_path, monkeypatch):
    sa = _setup(tmp_path, monkeypatch)
    result = sa.run(dry_run=True)
    inst_findings = result["per_instrument"]["isi"]
    assert any(f["type"] == "no_pro_yet" for f in inst_findings)


def test_analyzer_detects_metric_drift(db, tmp_path, monkeypatch):
    sa = _setup(tmp_path, monkeypatch)
    # 30 дней назад: HRV=25; последние 7 дней: HRV=15 (drift -40%)
    from datetime import date, timedelta
    for i in range(30, 7, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        db.add_daily_metrics(d, hrv=25.0, sleep_deep=1.0, readiness=78, resting_hr=60)
    for i in range(7, 0, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        db.add_daily_metrics(d, hrv=15.0, sleep_deep=1.0, readiness=78, resting_hr=60)
    result = sa.run(dry_run=True)
    drifts = [f for f in result["global"] if f.get("type") == "metric_drift"]
    assert any(f.get("metric") == "hrv" for f in drifts)
