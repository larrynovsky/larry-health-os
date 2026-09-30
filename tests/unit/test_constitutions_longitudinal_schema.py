"""W5K #175: regression test — _get_longitudinal_context читает данные
из agent_reports.findings.

История: 2026-05-14 generate_constitutions молча скипал все 5 доменов
с "⛔ Нет longitudinal данных" потому что искал ключи clean_date_ranges/
phase_stats/recovery_delta — а реальная схема (после рефактора longitudinal_analysis)
использует phases/yearly_trend/top_correlations/lab_metric_correlations/
recovery_vs_baseline.

Этот тест защищает от очередной смены схемы: если longitudinal_analysis
поменяет поле или generate_constitutions перестанет читать findings —
тест сломается до prod-деплоя.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

pytestmark = pytest.mark.unit


# Независимо придуманный профиль; числа не взяты из отчёта.
_MIN_LONGITUDINAL = {
    "generated_at": "2032-06-02 13:20:00",
    "data_range": {"start": "2025", "end": "2032", "total_years": 8},
    "phases": [
        {"phase": "Fixture baseline", "type": "baseline",
         "start": "2025-01-01", "end": "2027-12-31", "n": 1095,
         "hrv": 52.0, "resting_hr": 64.0, "sleep_total": 6.8},
        {"phase": "Fixture phase B", "type": "treatment",
         "start": "2028-02-01", "end": "2028-04-30", "n": 90, "hrv": 46.0},
    ],
    "yearly_trend": [
        {"year": 2030, "hrv": 48.0, "sleep_total": 6.6, "steps": 6200.0},
        {"year": 2031, "hrv": 51.0, "sleep_total": 6.9, "steps": 6700.0},
    ],
    "top_correlations": [
        {"a": "steps", "b": "active_kcal", "r": 0.76, "p": 0.002},
        {"a": "sleep_deep", "b": "hrv", "r": 0.53, "p": 0.004},
    ],
    "lab_metric_correlations": [
        {"lab": "Fixture_Lab_Q", "metric": "steps", "r": -0.52, "p": 0.003},
        {"lab": "Fixture_Lab_R", "metric": "hrv", "r": 0.64, "p": 0.002},
    ],
    "recovery_vs_baseline": {
        "hrv": {"baseline": 52.0, "current": 39.0, "pct": 75.0, "trend": "↑"},
        "sleep_deep": {"baseline": 1.2, "current": 1.5, "pct": 125.0, "trend": "↓"},
    },
    # С 2026-07-26 вера без применённого гейта читателем НЕ принимается (belief_contract).
    "gate": {"gate_applied": True, "status": "applied"},
}


def test_longitudinal_context_reads_findings(db):
    """generate_constitutions._get_longitudinal_context должен находить и
    форматировать данные из agent_reports.findings."""
    today = str(date.today())
    with db.conn() as c:
        c.execute(
            """INSERT INTO agent_reports
               (date, agent_type, agent_name, findings, created_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (today, "longitudinal_analysis", "longitudinal_analysis",
             json.dumps(_MIN_LONGITUDINAL, ensure_ascii=False)),
        )

    from generate_constitutions import _get_longitudinal_context

    ctx = _get_longitudinal_context()
    assert len(ctx) > 200, f"контекст слишком короткий: {len(ctx)} chars"

    # Структурные маркеры — каждый раздел должен присутствовать
    assert "Период данных" in ctx, "data_range секция не отрендерилась"
    assert "Клинические фазы" in ctx, "phases секция не отрендерилась"
    assert "Fixture baseline" in ctx, "конкретная фаза не выведена"
    # Горизонт (решение владельца 25.09): «current» восстановления — последние 90 дней,
    # оперативное окно; в конституцию НЕ подаётся (было: обязан выводиться).
    assert "Восстановление vs pre-illness baseline" not in ctx, "90-дневное окно просочилось"
    assert "Годовой тренд" in ctx, "yearly_trend не выведен"
    assert "Сильные корреляции" in ctx, "top_correlations не выведены"
    assert "Лаб ↔ метрики" in ctx, "lab_metric_correlations не выведены"


def test_longitudinal_context_refuses_ungated_belief(db):
    """Мутация схемы (2026-07-26): та же вера без `gate` → конституция ОТКАЗЫВАЕТСЯ её подавать
    и говорит об этом вслух. Негативный контроль к тесту выше: «раздел собрался» само по себе
    ничего не доказывает, если он собирается и на негейтованном сырье (P1-01)."""
    import copy
    today = str(date.today())
    _ungated = copy.deepcopy(_MIN_LONGITUDINAL)
    _ungated.pop("gate")
    with db.conn() as c:
        c.execute(
            """INSERT INTO agent_reports
               (date, agent_type, agent_name, findings, created_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (today, "longitudinal_analysis", "longitudinal_analysis",
             json.dumps(_ungated, ensure_ascii=False)),
        )

    from generate_constitutions import _get_longitudinal_context
    ctx = _get_longitudinal_context()
    assert "НЕ ПОДАЮТСЯ" in ctx, "отказ обязан быть видимым в конституции"
    assert "Сильные корреляции" not in ctx, "негейтованные пары не должны рендериться"
    assert "active_kcal" not in ctx


def test_longitudinal_context_returns_empty_on_missing_data(db):
    """Если в agent_reports нет longitudinal_analysis — пустая строка, не crash."""
    from generate_constitutions import _get_longitudinal_context
    ctx = _get_longitudinal_context()
    assert ctx == "", f"ожидали '' при отсутствии данных, получили: {ctx[:100]!r}"


def test_longitudinal_context_handles_malformed_json(db):
    """Если findings содержит невалидный JSON — пустая строка, не crash."""
    today = str(date.today())
    with db.conn() as c:
        c.execute(
            """INSERT INTO agent_reports
               (date, agent_type, agent_name, findings, created_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (today, "longitudinal_analysis", "longitudinal_analysis",
             "{ invalid json garbage"),
        )

    from generate_constitutions import _get_longitudinal_context
    ctx = _get_longitudinal_context()
    assert ctx == "", "malformed JSON должен возвращать ''"
