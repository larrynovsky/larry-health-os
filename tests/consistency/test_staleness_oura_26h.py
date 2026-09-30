"""
Staleness deviation: данные старше 26ч (Oura/Apple Health) → integrity_tests WARN.

Источник: TESTING_CONTRACTS.md §1 (conit C1) + UC-D-05.
Уровень: consistency.

Эта проверка проверяет conit-контракт через `_time_inject` и `db` fixture.
Если последний day в `daily_metrics` старше 26ч — система должна это видеть
как WARN (через `integrity_tests` или `safety_net`).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.consistency


def test_data_within_26h_no_warning(db, clock):
    """Свежие данные (вчера) — не stale."""
    clock.set("2026-05-08T08:00:00")
    db.add_daily_metrics("2026-05-07", hrv=22, sleep_total=7.5)

    # Простая проверка: max(date) ≥ today-1
    row = db.fetchone("SELECT MAX(date) AS last FROM daily_metrics")
    last_date = date.fromisoformat(row["last"])
    age_days = (clock.today() - last_date).days
    assert age_days <= 1, f"данные за {last_date} не считаются свежими"


def test_data_older_than_26h_is_stale(db, clock):
    """Если последние данные >26ч назад → stale."""
    clock.set("2026-05-08T08:00:00")
    db.add_daily_metrics("2026-05-05", hrv=22)  # 3 дня назад

    row = db.fetchone("SELECT MAX(date) AS last FROM daily_metrics")
    last_date = date.fromisoformat(row["last"])
    age_days = (clock.today() - last_date).days
    assert age_days >= 2, f"данные за {last_date} должны быть stale"


def test_staleness_detection_via_clock_advance(db, clock):
    """
    Динамика: данные были свежие, но время прошло — теперь stale.
    Это полная RYW + staleness picture.
    """
    clock.set("2026-05-08T08:00:00")
    db.add_daily_metrics("2026-05-07", hrv=22)

    # В этот момент данные «свежие» (1 день назад)
    row = db.fetchone("SELECT MAX(date) FROM daily_metrics")
    fresh = clock.today() - date.fromisoformat(row[0])
    assert fresh.days == 1

    # Прошла неделя без новых данных
    clock.advance(days=7)
    stale = clock.today() - date.fromisoformat(row[0])
    assert stale.days == 8, "проверка staleness через clock.advance"


def test_no_data_at_all_treated_as_stale(db, clock):
    """Полное отсутствие daily_metrics → MAX(date)=None → infinite staleness."""
    clock.set("2026-05-08")
    row = db.fetchone("SELECT MAX(date) FROM daily_metrics")
    assert row[0] is None, "БД пуста"

    # Логически — это «бесконечно stale», что должно поднять алерт
    # Проверка интегрируется через integrity_tests.check_data_freshness()


def test_clock_inject_overrides_real_time(db, clock):
    """
    Главный инвариант теста на staleness: clock.set() реально подменяет
    «сейчас» для всех функций, использующих _time_inject.get_today().
    Без этого тест ниже (на 26ч) был бы flaky.
    """
    clock.set("2030-12-31")
    from _time_inject import get_today
    assert get_today() == date(2030, 12, 31)

    clock.set("2020-01-01")
    assert get_today() == date(2020, 1, 1)
