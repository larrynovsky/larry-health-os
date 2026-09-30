"""Unit-тесты ecg_db — дедуп записей ЭКГ, отбрасывание сырой волны, окно/фильтр нон-синуса.

Канон не трогаем: fixture создаёт tmp-БД через init_db (там же _migrate_ecg_readings).
Регресс-гард для HAE ECG ingest (2026-07-06).
"""
from datetime import datetime, timezone, timedelta

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    return health_db


_FMT = "%Y-%m-%d %H:%M:%S %z"


def _entry(start, cls, hr=72, source="Apple Watch"):
    return {
        "start": start, "end": start, "classification": cls,
        "severity": "Not Set", "averageHeartRate": hr,
        "numberOfVoltageMeasurements": 15360, "samplingFrequency": 512,
        "source": source,
        "voltageMeasurements": [{"date": start, "voltage": 0.1, "units": "V"}],
    }


def test_parse_start(db):
    import ecg_db
    assert ecg_db._parse_start("2026-07-06 14:30:00 -0800") == "2026-07-06T22:30:00Z"
    assert ecg_db._parse_start("мусор") is None
    assert ecg_db._parse_start("") is None


def test_save_dedup_and_no_waveform(db):
    import ecg_db
    e = _entry("2026-07-06 14:30:00 -0800", "Sinus Rhythm")
    assert ecg_db.save_ecg_readings([e], "f1.json") == 1
    # та же запись (start_time+source) → 0 новых
    assert ecg_db.save_ecg_readings([e], "f2.json") == 0
    with db.get_conn() as c:
        row = c.execute("SELECT classification, avg_hr, sampling_hz, raw_meta FROM ecg_readings").fetchone()
    assert row["classification"] == "Sinus Rhythm"
    assert row["avg_hr"] == 72
    assert row["sampling_hz"] == 512
    # сырая волна НЕ попадает в БД
    assert "voltageMeasurements" not in row["raw_meta"]


def test_nonsinus_filter_and_window(db):
    import ecg_db
    now = datetime.now(timezone.utc)
    afib_recent = (now - timedelta(hours=2)).astimezone().strftime(_FMT)
    sinus_recent = (now - timedelta(hours=3)).astimezone().strftime(_FMT)
    afib_old = (now - timedelta(hours=100)).astimezone().strftime(_FMT)
    ecg_db.save_ecg_readings([
        _entry(afib_recent, "Atrial Fibrillation", hr=110),
        _entry(sinus_recent, "Sinus Rhythm"),
        _entry(afib_old, "Atrial Fibrillation"),
    ], "f.json")
    hits = ecg_db.find_nonsinus(48)
    # синус отфильтрован по классификации, старый AFib — вне окна 48ч
    assert len(hits) == 1
    assert hits[0]["classification"] == "Atrial Fibrillation"
    assert hits[0]["avg_hr"] == 110


def test_get_recent_all_classes(db):
    import ecg_db
    now = datetime.now(timezone.utc)
    ecg_db.save_ecg_readings([
        _entry((now - timedelta(hours=1)).astimezone().strftime(_FMT), "Sinus Rhythm"),
        _entry((now - timedelta(hours=2)).astimezone().strftime(_FMT), "Inconclusive Poor Recording"),
    ], "f.json")
    recent = ecg_db.get_recent_ecg(5)
    assert len(recent) == 2  # get_recent не фильтрует по классу
