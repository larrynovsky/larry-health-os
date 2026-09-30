#!/usr/bin/env python3.11
"""Unit-тест датчика свежести: алерт только на реальном обрыве (age > limit×2)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import oura_freshness_check as o


def test_fresh_no_alert():
    o.db.check_data_freshness = lambda sources=None: {}
    assert o.run_check(notify=False) == {}


def test_marginal_shift_not_alerted():
    # 30ч при лимите 26ч — суточный сдвиг, НЕ обрыв (30 < 26×2)
    o.db.check_data_freshness = lambda sources=None: {
        "apple_health": {"age_hours": 30.0, "limit_hours": 26.0, "message": "x"}
    }
    assert o.run_check(notify=False) == {}


def test_real_break_detected():
    # 60ч при лимите 26ч — >2 суток, реальный обрыв
    o.db.check_data_freshness = lambda sources=None: {
        "oura": {"age_hours": 60.0, "limit_hours": 26.0,
                 "message": "Данные oura устарели: 60.0ч (лимит 26ч)"}
    }
    r = o.run_check(notify=False)
    assert "oura" in r and r["oura"]["age_hours"] == 60.0


if __name__ == "__main__":
    test_fresh_no_alert()
    test_marginal_shift_not_alerted()
    test_real_break_detected()
    print("TEST PASS")
