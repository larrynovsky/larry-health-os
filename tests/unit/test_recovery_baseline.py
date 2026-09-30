"""Индекс восстановления сравнивает с baseline ТЕНАНТА, а не с числами одного человека (BL-PUB-16 а, 27.09)."""
from datetime import timedelta


def _fill(db, start, days, hrv):
    from _time_inject import get_today  # noqa: F401
    with db.conn() as c:
        for i in range(days):
            d = str(start + timedelta(days=i))
            c.execute("INSERT OR REPLACE INTO daily_metrics (date, sleep_deep, sleep_rem, hrv, readiness, steps) "
                      "VALUES (?, 1.0, 1.5, ?, 80, 8000)", (d, hrv))


def test_baseline_from_tenant_period_then_config(db):
    import hai_analysis as hai
    from datetime import date
    hai._baseline_cache.clear()
    _fill(db, date(2021, 1, 1), 40, 30.0)
    _fill(db, date(2022, 1, 1), 40, 50.0)
    with db.conn() as c:
        c.execute("INSERT INTO periods (name, type, start_date, end_date) "
                  "VALUES ('b', 'baseline', '2021-01-01', '2021-12-31')")
    b = hai.recovery_baseline(db.conn())
    assert b["kind"] == "baseline_period" and b["values"]["hrv_ms"] == 30.0
    assert b["values"]["deep_min"] == 60.0
    hai._baseline_cache.clear()
    with db.conn() as c:
        c.execute("INSERT OR REPLACE INTO system_config (key, value_json, category, source) VALUES "
                  "('recovery.baseline_window', '{\"from\": \"2022-01-01\", \"to\": \"2022-12-31\"}', 'x', 'test')")
    b = hai.recovery_baseline(db.conn())
    assert b["kind"] == "config" and b["values"]["hrv_ms"] == 50.0


def test_no_period_uses_recent_year_and_short_series_gives_none(db):
    import hai_analysis as hai
    from _time_inject import get_today
    hai._baseline_cache.clear()
    _fill(db, get_today() - timedelta(days=20), 20, 40.0)
    assert hai.recovery_baseline(db.conn()) is None            # < 30 дней — нет «обычного уровня»
    ri = hai.compute_recovery_index(target=get_today())
    assert ri["composite"] is None
    hai._baseline_cache.clear()
    _fill(db, get_today() - timedelta(days=60), 40, 40.0)
    b = hai.recovery_baseline(db.conn())
    assert b["kind"] == "recent_year" and b["values"]["hrv_ms"] == 40.0
    assert "обычного уровня" in hai.format_recovery_index(hai.compute_recovery_index(target=get_today()))
