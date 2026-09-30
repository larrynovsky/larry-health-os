"""Текст оконной тревоги несёт число САМОГО порога, а не литерал чужого ряда (BL-PUB-16 а, 27.09).

Шаблон с фиксированным числом может разойтись с персонализированным правилом.
Придуманный порог и значения ниже проверяют подстановку числа самого правила.
Поиск по прежнему source не подходит: персонализация меняет источник.
"""
from __future__ import annotations


def test_every_trend_reason_takes_threshold_from_rule():
    import health_db as hdb
    for key, tmpl in hdb._TREND_REASONS.items():
        assert "{thr" in tmpl, key
        assert "23" in tmpl.format(val=1.0, n=2, thr=23.0), key


def test_scrub_rewrites_personalized_row_by_identity(db):
    import health_db as hdb
    hdb._seed_trend_thresholds()
    with db.conn() as c:
        c.execute("UPDATE trend_thresholds SET value=32, source='p10_personal_trend', "
                  "reason_template='ВСР <40 мс два дня подряд (мин {val:.0f})' "
                  "WHERE metric='hrv' AND window_days=2 AND mode='consecutive'")
    hdb._scrub_leaky_seed_reasons()
    row = db.fetchone("SELECT reason_template FROM trend_thresholds "
                      "WHERE metric='hrv' AND window_days=2 AND mode='consecutive'")
    assert row["reason_template"] == hdb._TREND_REASONS[("hrv", 2, "consecutive")]


def test_fired_trend_alert_shows_personal_threshold(db):
    import health_db as hdb
    import trend_alerts
    hdb._seed_trend_thresholds()
    hdb._scrub_leaky_seed_reasons()
    with db.conn() as c:
        c.execute("UPDATE trend_thresholds SET value=32 "
                  "WHERE metric='hrv' AND window_days=2 AND mode='consecutive'")
        for d, v in (("2042-04-05", 27.0), ("2042-04-06", 26.0)):
            c.execute("INSERT OR REPLACE INTO daily_metrics (date, hrv) VALUES (?, ?)", (d, v))
    hits = [a for a in trend_alerts.evaluate_trends() if a["metric"] == "hrv"]
    assert hits and "32 мс" in hits[0]["reason"] and "40" not in hits[0]["reason"]


def test_bootstrap_without_own_data_is_not_someone_elses_series(db):
    """Стартовые значения — шкала прибора или «молчит»; источник не выдаёт себя за анализ ряда."""
    import health_db as hdb
    hdb._seed_absolute_thresholds()
    hdb._seed_trend_thresholds()
    for table in ("absolute_thresholds", "trend_thresholds"):
        rows = db.fetchall(f"SELECT metric, source FROM {table} "
                           "WHERE metric IN ('hrv','readiness','sleep_deep','sleep_score','sleep_total') "
                           "AND source NOT LIKE 'code_migration_F%'")
        assert rows
        for r in rows:
            assert r["source"].startswith("bootstrap_"), (table, r["metric"], r["source"])
