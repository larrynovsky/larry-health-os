"""Консилиум получает корреляции ТОЛЬКО из принятой веры (UC-B-09, 2026-09-03).

Второй производитель коэффициента мимо validation_gate может отправить в
консилиум отвергнутую пару. Независимо придуманный ответ детектора ниже
должен быть исключён из input; принятая вера доставляется без изменений.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest


def _build(belief_lines, drift):
    with patch("health_db.init_db"), \
         patch("health_db.get_conn") as mock_conn, \
         patch("hai_core._build_patient_profile", return_value="[profile]"), \
         patch("gp_agent._build_clinical_history", return_value="[history]"), \
         patch("hai_analysis.detect_metric_drift", return_value=[]), \
         patch("hai_analysis.detect_correlation_drift", return_value=drift), \
         patch("gp_context._build_longitudinal_correlations_block", return_value=belief_lines), \
         patch("health_db.get_recent_labs", return_value=[]), \
         patch("genome_context.build_genetic_context_block", return_value=""):
        cur = MagicMock()
        cur.fetchone.return_value = None
        cur.fetchall.return_value = []
        mock_conn.return_value.__enter__.return_value.execute.return_value = cur
        import monthly_consilium as mc
        return mc._build_consilium_input(date(2032, 4, 12), 30)


_DRIFT = [{"metric_a": "fixture_q", "metric_b": "fixture_r", "r_baseline": 0.68,
           "r_recent": 0.51, "delta_pct": -25.0, "severity": "mild", "n_pairs": 90}]


def test_drift_r_never_reaches_consilium_input():
    """Негативный контроль: детектор дрейфа отдаёт r=0.68 — в input его нет."""
    pkg = _build([], _DRIFT)
    assert "r_baseline=" not in pkg
    assert "0.68" not in pkg
    assert "ИЗМЕНЕНИЯ КОРРЕЛЯЦИЙ" not in pkg


def test_empty_belief_is_stated_not_silent():
    """Пустая принятая вера подаётся явной строкой-запретом, а не тишиной."""
    pkg = _build([], _DRIFT)
    assert "Принятых статистическим гейтом корреляций нет" in pkg
    assert "ДОЛГОСРОЧНЫЕ КОРРЕЛЯЦИИ" in pkg


def test_accepted_belief_lines_are_forwarded_verbatim():
    """Строки читателя веры едут как есть — один рендерер на GP и консилиум."""
    lines = ["", "ДОЛГОСРОЧНЫЕ КОРРЕЛЯЦИИ (4 года, обновлено 2032-03-08):",
             "  Между метриками:", "    fixture_q ↔ fixture_r: r=+0.34 — рычаг"]
    pkg = _build(lines, _DRIFT)
    assert "fixture_q ↔ fixture_r: r=+0.34" in pkg
    assert "Принятых статистическим гейтом корреляций нет" not in pkg


def test_source_has_no_drift_call():
    """Сторож формы поверх поведения: сам вызов детектора дрейфа из консилиума убран."""
    from pathlib import Path
    import monthly_consilium
    src = Path(monthly_consilium.__file__).read_text(encoding="utf-8")
    body = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert "detect_correlation_drift(" not in body
