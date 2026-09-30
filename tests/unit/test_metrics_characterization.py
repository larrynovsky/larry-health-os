"""
tests/unit/test_metrics_characterization.py — характеризационные пины домена
metrics/daily в health_db (Поток B рефакторинга, 2026-06-27).
  get_day, get_window, get_stats, get_metric_percentiles.

behavior-preserving пины: «как есть» ≠ «как правильно».
"""
from __future__ import annotations

from datetime import date

import health_db


def test_get_day_missing_returns_empty_dict(db):
    assert health_db.get_day("2099-01-01") == {}


def test_get_day_returns_steps_from_flat_column(db):
    db.add_daily_metrics("2026-05-08", steps=8000)
    assert health_db.get_day("2026-05-08").get("steps") == 8000


def test_get_day_flat_hrv_surfaces_as_fallback(db):
    # PIN-VERDICT: design — ПОЧИНЕНО 2026-07-02 (был footgun): flat-only hrv
    # теперь попадает в get_day в raw-совместимой форме {avg,unit,source='flat'}.
    # Потребители читают (day.get("hrv") or {}).get("avg") — форма сохранена.
    db.add_daily_metrics("2026-05-08", hrv=25)
    day = health_db.get_day("2026-05-08")
    assert (day.get("hrv") or {}).get("avg") == 25
    assert day["hrv"]["source"] == "flat"


def test_get_day_raw_hrv_wins_over_flat(db):
    # Регресс-защита: когда raw-hrv (Oura) есть, flat НЕ перетирает его.
    import json as _j
    db.add_daily_metrics("2026-05-09", hrv=25)
    with health_db.get_conn() as c:
        c.execute("UPDATE daily_metrics SET raw=? WHERE date=?",
                  (_j.dumps({"hrv": {"avg": 30, "unit": "ms", "source": "Oura"}}),
                   "2026-05-09"))
        c.commit()
    day = health_db.get_day("2026-05-09")
    assert day["hrv"]["avg"] == 30
    assert day["hrv"]["source"] == "Oura"


def test_get_day_flat_vitals_surface_as_fallback(db):
    # PIN-VERDICT: design — follow-up #3 (2026-07-02): resting_hr/readiness/spo2
    # из flat-колонок теперь видны в get_day в форме, которую ждут потребители.
    db.add_daily_metrics("2026-05-10", resting_hr=56, readiness=77, spo2_avg=95)
    day = health_db.get_day("2026-05-10")
    assert (day.get("resting_heart_rate") or {}).get("value") == 56
    assert day.get("readiness_score") == 77          # голое число (как ждёт потребитель)
    assert (day.get("spo2") or {}).get("avg") == 95
    assert day["resting_heart_rate"]["source"] == "flat"


def test_get_day_raw_vitals_win_over_flat(db):
    # Регресс: raw-значения (Oura) не перетираются flat-fallback.
    import json as _j
    db.add_daily_metrics("2026-05-11", resting_hr=56, readiness=77, spo2_avg=95)
    with health_db.get_conn() as c:
        c.execute("UPDATE daily_metrics SET raw=? WHERE date=?", (_j.dumps({
            "resting_heart_rate": {"value": 60, "source": "Oura"},
            "readiness_score": 80,
            "spo2": {"avg": 97, "source": "Oura"},
        }), "2026-05-11"))
        c.commit()
    day = health_db.get_day("2026-05-11")
    assert day["resting_heart_rate"]["value"] == 60
    assert day["readiness_score"] == 80
    assert day["spo2"]["avg"] == 97


def test_get_day_sleep_not_reconstructed_from_flat(db):
    # PIN-VERDICT: design — sleep СОЗНАТЕЛЬНО не собирается из flat-колонок
    # (raw['sleep'] многоключевой; частичная сборка хуже None). Фиксируем решение.
    db.add_daily_metrics("2026-05-12", sleep_total=6.0)
    day = health_db.get_day("2026-05-12")
    assert "sleep" not in day


def test_get_window_length_and_missing_filled(db):
    db.add_daily_metrics("2026-05-08", hrv=42)
    win = health_db.get_window(end=date(2026, 5, 10), days=3)
    assert len(win) == 3
    assert all("date" in d for d in win)
    assert len([d for d in win if d.get("hrv") == 42]) == 1


def test_get_stats_returns_aggregate_keys(db):
    db.add_daily_metrics("2026-05-15", hrv=20, sleep_total=7.0)
    db.add_daily_metrics("2026-05-16", hrv=30, sleep_total=8.0)
    stats = health_db.get_stats(days=30, end=date(2026, 5, 31))
    assert "avg_hrv" in stats
    assert stats["avg_hrv"] == 25.0


def test_get_metric_percentiles_empty_without_data(db):
    result = health_db.get_metric_percentiles()
    assert isinstance(result, dict)
    assert result == {}
