"""
Wave 4-CORRELATIONS C-10 — cadence staleness watchdog (check_longitudinal_freshness).

Архитектурный момент: integrity_tests читает из health_db.get_conn() (production
DB_PATH), не из in-memory fixture `db`. Поэтому мокаем get_conn напрямую через
context manager, не через db fixture.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.integration


def _fake_conn(last_date: str | None):
    """Возвращает mock-объект конекшна, который возвращает заданную last_date."""
    fake_row = {"last_date": last_date} if last_date is not None else {"last_date": None}
    conn = MagicMock()
    # conn.__enter__ возвращает себя; execute().fetchone() возвращает fake_row.
    conn.__enter__ = lambda self: self
    conn.__exit__ = lambda self, *args: False
    conn.execute = MagicMock(return_value=MagicMock(fetchone=MagicMock(return_value=fake_row)))
    return conn


def test_stale_run_triggers_warn(monkeypatch):
    """9 дней назад > порог 8 → WARN."""
    import sys
    sys.path.insert(0, str(Path(__file__).parents[2]))
    import integrity_tests

    today = date(2026, 5, 12)
    monkeypatch.setattr(integrity_tests, "today", today)
    monkeypatch.setattr(integrity_tests.db, "get_conn",
                        lambda: _fake_conn("2026-05-03"))  # 9 дней назад

    warnings: list[str] = []
    monkeypatch.setattr(integrity_tests, "warn",
                        lambda label, detail="": warnings.append(f"{label}: {detail}"))

    age = integrity_tests.check_longitudinal_freshness()
    assert age == 9, f"Ожидался age=9, получили {age}"
    assert any("longitudinal_analysis устарел" in w for w in warnings), \
        f"WARN не пойман. Warnings: {warnings}"


def test_fresh_run_no_warn(monkeypatch):
    """След покрывает последний плановый запуск (вс 10.05 03:00, плист из conftest) → тишина.

    До 23.09 здесь стояло «6 дней < порог 8» со следом от СРЕДЫ 06.05 — по расписанию это
    пропущенное воскресенье 10.05, и литерал его молча прощал (см. test_missed_sunday)."""
    import sys
    sys.path.insert(0, str(Path(__file__).parents[2]))
    import integrity_tests

    today = date(2026, 5, 12)
    monkeypatch.setattr(integrity_tests, "today", today)
    monkeypatch.setattr(integrity_tests.db, "get_conn",
                        lambda: _fake_conn("2026-05-10"))  # воскресный запуск, 2 дня назад

    warnings: list[str] = []
    monkeypatch.setattr(integrity_tests, "warn",
                        lambda label, detail="": warnings.append(f"{label}: {detail}"))

    age = integrity_tests.check_longitudinal_freshness()
    assert age == 2
    assert not warnings, f"След покрывает запуск — тишина. {warnings}"


def test_missed_sunday_warns_before_old_threshold(monkeypatch):
    """6 дней — литерал «>8» молчал бы ещё два дня; по расписанию воскресенье пропущено."""
    import sys
    sys.path.insert(0, str(Path(__file__).parents[2]))
    import integrity_tests

    monkeypatch.setattr(integrity_tests, "today", date(2026, 5, 12))
    monkeypatch.setattr(integrity_tests.db, "get_conn",
                        lambda: _fake_conn("2026-05-06"))  # среда, 6 дней назад
    warnings: list[str] = []
    monkeypatch.setattr(integrity_tests, "warn",
                        lambda label, detail="": warnings.append(f"{label}: {detail}"))
    assert integrity_tests.check_longitudinal_freshness() == 6
    assert any("longitudinal_analysis устарел" in w and "10.05 03:00" in w for w in warnings), warnings


def test_no_runs_at_all_triggers_different_warn(monkeypatch):
    """Пустая БД — WARN 'ни разу не запускался'."""
    import sys
    sys.path.insert(0, str(Path(__file__).parents[2]))
    import integrity_tests

    today = date(2026, 5, 12)
    monkeypatch.setattr(integrity_tests, "today", today)
    monkeypatch.setattr(integrity_tests.db, "get_conn", lambda: _fake_conn(None))

    warnings: list[str] = []
    monkeypatch.setattr(integrity_tests, "warn",
                        lambda label, detail="": warnings.append(f"{label}: {detail}"))

    age = integrity_tests.check_longitudinal_freshness()
    assert age is None
    assert any("ни разу не запускался" in w for w in warnings), \
        f"WARN 'ни разу не запускался' не пойман. {warnings}"
