"""Давление Withings: токен с ротацией, разбор мер, зеркало замеров, дневное давление и его сторож (нити withings-bp, bp-withings-owner).

Значения ниже вымышленные. Зачем нить: давление через Apple Health → HAE не приходило с одной из летних дат
по 04.10, и ни один датчик этого не видел; Withings — второй независимый путь и сторож первого.
"""
from __future__ import annotations

import io
import json
import sqlite3
import time
from pathlib import Path

import pytest

import withings_api as wa

pytestmark = pytest.mark.unit


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _answers(monkeypatch, bodies):
    """urlopen отдаёт ответы по очереди; запросы копятся для проверки."""
    sent = []

    def fake(req, timeout=30):
        sent.append(req)
        return _Resp(json.dumps(bodies.pop(0)).encode())
    monkeypatch.setattr(wa.urllib.request, "urlopen", fake)
    return sent


@pytest.fixture
def secrets(tmp_path, monkeypatch):
    sd = tmp_path / "secrets"
    sd.mkdir()
    (sd / "withings_client_id").write_text("cid")
    (sd / "withings_client_secret").write_text("csecret")
    monkeypatch.setattr(wa, "secrets_dir", lambda: sd)
    return sd


def test_http_200_with_error_status_is_a_failure(monkeypatch, secrets):
    _answers(monkeypatch, [{"status": 401, "body": {}}])
    with pytest.raises(wa.WithingsError, match="status 401"):
        wa._post(wa.MEASURE_URL, {"action": "getmeas"}, bearer="t")


def test_not_connected_is_none(tmp_path, secrets):
    assert wa.withings_access_token(tmp_path / "data") is None


def test_expiring_token_rotates_and_new_refresh_is_saved_before_use(tmp_path, monkeypatch, secrets):
    (secrets / wa.TOKEN_NAME).write_text(json.dumps(
        {"access_token": "old", "refresh_token": "r-old", "expires_at": time.time() + 10}))
    sent = _answers(monkeypatch, [{"status": 0, "body": {
        "access_token": "new", "refresh_token": "r-new", "expires_in": 10800}}])
    data = tmp_path / "data"
    assert wa.withings_access_token(data) == "new"
    assert json.loads((data / wa.TOKEN_NAME).read_text())["refresh_token"] == "r-new"
    form = sent[0].data.decode()
    assert "action=requesttoken" in form and "grant_type=refresh_token" in form and "r-old" in form


def test_fresh_token_is_used_without_network(tmp_path, monkeypatch, secrets):
    (secrets / wa.TOKEN_NAME).write_text(json.dumps(
        {"access_token": "live", "refresh_token": "r", "expires_at": time.time() + 3600}))
    _answers(monkeypatch, [])
    assert wa.withings_access_token(tmp_path / "data") == "live"


def _grp(ts, sys_, dia, pulse=None, grpid=1):
    m = [{"type": 10, "value": sys_ * 10, "unit": -1}, {"type": 9, "value": dia, "unit": 0}]
    if pulse is not None:
        m.append({"type": 11, "value": pulse, "unit": 0})
    return {"grpid": grpid, "date": ts, "measures": m}


def test_fetch_decodes_units_skips_non_bp_groups_and_pages(monkeypatch, secrets):
    _answers(monkeypatch, [
        {"status": 0, "body": {"more": 1, "offset": 7, "measuregrps": [
            _grp(1900000000, 121, 79, 64),
            {"grpid": 2, "date": 1900000100, "measures": [{"type": 11, "value": 70, "unit": 0}]}]}},
        {"status": 0, "body": {"more": 0, "measuregrps": [_grp(1900000200, 133, 85, grpid=3)]}}])
    rows = wa.fetch_bp("t", 0)
    assert [(r["ts"], r["systolic"], r["diastolic"], r["pulse"]) for r in rows] == [
        (1900000000, 121.0, 79, 64), (1900000200, 133.0, 85, None)]


def test_ambiguous_reading_of_shared_device_is_skipped(monkeypatch, secrets):
    amb = _grp(1900000300, 142, 97, grpid=4)
    amb["attrib"] = 1                                       # прибор не знает, чей это замер
    mine = _grp(1900000400, 111, 81, grpid=5)
    mine["attrib"] = 0
    _answers(monkeypatch, [{"status": 0, "body": {"more": 0, "measuregrps": [amb, mine]}}])
    assert [r["grpid"] for r in wa.fetch_bp("t", 0)] == [5]


def test_more_without_new_offset_refuses_instead_of_looping(monkeypatch, secrets):
    _answers(monkeypatch, [{"status": 0, "body": {"more": 1, "measuregrps": []}}])
    with pytest.raises(wa.WithingsError, match="offset"):
        wa.fetch_bp("t", 0)


def _hdb():
    import health_db
    return health_db


def _ts(days_ago, hour=12):
    """Момент замера в местном поясе установки — чтобы день замера был однозначен."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    import region_pack
    tz = ZoneInfo(region_pack.value("timezone", "UTC"))
    t = datetime.now(tz).replace(hour=hour, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
    return int(t.timestamp()), t.date().isoformat()


def _run(iw, monkeypatch, rows):
    monkeypatch.setattr(iw, "withings_access_token", lambda d: "t")
    monkeypatch.setattr(iw, "fetch_bp", lambda tok, since: [dict(r) for r in rows])
    return iw.run()


def _day(db_, date):
    with _hdb().get_conn() as c:
        r = c.execute("SELECT bp_systolic, bp_diastolic, raw FROM daily_metrics WHERE date=?", (date,)).fetchone()
    return (r[0], r[1], json.loads(r[2] or "{}")) if r else None


def _r(ts, s, d, grpid):
    return {"ts": ts, "systolic": s, "diastolic": d, "pulse": None, "grpid": grpid}


def test_import_is_idempotent_and_day_is_mean_of_all_readings(db, monkeypatch):
    import import_withings as iw
    (t1, day), (t2, _) = _ts(3, 8), _ts(3, 20)
    rows = [_r(t1, 121.0, 79, 1), _r(t2, 131.0, 85, 2)]
    assert _run(iw, monkeypatch, rows) == 0 and _run(iw, monkeypatch, rows) == 0
    with _hdb().get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM bp_readings").fetchone()[0] == 2
    s, d, raw = _day(None, day)
    assert (s, d) == (126.0, 82.0)
    assert (raw["bp_systolic_max"], raw["bp_diastolic_max"], raw["bp_source"]) == (131.0, 85, "withings")


def test_reading_deleted_in_withings_is_deleted_here_and_its_day_cleared(db, monkeypatch):
    import import_withings as iw
    (t1, day1), (t2, day2) = _ts(5), _ts(2)
    _run(iw, monkeypatch, [_r(t1, 120.0, 80, 1), _r(t2, 142.0, 97, 2)])
    _run(iw, monkeypatch, [_r(t1, 120.0, 80, 1)])            # удалённый в приложении замер
    with _hdb().get_conn() as c:
        assert [r[0] for r in c.execute("SELECT measured_at FROM bp_readings")] == [t1]
    s, d, raw = _day(None, day2)
    assert (s, d) == (None, None) and "bp_source" not in raw and "bp_systolic_max" not in raw
    assert _day(None, day1)[0] == 120.0


def test_empty_answer_with_history_refuses_instead_of_wiping(db, monkeypatch):
    import import_withings as iw
    t1, _ = _ts(4)
    _run(iw, monkeypatch, [_r(t1, 120.0, 80, 1)])
    with pytest.raises(wa.WithingsError, match="ноль замеров"):
        _run(iw, monkeypatch, [])
    with _hdb().get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM bp_readings").fetchone()[0] == 1


def test_foreign_value_is_overwritten_from_first_withings_day_older_left_alone(db, monkeypatch):
    import import_withings as iw
    (t1, day1), (_, day_mid), (_, day_old) = _ts(6), _ts(3), _ts(30)
    with _hdb().get_conn() as c:                                   # значения «Здоровья» до решения 06.10
        for dd, v in ((day1, 126.5), (day_mid, 150.0), (day_old, 118.0)):
            c.execute("INSERT INTO daily_metrics(date, bp_systolic, bp_diastolic) VALUES (?,?,?)", (dd, v, 80.0))
    _run(iw, monkeypatch, [_r(t1, 111.0, 81, 1)])
    assert _day(None, day1)[:2] == (111.0, 81.0)                 # чужое среднее заменено
    assert _day(None, day_mid)[:2] == (None, None)               # после первого дня Withings — один хозяин
    assert _day(None, day_old)[:2] == (118.0, 80.0)              # до Withings — история «Здоровья»


def test_apple_health_bp_is_only_a_witness_once_withings_owns(db):
    import metrics_db
    payload = {"bp_systolic": 140.0, "bp_diastolic": 90.0, "bp_systolic_max": 142.0, "bp_diastolic_max": 97.0}
    metrics_db.upsert_metrics_from_json("2026-01-10", payload, source="AppleHealth")
    assert _day(None, "2026-01-10")[:2] == (140.0, 90.0)        # Withings нет — пишет «Здоровье» (партнёр)
    _hdb().init_db()                                           # таблица замеров появляется с Withings
    with _hdb().get_conn() as c:
        c.execute("INSERT INTO bp_readings (measured_at, systolic, diastolic) VALUES (1, 111, 81)")
    metrics_db.upsert_metrics_from_json("2026-01-11", payload, source="AppleHealth")
    s, d, raw = _day(None, "2026-01-11")
    assert (s, d) == (None, None) and "bp_systolic_max" not in raw
    assert raw["apple_health_bp"]["bp_systolic_max"] == 142.0


def test_import_without_withings_is_silent_zero(db, monkeypatch):
    import import_withings as iw
    monkeypatch.setattr(iw, "withings_access_token", lambda d: None)
    assert iw.run() == 0


# ── сторож: дневное давление — среднее замеров Withings, второго писателя нет ──────────────

def _tenant(tmp_path, readings, days):
    """readings: [(days_ago, sys, dia)]; days: {days_ago: (sys, dia)} — что лежит в daily_metrics."""
    import integrity_tests as it
    home = tmp_path / "health"
    (home / "data").mkdir(parents=True)
    dbp = home / "data" / "health.db"
    con = sqlite3.connect(dbp)
    con.execute("CREATE TABLE bp_readings (measured_at INTEGER PRIMARY KEY, systolic REAL, diastolic REAL)")
    con.execute("CREATE TABLE daily_metrics (date TEXT PRIMARY KEY, bp_systolic REAL, bp_diastolic REAL)")
    for i, (ago, s, d) in enumerate(readings):
        con.execute("INSERT INTO bp_readings VALUES (?,?,?)", (_ts(ago, 8 + i)[0], s, d))
    for ago, (s, d) in days.items():
        con.execute("INSERT INTO daily_metrics VALUES (?,?,?)", (_ts(ago)[1], s, d))
    con.commit()
    con.close()
    return it, dbp


def _fired(monkeypatch, it, dbp):
    monkeypatch.setattr(it, "_tenant_db_paths", lambda include_current=True: [str(dbp)])
    it._warnings.clear()
    it.check_bp_day_is_withings()
    return [w for w in it._warnings if "Withings" in w[0]]


def test_sensor_silent_when_days_are_withings_means(tmp_path, monkeypatch):
    it, dbp = _tenant(tmp_path, [(5, 120, 80), (5, 130, 84)], {5: (125.0, 82.0), 40: (118.0, 79.0)})
    assert _fired(monkeypatch, it, dbp) == []


def test_sensor_rings_on_second_writer_and_on_wrong_mean(tmp_path, monkeypatch):
    it, dbp = _tenant(tmp_path, [(9, 120, 80), (5, 130, 84)], {9: (126.5, 89.0), 5: (130.0, 84.0), 2: (140.0, 90.0)})
    fired = _fired(monkeypatch, it, dbp)
    assert fired and "2 дн." in fired[0][0]                    # 9-й — не то среднее, 2-й — без замеров
