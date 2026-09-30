"""
Wave 4-CORRELATIONS C-9 — integration test для GP-context с корреляциями.

Проверяет:
- Блок «ДОЛГОСРОЧНЫЕ КОРРЕЛЯЦИИ» присутствует в _build_gp_context.
- UC-D-05 маркер даты («обновлено YYYY-MM-DD») есть.
- Топ-5 корреляций отрендерены.
- Graceful skip если в БД нет longitudinal_analysis отчёта.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_gp_context_includes_longitudinal_block_when_data_exists(db):
    """Если есть свежий agent_reports.longitudinal_analysis — блок появляется в gp_context."""
    import sys
    sys.path.insert(0, str(Path(__file__).parents[2]))
    import gp_agent
    import health_db

    # Сохраняем mock longitudinal-отчёт в agent_reports.
    findings_payload = json.dumps({
        "generated_at": "2026-05-12 03:00:00",
        "top_correlations": [
            {"a": "hrv", "b": "creatinine", "r": 0.83, "p": 0.001},
            {"a": "mcv", "b": "steps", "r": -0.74, "p": 0.002},
            {"a": "sleep_deep", "b": "readiness", "r": 0.65, "p": 0.01},
        ],
        "lab_metric_correlations": [
            {"lab": "Cholesterol", "metric": "readiness", "r": -0.88, "p": 0.01},
        ],
        # С 2026-07-26 блок принимает только гейтованную веру (belief_contract, P1-01).
        "gate": {"gate_applied": True, "status": "applied"},
    })
    health_db.save_agent_report(
        agent_type="longitudinal_analysis",
        agent_name="LongitudinalAnalyst",
        date_str="2026-05-12",
        has_findings=0,
        data_queried=[],
        pubmed_ids=[],
        peers_reviewed=[],
        changes_summary="",
        findings=findings_payload,
        recommendations=None,
        raw_output=None,
        period_days=3650,
    )

    ctx = gp_agent._build_gp_context(date(2026, 5, 12), period_days=7)

    assert "ДОЛГОСРОЧНЫЕ КОРРЕЛЯЦИИ" in ctx, \
        "Блок 'ДОЛГОСРОЧНЫЕ КОРРЕЛЯЦИИ' отсутствует в gp_context"
    # UC-D-05 маркер даты
    assert "обновлено 2026-05-12" in ctx, \
        "UC-D-05 маркер даты («обновлено YYYY-MM-DD») отсутствует"
    # Корреляции отрендерены
    assert "hrv ↔ creatinine" in ctx, "Топ-корреляция не отрендерена"
    assert "r=+0.83" in ctx, "Значение r не отрендерено"


def test_gp_context_skips_longitudinal_block_when_no_data(db):
    """Если в БД нет longitudinal_analysis — блок просто отсутствует, не падает."""
    import sys
    sys.path.insert(0, str(Path(__file__).parents[2]))
    import gp_agent

    # БД пустая (db fixture), не сохраняем longitudinal_analysis.
    ctx = gp_agent._build_gp_context(date(2026, 5, 12), period_days=7)
    assert "ДОЛГОСРОЧНЫЕ КОРРЕЛЯЦИИ" not in ctx, \
        "Без данных longitudinal_analysis блок не должен появляться"


def test_gp_context_longitudinal_block_has_uc_d_05_marker(db):
    """UC-D-05: дата в шапке блока всегда явная (для distinguishing stale data)."""
    import sys
    sys.path.insert(0, str(Path(__file__).parents[2]))
    import gp_agent
    import health_db
    import re

    health_db.save_agent_report(
        agent_type="longitudinal_analysis",
        agent_name="LongitudinalAnalyst",
        date_str="2025-12-25",  # старая дата
        has_findings=0,
        data_queried=[],
        pubmed_ids=[],
        peers_reviewed=[],
        changes_summary="",
        findings=json.dumps({
            "top_correlations": [{"a": "x", "b": "y", "r": 0.7}],
            "gate": {"gate_applied": True, "status": "applied"},
        }),
        recommendations=None,
        raw_output=None,
        period_days=3650,
    )

    ctx = gp_agent._build_gp_context(date(2026, 5, 12), period_days=7)
    # Регулярка: должна быть «обновлено YYYY-MM-DD»
    match = re.search(r"обновлено (\d{4}-\d{2}-\d{2})", ctx)
    assert match is not None, \
        f"UC-D-05 marker регулярка не нашла «обновлено YYYY-MM-DD» в gp_context"
    # Дата должна совпадать со сохранённой (UC-D-05: explicit stale date)
    assert match.group(1) == "2025-12-25", \
        f"UC-D-05: маркер должен быть исходной даты ({match.group(1)})"
