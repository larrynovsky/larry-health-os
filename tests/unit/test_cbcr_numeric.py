"""W5A-D2: tests для _check_unsupported_numerics + observation whitelist."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import pytest
import cbcr_hypothesis as ch

pytestmark = pytest.mark.unit


@pytest.fixture
def obs():
    return {
        "trigger": "drift",
        "summary": "Глубокий сон −18.9% за 90 дней, streak 14 дней",
        "details": {"current_7d": 38.2, "baseline_30d": 47.1, "delta_pct": -18.9, "streak_days": 14},
    }


def test_number_from_observation_not_flagged(obs):
    """'47.1 мин' из baseline_30d → не flagged."""
    hyp = {"evidence_for": [
        {"source": "oura", "fact": "deep_sleep current 38.2 мин vs 47.1 мин baseline", "weight": "strong"},
    ]}
    issues = ch._check_unsupported_numerics(hyp, obs)
    assert issues == []


def test_phantom_number_alone_not_flagged(obs):
    """1-2 unsupported числа → soft warning, не critical (return пустой)."""
    hyp = {"evidence_for": [
        {"source": "literature", "fact": "Exampliplatin персистирует 6-18 мес после REG-A",
         "weight": "supporting"},
    ]}
    # 1 unsupported недостаточно для regenerate (порог 3)
    assert ch._check_unsupported_numerics(hyp, obs) == []


def test_three_phantom_numbers_flagged(obs):
    """≥3 unsupported → critical_issues."""
    hyp = {"evidence_for": [
        {"source": "literature", "fact": "Эффект 6 мес после терапии", "weight": "supporting"},
        {"source": "literature", "fact": "Контроль 12 недель показывает 25%", "weight": "supporting"},
        {"source": "literature", "fact": "TSH < 4.5 мМЕ/л норма", "weight": "moderate"},
    ]}
    issues = ch._check_unsupported_numerics(hyp, obs)
    assert len(issues) >= 3


def test_pmid_in_fact_whitelist(obs):
    """Реальный PMID в fact → число с любыми единицами allowed."""
    hyp = {"evidence_for": [
        {"source": "literature", "fact": "Эффект 6 мес после терапии (PMID:12345678)", "weight": "moderate"},
        {"source": "literature", "fact": "Контроль 12 недель — 25% ответ PMID 99999999", "weight": "moderate"},
        {"source": "literature", "fact": "TSH < 4.5 мМЕ/л (PMID:11111111)", "weight": "moderate"},
    ]}
    assert ch._check_unsupported_numerics(hyp, obs) == []


def test_no_observation_passes_through():
    """Без observation whitelist невозможен — возвращает [] (мягкое поведение)."""
    hyp = {"evidence_for": [
        {"source": "literature", "fact": "6-18 мес 25% 30 дней", "weight": "moderate"},
    ]}
    assert ch._check_unsupported_numerics(hyp, None) == []
    assert ch._check_unsupported_numerics(hyp, {}) == []


def test_number_from_summary_whitelisted():
    """Число в observation.summary тоже whitelist."""
    obs = {"summary": "Streak 14 дней ниже порога", "details": {}}
    hyp = {"evidence_for": [
        {"source": "oura", "fact": "Streak 14 дней — не флуктуация", "weight": "strong"},
        {"source": "oura", "fact": "Стабильно 14 дней подряд", "weight": "strong"},
        {"source": "oura", "fact": "Тренд устойчив 14 дней", "weight": "moderate"},
    ]}
    issues = ch._check_unsupported_numerics(hyp, obs)
    assert issues == [], f"14 from summary should be whitelist; got {issues}"


def test_range_number_partially_supported():
    """'6-18 мес' — обе границы проверяются, одна не в observation → flagged."""
    obs = {"summary": "Тренд за 6 месяцев", "details": {}}
    hyp = {"evidence_for": [
        {"source": "literature", "fact": "Эффект 6-18 мес персистирует", "weight": "moderate"},
        {"source": "literature", "fact": "Восстановление 6-12 мес", "weight": "moderate"},
        {"source": "literature", "fact": "Хронология 6-9 мес", "weight": "moderate"},
    ]}
    issues = ch._check_unsupported_numerics(hyp, obs)
    # 6 в observation, 18/12/9 нет → каждая запись flagged
    assert len(issues) >= 3
