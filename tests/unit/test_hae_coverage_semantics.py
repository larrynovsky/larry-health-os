"""Позитивный контроль к семантике ПОКРЫТИЯ ДАННЫХ (вариант A) в upsert_hae_metric.

first_seen/last_seen = покрытие данных: first только раньше, last только позже,
NULL-safe, монотонно (покрытие не сужается). Даты приходят из data_first/data_last
(min/max дат записей, добывает hae_checker). Голый upsert без дат покрытие не трогает.
"""
import health_db


def test_coverage_set_on_insert(db):
    health_db.upsert_hae_metric("m_new", status="new",
                                data_first="2024-03-01", data_last="2024-03-31")
    reg = health_db.get_hae_registry()
    assert reg["m_new"]["first_seen"] == "2024-03-01"
    assert reg["m_new"]["last_seen"] == "2024-03-31"


def test_coverage_expands_outward_only(db):
    db.execute("INSERT INTO hae_metric_registry (metric_name, status, first_seen, last_seen) "
               "VALUES ('m_cov', 'handled', '2024-06-01', '2024-06-30')")
    # новые данные шире с обеих сторон → покрытие расширяется наружу
    health_db.upsert_hae_metric("m_cov", data_first="2024-01-15", data_last="2024-12-20")
    reg = health_db.get_hae_registry()
    assert reg["m_cov"]["first_seen"] == "2024-01-15"
    assert reg["m_cov"]["last_seen"] == "2024-12-20"


def test_coverage_never_shrinks(db):
    db.execute("INSERT INTO hae_metric_registry (metric_name, status, first_seen, last_seen) "
               "VALUES ('m_narrow', 'handled', '2024-01-01', '2024-12-31')")
    # более узкие данные НЕ должны сужать существующее покрытие (монотонность)
    health_db.upsert_hae_metric("m_narrow", data_first="2024-05-01", data_last="2024-06-01")
    reg = health_db.get_hae_registry()
    assert reg["m_narrow"]["first_seen"] == "2024-01-01"
    assert reg["m_narrow"]["last_seen"] == "2024-12-31"


def test_first_seen_backfilled_from_null(db):
    # pre-seeded строка с пустым покрытием (как реальные 29 строк реестра) → доливается
    db.execute("INSERT INTO hae_metric_registry (metric_name, status, first_seen, last_seen) "
               "VALUES ('m_null', 'handled', NULL, NULL)")
    health_db.upsert_hae_metric("m_null", data_first="2023-08-17", data_last="2026-07-09")
    reg = health_db.get_hae_registry()
    assert reg["m_null"]["first_seen"] == "2023-08-17"
    assert reg["m_null"]["last_seen"] == "2026-07-09"


def test_no_dates_leaves_coverage_untouched(db):
    db.execute("INSERT INTO hae_metric_registry (metric_name, status, first_seen, last_seen) "
               "VALUES ('m_alert', 'new', '2024-02-02', '2024-02-02')")
    # alert-джоба ставит только alerted_at — покрытие не трогаем
    health_db.upsert_hae_metric("m_alert", alerted_at="2024-07-01")
    reg = health_db.get_hae_registry()
    assert reg["m_alert"]["first_seen"] == "2024-02-02"
    assert reg["m_alert"]["last_seen"] == "2024-02-02"
    assert reg["m_alert"]["alerted_at"] == "2024-07-01"
