"""tests/unit/test_memory_truthcheck.py — A1 (2026-07-06): парсер сверки памяти с каноном.

Независимо придуманные фразы проверяют парсер: readiness не должен
дублироваться как sleep score, часы без контекста сна не являются сном.
"""
from __future__ import annotations
import pytest

pytestmark = pytest.mark.unit

import memory_truthcheck as mt


def test_readiness_not_double_counted_as_score():
    """Синтетический readiness score извлекается только в свой ключ."""
    c = mt._extract_claims("Readiness score 83 on 2040-04-09")
    assert c.get("readiness") == 83
    assert "score" not in c, "readiness score утёк в sleep score (регресс v1)"


def test_explicit_sleep_score_and_deep():
    c = mt._extract_claims("Sleep score 64 with deep sleep 55 minutes")
    assert c["score"] == 64
    assert c["deep_min"] == 55


def test_hours_without_sleep_context_not_a_sleep_claim():
    """Часы без контекста сна не создают утверждение о длительности сна."""
    c = mt._extract_claims("2040-04-09: Workshop starts within 4 hours")
    assert "total_h" not in c, "часы без контекста сна не должны стать claim о сне"
    c2 = mt._extract_claims("учебная встреча через 5 часов, 2040-04-09")
    assert "total_h" not in c2


def test_hours_with_sleep_context_still_a_claim():
    """Не сломали реальные утверждения о сне — контекст сна есть → total_h извлекается."""
    assert mt._extract_claims("спал 7.5 часов, выспался")["total_h"] == 7.5
    assert mt._extract_claims("slept 8 hours last night")["total_h"] == 8.0


def test_hrv_and_total_hours():
    c = mt._extract_claims("HRV 48 ms, спал 6.8 часа")
    assert c["hrv"] == 48
    assert c["total_h"] == 6.8


def test_hours_before_sleep_not_taken_as_duration():
    """'за 2 часа до сна' — не длительность сна."""
    c = mt._extract_claims("Планирует выключить экраны за 2 часа до сна")
    assert "total_h" not in c


def test_dates_iso_and_ru_month():
    ds = mt._extract_dates("2026-02-11 и 14 февраля")
    assert (2, 11, 2026) in ds
    assert (2, 14, None) in ds


def test_no_metric_no_date_is_unparseable():
    """Утверждение без числа+даты не даёт ложных claims."""
    assert mt._extract_claims("User prefers flexible sleep schedule") == {}
