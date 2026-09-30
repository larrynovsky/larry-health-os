"""hae_checker.judge_payload: хозяин у каждой прибывшей метрики — по ПОВЕДЕНИЮ разборщика.

Реестр может объявлять составную метрику обработанной, хотя разборщик
не создаёт ни одной колонки. Судья проверяет результат настоящего
aggregate_metric_by_day; решение «не берём:» с «покрыто: <колонка>»
проверяется по дням прихода на вымышленных входах.
"""
import json

import health_db
import hae_checker


def _payload(tmp_path, metrics):
    p = tmp_path / "HealthAutoExport-REST-20320412T000000.json"
    p.write_text(json.dumps({"data": {"metrics": metrics}}), encoding="utf-8")
    return p


def _row(db, name, status, notes=""):
    db.execute("INSERT OR REPLACE INTO hae_metric_registry (metric_name, status, notes) "
               "VALUES (?,?,?)", (name, status, notes))


def _status(name):
    return health_db.get_hae_registry()[name]["status"]


def test_parser_behaviour_beats_stale_registry_label(db, tmp_path):
    # живая строка врёт «tracked», разборщик на самом деле кладёт давление → handled
    _row(db, "blood_pressure", "tracked", "см. systolic")
    loud = hae_checker.judge_payload(_payload(tmp_path, [{"name": "blood_pressure", "data": [
        {"date": "2021-05-10 09:15:00 +0300", "systolic": 136, "diastolic": 88}]}]))
    assert _status("blood_pressure") == "handled" and loud == []


def test_parsed_but_not_stored_is_unowned(db, tmp_path, monkeypatch):
    # разборщик кладёт ключ, но база его не хранит — такая же потеря (замер 26.09: flights и др.)
    import metrics_db
    monkeypatch.setattr(metrics_db, "APPLE_RAW_KEYS", frozenset())
    _row(db, "flights_climbed", "handled")
    loud = hae_checker.judge_payload(_payload(tmp_path, [{"name": "flights_climbed", "data": [
        {"date": "2026-09-20 10:00:00 +0300", "qty": 3}]}]))
    assert _status("flights_climbed") == "unowned" and loud == ["flights_climbed"]


def test_decision_with_false_coverage_claim_is_unowned(db, tmp_path):
    # «не берём: дубль; покрыто: stand_min» — утверждение; в день прихода колонка пуста → ложь
    _row(db, "apple_stand_hour", "tracked", "не берём: дубль минут стояния; покрыто: stand_min")
    p = _payload(tmp_path, [{"name": "apple_stand_hour", "data": [
        {"date": "2026-09-01 08:00:00 +0300", "qty": 1}]}])
    assert hae_checker.judge_payload(p) == ["apple_stand_hour"]     # в колонке 09-01 пусто
    db.execute("INSERT OR IGNORE INTO daily_metrics(date) VALUES ('2026-09-01')")
    db.execute("UPDATE daily_metrics SET stand_min=30 WHERE date='2026-09-01'")
    assert hae_checker.judge_payload(p) == [] and _status("apple_stand_hour") == "tracked"


def test_unknown_metric_is_new(db, tmp_path):
    loud = hae_checker.judge_payload(_payload(tmp_path, [{"name": "totally_new_xyz", "data": [
        {"date": "2026-09-20 10:00:00 +0300", "qty": 1}]}]))
    assert loud == ["totally_new_xyz"] and _status("totally_new_xyz") == "new"


def test_seed_does_not_erase_unowned_verdict(db):
    # init_db после каждого рестарта бота не должен перетирать вердикт судьи
    _row(db, "walking_double_support_percentage", "unowned")
    health_db._seed_hae_registry()
    assert _status("walking_double_support_percentage") == "unowned"
