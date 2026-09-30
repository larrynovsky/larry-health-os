#!/usr/bin/env python3.11
"""
Регрессия-чек (commit e787729): integrity_tests.py --json ОБЯЗАН отдавать
валидный JSON первой строкой.

Баг, который это ловит: незащищённый `print("[10] Структура документации")`
(без `if not JSON_OUTPUT`) печатал не-JSON строку перед JSON → run_checks.sh
(json.load → при ошибке FAIL=0) и triage_agent (json.load падал) молча
глохли. 33 проверки гонялись впустую — тихий сбой всего дневного мониторинга.

Контракт проверяется в ТОЧКЕ ПОТРЕБЛЕНИЯ: то, что парсят run_checks/triage.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.slow  # запускает integrity_tests.py --json (~30-120 сек на Studio)
def test_integrity_json_is_parseable():
    r = subprocess.run(
        [sys.executable, str(ROOT / "integrity_tests.py"), "--json"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    # Не-Studio окружение без health.db — не наш кейс, пропускаем.
    if r.returncode not in (0, 1, 2) or (
        "HEALTH_DATA_DIR" in r.stderr or "health.db" in r.stderr
    ):
        import pytest
        pytest.skip(f"нет доступа к health.db (env? rc={r.returncode})")
    out = r.stdout.lstrip()
    assert out.startswith("{"), (
        f"--json должен начинаться с JSON-объекта, а не с текста: {r.stdout[:80]!r}"
    )
    data = json.loads(r.stdout)  # падение здесь = регрессия e787729
    assert {"pass", "fail", "warn"} <= set(data), f"нет ключей счётчиков: {list(data)[:8]}"


if __name__ == "__main__":
    test_integrity_json_is_parseable()
    print("TEST PASS")
