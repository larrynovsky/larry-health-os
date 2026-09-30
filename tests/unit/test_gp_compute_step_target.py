"""
Характеризация gp_agent.compute_step_target + generate_attribution_report(not-found).

compute_step_target — детерминированная логика цели по шагам: readiness-уровни,
HRV-модификаторы, критический отдых, 2 стрессовых дня. LLM не задействован.
Пинит ПОВЕДЕНИЕ КАК ЕСТЬ перед выносом gp_context (Поток C). 0 тестов до этого.

Стратегия: подменяем три обращения к БД (get_day / get_stats / get_conn)
контролируемыми значениями — изолируем чистую логику от схемы daily_metrics.
"""
from __future__ import annotations

from datetime import date

import pytest

pytestmark = pytest.mark.unit

import health_db
import gp_agent

DAY0 = date(2026, 6, 1)


def _patch_db(monkeypatch, *, readiness, hrv_today=None, hrv_30avg=0, stress=()):
    """Контролируемые get_day / get_stats / get_conn для одной ветки."""
    day: dict = {}
    if readiness is not None:
        day["readiness_score"] = readiness
    if hrv_today is not None:
        day["hrv"] = {"avg": hrv_today}

    monkeypatch.setattr(health_db, "get_day", lambda d: day)
    monkeypatch.setattr(health_db, "get_stats", lambda n, d: {"avg_hrv": hrv_30avg})

    class _Cur:
        def fetchall(self):
            return [{"stress_summary": s} for s in stress]

    class _Conn:
        def execute(self, *a, **k):
            return _Cur()

        def close(self):
            pass

    monkeypatch.setattr(health_db, "get_conn", lambda: _Conn())


# ── Базовые уровни readiness ──────────────────────────────────────────────────

def test_readiness_none_returns_feel_based(monkeypatch):
    _patch_db(monkeypatch, readiness=None)
    assert "данных readiness нет" in gp_agent.compute_step_target(DAY0)


def test_recovery_day_below_60(monkeypatch):
    _patch_db(monkeypatch, readiness=50)
    out = gp_agent.compute_step_target(DAY0)
    assert "Восстановительный день" in out and "3,000" in out and "5,000" in out


def test_moderate_day_60_to_75(monkeypatch):
    _patch_db(monkeypatch, readiness=70)
    out = gp_agent.compute_step_target(DAY0)
    assert "Умеренный день" in out and "6,000" in out and "8,000" in out


def test_active_day_75_to_85(monkeypatch):
    _patch_db(monkeypatch, readiness=80)
    out = gp_agent.compute_step_target(DAY0)
    assert "Активный день" in out and "8,000" in out and "10,000" in out


def test_peak_day_85_plus(monkeypatch):
    _patch_db(monkeypatch, readiness=90)
    out = gp_agent.compute_step_target(DAY0)
    assert "Пиковый день" in out and "10,000" in out and "12,000" in out


# ── Модификаторы ──────────────────────────────────────────────────────────────

def test_low_hrv_applies_minus_25pct(monkeypatch):
    # hrv_today 40 < 85% от 30-дн среднего 60 (=51) → −25%: 8000→6000, 10000→7500
    _patch_db(monkeypatch, readiness=80, hrv_today=40, hrv_30avg=60)
    out = gp_agent.compute_step_target(DAY0)
    assert "6,000" in out and "7,500" in out and "25%" in out


def test_critical_hrv_below_15_forces_rest(monkeypatch):
    # hrv_today 12 < 15 → жёсткий override 2000–4000, метка критического отдыха
    _patch_db(monkeypatch, readiness=90, hrv_today=12, hrv_30avg=60)
    out = gp_agent.compute_step_target(DAY0)
    assert "Критический отдых" in out and "2,000" in out and "4,000" in out


def test_two_consecutive_stress_days_minus_30pct(monkeypatch):
    # 2 стрессовых дня → −30%: active 8000→5600, 10000→7000
    _patch_db(monkeypatch, readiness=80, stress=("stressful", "stressful"))
    out = gp_agent.compute_step_target(DAY0)
    assert "5,600" in out and "7,000" in out and "30%" in out


# ── generate_attribution_report: ветка «эксперимент не найден» (без LLM) ───────

def test_attribution_experiment_not_found(monkeypatch):
    """BL-GP-1 ПОЧИНЕН (2026-07-02): добавлен import health_db as db в
    generate_attribution_report — раньше NameError на первой строке → тихий отказ
    (attribution-отчёты никогда не генерировались, т.к. зовётся из
    run_experiment_checks внутри try/except)."""
    monkeypatch.setattr(health_db, "get_experiment_stats", lambda eid: None)
    out = gp_agent.generate_attribution_report(99999)
    assert "не найден" in out
