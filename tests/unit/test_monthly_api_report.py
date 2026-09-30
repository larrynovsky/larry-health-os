"""Unit-тесты на monthly_api_report.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

import monthly_api_report as mar


def test_cost_haiku():
    # 1M input + 1M output на haiku = $1 + $5 = $6
    assert mar._cost("claude-haiku-4-5", 1_000_000, 1_000_000) == pytest.approx(6.0)


def test_cost_sonnet():
    assert mar._cost("claude-sonnet-4-6", 1_000_000, 1_000_000) == pytest.approx(18.0)


def test_aggregate_empty_log(tmp_path, monkeypatch):
    monkeypatch.setattr(mar, "SPEND_LOG", tmp_path / "missing.log")
    rep = mar.aggregate("2026-04")
    assert rep["calls"] == 0
    assert rep["total_usd"] == 0


def test_aggregate_with_data(tmp_path, monkeypatch):
    log = tmp_path / "spend.log"
    records = [
        {"ts": "2026-04-15", "test_id": "t1", "model": "claude-haiku-4-5",
         "tokens_in": 1000, "tokens_out": 200, "cache_hit": False},
        {"ts": "2026-04-20", "test_id": "t1", "model": "claude-haiku-4-5",
         "tokens_in": 0, "tokens_out": 0, "cache_hit": True},  # cache hit, $0
        {"ts": "2026-04-22", "test_id": "t2", "model": "claude-haiku-4-5",
         "tokens_in": 5000, "tokens_out": 500, "cache_hit": False},
        {"ts": "2026-05-01", "test_id": "t3",  # вне месяца
         "model": "claude-haiku-4-5",
         "tokens_in": 999, "tokens_out": 999, "cache_hit": False},
    ]
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    monkeypatch.setattr(mar, "SPEND_LOG", log)

    rep = mar.aggregate("2026-04")
    assert rep["calls"] == 3
    assert rep["cache_hits"] == 1
    assert rep["cache_hit_ratio"] == pytest.approx(1 / 3)
    # cost: t1=(1000*1+200*5)/1M = 0.000002; t2=(5000+500*5)/1M = 0.0000075;
    # cache hit = 0; total ~0.0000095
    assert rep["total_usd"] == pytest.approx(
        (1000 * 1 + 200 * 5 + 5000 * 1 + 500 * 5) / 1_000_000, rel=1e-3
    )
    # top — t2 дороже t1
    assert rep["top_tests"][0]["test_id"] == "t2"


def test_format_for_tg_empty():
    rep = {"month": "2026-04", "note": "log not found",
           "total_usd": 0, "calls": 0, "cache_hit_ratio": None,
           "tokens_in": 0, "tokens_out": 0, "top_tests": []}
    text = mar.format_for_tg(rep)
    assert "пуст" in text


def test_format_for_tg_with_data(tmp_path, monkeypatch):
    log = tmp_path / "spend.log"
    records = [
        {"ts": "2026-04-01", "test_id": "test_uc_a_01_lab",
         "model": "claude-haiku-4-5",
         "tokens_in": 5000, "tokens_out": 500, "cache_hit": False},
    ]
    log.write_text(json.dumps(records[0]) + "\n")
    monkeypatch.setattr(mar, "SPEND_LOG", log)

    rep = mar.aggregate("2026-04")
    text = mar.format_for_tg(rep)
    assert "test_uc_a_01_lab" in text
    assert "$" in text


def test_previous_month():
    assert mar._previous_month("2026-05-08") == "2026-04"
    assert mar._previous_month("2026-01-15") == "2025-12"
    assert mar._previous_month("2026-12-31") == "2026-11"
