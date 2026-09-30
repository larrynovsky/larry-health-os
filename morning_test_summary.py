#!/usr/bin/env python3.11
"""
morning_test_summary.py — пишет резюме ночного test suite в agent_reports.

Вызывается из run_full_test_suite.sh после test_failure_handler.
Читает tests/reports/{today}/summary.json + parses все junit.xml,
сохраняет краткий отчёт в `agent_reports type='test_summary'`.

(до 30.09 ретайренный morning_report.py читал эту запись и добавлял
секцию «Тесты» в утренний отчёт.

CLI:
    python3.11 morning_test_summary.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import infra_config  # основная машина — данные установки (private/infra.yaml)
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from _time_inject import get_today
import test_failure_handler as tfh

SUITE_LABEL = "com.larry.health.test-suite"
"""Задача launchd, последним шагом которой этот модуль пишет итог ночи. Итог пишется
ИЗНУТРИ задачи, поэтому о смерти задачи он сообщить не может: отсутствие итога
судят читатели через `summary_covers_last_run`."""


def summary_covers_last_run(latest_date, now=None, la_dir=None):
    """Покрывает ли последний записанный итог последний плановый запуск набора.

    True/False; None — расписание задачи не выводится (не судимо — говорить вслух).
    Один дом вопроса «свежи ли данные о ночи» для двух читателей: датчика живости
    `check_nightly_suite_liveness` и датчика красной серии `night_cycle`. До 23.09
    второй читал две последние ЗАПИСИ как две последние ночи — после смерти задачи
    он бесконечно пересматривал бы старые зелёные ночи и молчал."""
    import plist_env_liveness as _pl
    from _time_inject import get_now
    return _pl.artifact_covers_last_fire(SUITE_LABEL, latest_date,
                                         now or get_now(), la_dir)


def build_summary(date_str: str) -> dict:
    """Собирает структуру: {pass, fail, expected_gap, regression, flaky, elapsed}.

    Источники:
    - tests/reports/{date}/summary.json (totals от run_full_test_suite.sh)
    - parse junit.xml через test_failure_handler.parse_junit
    - test_failure_handler.handle_test_failures (dry-run) → severity
    """
    reports_dir = SCRIPT_DIR / "tests" / "reports" / date_str
    if not reports_dir.exists():
        return {"date": date_str, "status": "no_reports",
                "message": "test suite не запускался"}

    suite_summary_path = reports_dir / "summary.json"
    suite_summary = {}
    if suite_summary_path.exists():
        try:
            suite_summary = json.loads(suite_summary_path.read_text())
        except Exception:
            pass

    # Severity classification
    # Вердикт «случайно или нет» берётся из ночного повтора, а не добывается заново (23.09):
    # иначе утро перезапускало бы упавшие тесты и могло разойтись с ночью.
    result = tfh.handle_test_failures(reports_dir, dry_run=True,
                                      rerun=tfh.cached_rerun(reports_dir))

    total = suite_summary.get("total", {})
    passed = total.get("tests", 0) - total.get("failures", 0) - total.get("errors", 0)

    return {
        "date": date_str,
        "passed": passed,
        "regression": len(result.critical),  # CRITICAL = implemented UC failed
        "expected_gap": len(result.expected_gap),
        "flaky": len(result.flaky),
        "auto_fixed": len(result.auto_fixed),
        "skipped": total.get("skipped", 0),
        "per_layer": suite_summary.get("per_layer", {}),
        "regression_ids": [f.test_id for f in result.critical[:5]],
        "regression_details": [
            {
                "test_id": f.test_id,
                "message": (f.message or "").splitlines()[0][:200] if (f.message or "") else "",
                "name": f.name,
            }
            for f in result.critical[:5]
        ],
    }


def save_to_db(summary: dict) -> None:
    """Сохранить как agent_report. На non-Studio — пропуск (read-only DB)."""
    import socket
    if not infra_config.is_primary() and not __import__("os").environ.get("ALLOW_WRITE_NONPRIMARY"):
        # MacBook без override — read-only
        return
    try:
        import health_db as db
        # Семантика test_summary:
        #   has_findings = есть regression — секция "Тесты" попадёт в morning_report
        #   data_queried = слои-источники (для self-документации)
        #   peers_reviewed = test_failure_handler видел эти результаты
        #   changes_summary = одна строка для quick-grep
        regression = summary.get("regression", 0)
        crit_count = regression
        per_layer = summary.get("per_layer", {})
        layers = sorted(per_layer.keys())
        passed = summary.get("passed", 0)
        skipped = summary.get("skipped", 0)
        if regression > 0:
            changes = f"⚠️ {regression} regression / {passed} pass / {skipped} skipped"
        else:
            changes = f"OK · {passed} pass · {skipped} skipped"
        db.save_agent_report(
            agent_type="test_summary",
            agent_name="morning_test_summary",
            date_str=summary["date"],
            has_findings=bool(crit_count > 0),
            data_queried=layers,
            pubmed_ids=[],
            peers_reviewed=["test_failure_handler"],
            changes_summary=changes,
            findings=json.dumps(summary, ensure_ascii=False),
            recommendations=None,
            data_requests=None,
            raw_output=summary,
            period_days=1,
        )
    except Exception as e:
        print(f"WARN: save_agent_report failed: {e}", file=sys.stderr)


def format_for_morning_report(summary: dict) -> str:
    """Однострочное (или 2-3 строки) резюме для секции «Тесты» в утреннем отчёте."""
    if summary.get("status") == "no_reports":
        return "Тесты: ночной запуск не выполнен."

    parts = []
    if summary.get("regression", 0) > 0:
        parts.append(f"⚠️ {summary['regression']} regression")
    if summary.get("expected_gap", 0) > 0:
        parts.append(f"{summary['expected_gap']} expected_gap")
    if summary.get("flaky", 0) > 0:
        parts.append(f"{summary['flaky']} flaky")
    parts.insert(0, f"{summary['passed']} pass")

    line = "Тесты: " + " · ".join(parts)
    details = summary.get("regression_details", [])
    if details:
        # Для каждой регрессии — короткое имя теста + первая строка assertion.
        # Если message пустой — fallback на test_id (как раньше).
        for d in details[:3]:
            name = d.get("name", "?")
            msg = d.get("message", "")
            if msg:
                line += f"\n  • {name}: {msg[:140]}"
            else:
                line += f"\n  • {name}"
    elif summary.get("regression_ids"):
        # Fallback на старый формат если нет details (старые summary.json)
        line += f"\n  Regression: {', '.join(summary['regression_ids'][:3])}"
    return line


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=None)
    args = p.parse_args()

    date_str = args.date or str(get_today())
    summary = build_summary(date_str)
    save_to_db(summary)
    print(format_for_morning_report(summary))
    print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
