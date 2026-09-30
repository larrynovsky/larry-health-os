"""Давление от тонометра приходит ОДНОЙ метрикой `blood_pressure` (Withings через HAE).

Фикстуры воспроизводят формат HAE: systolic/diastolic без qty и повтор одного
замера в нескольких выгрузках. Все значения и даты независимо придуманы.
Колонка хранит среднее за день, raw — максимум, тревога смотрит на максимум.
"""
from import_apple_health import aggregate_metric_by_day

_DAY = [
    {"source": "Withings", "diastolic": 88, "date": "2021-05-10 09:15:00 +0300", "systolic": 136},
    {"source": "Withings", "diastolic": 76, "date": "2021-05-10 18:30:00 +0300", "systolic": 118},
    # повтор того же замера из другой выгрузки — не должен сдвигать среднее
    {"source": "Withings", "diastolic": 76, "date": "2021-05-10 18:30:00 +0300", "systolic": 118},
]


def test_combined_bp_lands_mean_and_max():
    daily = {}
    aggregate_metric_by_day("blood_pressure", _DAY, daily)
    b = daily["2021-05-10"]
    assert b["bp_systolic"] == 127.0 and b["bp_diastolic"] == 82.0      # среднее двух замеров
    assert b["bp_systolic_max"] == 136 and b["bp_diastolic_max"] == 88  # пик виден тревоге


def test_entry_without_both_fields_is_skipped():
    daily = {}
    aggregate_metric_by_day("blood_pressure", [{"date": "2021-05-10 10:00:00 +0300",
                                                "systolic": 130}], daily)
    assert "bp_systolic" not in daily.get("2021-05-10", {})


def test_bp_threshold_moves_to_peak_without_duplicate(db):
    """Живая строка порога на среднем (bp_systolic) переименовывается в пик — не остаётся
    двух активных порогов давления (INSERT OR IGNORE сам бы не переименовал)."""
    import health_db
    db.execute("DELETE FROM absolute_thresholds WHERE metric LIKE 'bp_%'")
    db.execute("INSERT INTO absolute_thresholds (metric, direction, value, reason_template, source,"
               " source_date, kind, baseline, band_label, variant) VALUES ('bp_systolic','ceiling',"
               "140.0,'old','ESC_AHA_2023','2023-01-01','absolute',NULL,'','')")
    health_db._seed_absolute_thresholds()
    rows = db.execute("SELECT metric FROM absolute_thresholds WHERE metric LIKE 'bp_%' "
                      "AND direction='ceiling'").fetchall()
    assert sorted(r[0] for r in rows) == ["bp_diastolic_max", "bp_systolic_max"]


def test_fitness_metrics_land_as_daily_mean_without_duplicates():
    """Копии одного вымышленного замера считаются по времени один раз."""
    daily = {}
    e = {"date": "2026-06-30 09:31:00 +0300", "qty": 500}
    aggregate_metric_by_day("six_minute_walking_test_distance", [e, dict(e), dict(e)], daily)
    aggregate_metric_by_day("stair_speed_up", [
        {"date": "2026-06-30 17:51:00 +0300", "qty": 0.3},
        {"date": "2026-06-30 22:49:00 +0300", "qty": 0.2}], daily)
    b = daily["2026-06-30"]
    assert b["six_min_walk_m"] == 500 and b["stair_speed_up_ms"] == 0.25


def test_bodycomp_from_apple_health_takes_morning_and_only_fills_empty(db):
    """Утреннее значение заполняет только пустые ячейки.
    Повтор одного момента с другим поясом не создаёт второй замер;
    существующие данные другого импортёра сохраняются."""
    import health_db
    daily = {}
    aggregate_metric_by_day("body_fat_percentage", [
        {"date": "2021-05-10 07:24:00 +0400", "qty": 20.44},
        {"date": "2021-05-10 18:00:00 +0300", "qty": 22.0}], daily)
    assert daily["2021-05-10"]["body_fat_pct"] == 20.4
    db.execute("INSERT OR IGNORE INTO daily_metrics(date, body_fat_pct) VALUES ('2021-05-01', 20.0)")
    health_db.upsert_metrics_from_json("2021-05-01", {"body_fat_pct": 99.0}, source="AppleHealth")
    health_db.upsert_metrics_from_json("2021-05-02", {"body_fat_pct": 21.0}, source="AppleHealth")
    got = dict(db.execute("SELECT date, body_fat_pct FROM daily_metrics "
                          "WHERE date IN ('2021-05-01','2021-05-02')").fetchall())
    assert got == {"2021-05-01": 20.0, "2021-05-02": 21.0}
