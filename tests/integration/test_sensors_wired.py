#!/usr/bin/env python3.11
"""
Датчики liveness должны быть ВРЕЗАНЫ в свои периодические прогоны.

Анти-«детект без доставки» (2026-06-19): если кто-то уберёт вызов датчика,
он станет dormant молча — как было с пустой system_config.model.* и
дрейфанувшим WellAlly SPEC_DIR. Этот статический check ловит расврезку.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_model_health_check_wired_in_monthly_report():
    src = (ROOT / "monthly_api_report.py").read_text(encoding="utf-8")
    assert "model_health_check" in src and "run_check" in src, \
        "model_health_check расврезан из monthly_api_report.py"


def test_oura_freshness_check_wired_in_run_checks():
    src = (ROOT / "run_checks.sh").read_text(encoding="utf-8")
    assert "oura_freshness_check.py" in src, \
        "oura_freshness_check расврезан из run_checks.sh --scheduled"


def test_daemon_liveness_wired_in_integrity():
    src = (ROOT / "integrity_tests.py").read_text(encoding="utf-8")
    assert "check_daemons_alive" in src and "daemon_liveness" in src, \
        "датчик liveness демонов расврезан из integrity_tests.py"


def test_arch_guard_failure_alerts_in_run_checks():
    src = (ROOT / "run_checks.sh").read_text(encoding="utf-8")
    assert 'record_fault "arch_guard failed"' in src, \
        "arch_guard else-алерт расврезан из run_checks.sh (падение снова тонет в логе)"


def test_triage_uses_notify_fallback():
    """triage-доставка должна идти через notify (Telegram→резерв), не голым curl."""
    src = (ROOT / "triage_agent.py").read_text(encoding="utf-8")
    assert "notify.notify" in src, "triage не использует notify — Telegram-SPOF вернулся"


def test_db_row_regression_wired_in_integrity():
    src = (ROOT / "integrity_tests.py").read_text(encoding="utf-8")
    assert "check_db_row_regression" in src and "db_regression" in src, \
        "датчик регрессии строк расврезан из integrity_tests.py"


def test_trend_alerts_wired_in_recommendations():
    src = (ROOT / "services" / "recommendations.py").read_text(encoding="utf-8")
    assert "trend_alerts" in src and "evaluate_trends" in src, \
        "трендовый слой расврезан из evaluate_domain_need (BL-ALERT-TREND-1)"


def test_proposal_expiry_wired_in_pipeline():
    src = (ROOT / "scripts" / "run_survivorship_pipeline.sh").read_text(encoding="utf-8")
    assert "expire_aged_proposals" in src, \
        "авто-expiry proposals расврезан из survivorship-пайплайна (lifecycle)"


def test_ecg_nonsinus_wired_in_integrity():
    src = (ROOT / "integrity_tests.py").read_text(encoding="utf-8")
    assert "check_ecg_nonsinus" in src and "ecg_db" in src, \
        "кардио-алерт ЭКГ расврезан из integrity_tests.py (AFib/High HR не доставляется)"


def test_ecg_block_wired_in_gp_context():
    src = (ROOT / "gp_context.py").read_text(encoding="utf-8")
    assert "_build_ecg_block" in src, \
        "блок ЭКГ расврезан из gp_context (синусовые записи стали мёртвыми данными)"


def test_plist_env_consistency_wired_in_integrity():
    src = (ROOT / "integrity_tests.py").read_text(encoding="utf-8")
    assert "check_plist_env_consistency" in src and "plist_env_liveness" in src, \
        "датчик env-консистентности плистов расврезан из integrity_tests.py (BL-MULTITENANT-1)"


if __name__ == "__main__":
    test_model_health_check_wired_in_monthly_report()
    test_oura_freshness_check_wired_in_run_checks()
    print("TEST PASS")
