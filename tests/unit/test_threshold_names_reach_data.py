"""Порог обязан ДОЕЗЖАТЬ до данных (нить norm-provenance, 2026-09-02).

Дефект: absolute_thresholds держал 'CA19.9' и 'Cholesterol', lab_results — 'CA19-9' и
'Cholesterol_Total'; safety_net матчил строкой → онкомаркер с тремя уровнями тревоги
молчал 39 дней неотличимо от «в норме». Класс тот же, что у check_family_names_resolve
(25.07), но тот датчик был осознанно сужен до валид-гейта, и таблицы порогов остались вне.

Три стража:
1. Поведение: CA19-9=100 под канон-именем → safety_net даёт URGENT (то, что молчало).
2. Датчик класса check_threshold_names_reach_data: периметр ВЫЧИСЛЯЕТСЯ (таблицы с
   metric+direction), предикат в обе стороны — не-канон имя И канон без данных.
   Негативный контроль исполнен: старое написание порога → красное.
3. Миграция _canonize_threshold_metrics: переименовывает в канон, при занятом ключе не
   трогает (дубль — человеку), daily-метрики не портит.
"""
from __future__ import annotations

import pytest

import health_db
import integrity_tests as I
import safety_net as sn

pytestmark = pytest.mark.unit


@pytest.fixture
def sdb(db):
    health_db._seed_lab_trend_thresholds()
    health_db._seed_safety_lab_thresholds()
    health_db._seed_safety_lifestyle_thresholds()
    return db


def _lab(db, name, value, d="2026-08-20", lo=None, hi=None):
    with db.conn() as c:
        c.execute("INSERT INTO lab_results (date, source, test_name, value, unit, ref_low, ref_high) "
                  "VALUES (?,?,?,?,?,?,?)", (d, "doc:test", name, value, "U/mL", lo, hi))


# ── 1. Поведение предохранителя ───────────────────────────────────────────────

def test_ca19_9_above_bank_reference_warns(sdb, monkeypatch):
    """Ровно тот случай, что молчал: онкомаркер под канон-именем выше референса бланка.
    С 2026-09-02 (norm-from-documents) у онкомаркеров нет набранного urgent/critical: CTCAE их
    не грейдит, документа для абсолюта нет → вид 1 (референс бланка) даёт WARN, динамику судит
    RCV (check_lab_trends). Раньше здесь стоял придуманный 39/100/300."""
    from datetime import date
    _lab(sdb, "CA19-9", 100.0, lo=0.0, hi=39.0)
    alerts = [a for a in sn.check_lab_alerts(date(2026, 9, 2)) if a["metric"] == "CA19-9"]
    assert alerts and alerts[0]["level"] == sn.WARN and alerts[0]["direction"] == "high", alerts


@pytest.mark.owner_data
def test_reader_normalizes_data_name(sdb):
    """Данные под вариантом написания тоже встречают норму — нормализуются обе стороны."""
    from datetime import date
    _lab(sdb, "Hemoglobin", 9.0, lo=13.5, hi=17.5)     # normalize → HGB; CTCAE grade 2 → urgent
    alerts = [a for a in sn.check_lab_alerts(date(2026, 9, 2)) if a["metric"] == "HGB"]
    assert alerts and alerts[0]["level"] == sn.URGENT


def test_seed_and_fallback_keys_are_canonical():
    import lab_canon
    for k in list(sn._FALLBACK_LAB) + list(sn._FALLBACK_LAB_TREND):
        assert lab_canon.normalize(k) == k, f"резерв держит не-канон имя {k!r}"


# ── 2. Датчик класса ──────────────────────────────────────────────────────────

@pytest.mark.owner_data
def test_sensor_green_when_thresholds_meet_data(sdb):
    for m in {r[0] for r in sdb.fetchall(
            "SELECT DISTINCT metric FROM absolute_thresholds WHERE variant='safety_net'")} | \
            {r[0] for r in sdb.fetchall("SELECT DISTINCT metric FROM lab_trend_thresholds")}:
        if m in ("spo2", "readiness", "sleep_score"):
            continue
        _lab(sdb, m, 1.0)
    out = I.check_threshold_names_reach_data(db_path=str(sdb.path))
    assert set(out["tables"]) >= {"absolute_thresholds", "lab_trend_thresholds"}, "периметр не вычислен"
    assert out["checked"] > 0


@pytest.mark.owner_data
def test_sensor_red_on_old_spelling(sdb):
    """НЕГАТИВНЫЙ КОНТРОЛЬ: вернуть дефект — порог под 'CA19.9' при данных 'CA19-9'."""
    test_sensor_green_when_thresholds_meet_data(sdb)
    with sdb.conn() as c:
        c.execute("UPDATE absolute_thresholds SET metric='Hemoglobin' WHERE metric='HGB'")
    with pytest.raises(AssertionError, match="Hemoglobin.*не канон"):
        I.check_threshold_names_reach_data(db_path=str(sdb.path))


@pytest.mark.owner_data
def test_sensor_red_on_threshold_without_data(sdb):
    """Вторая сторона предиката (§17): канон-имя, под которым нет ни одной строки."""
    test_sensor_green_when_thresholds_meet_data(sdb)
    with sdb.conn() as c:
        c.execute("DELETE FROM lab_results WHERE test_name='ALT'")
    with pytest.raises(AssertionError, match="ALT: ни одной строки"):
        I.check_threshold_names_reach_data(db_path=str(sdb.path))


@pytest.mark.owner_data
def test_sensor_perimeter_is_computed_not_listed(sdb):
    """Новая таблица порогов попадает под датчик без правки датчика."""
    test_sensor_green_when_thresholds_meet_data(sdb)
    with sdb.conn() as c:
        c.execute("CREATE TABLE extra_thresholds (metric TEXT, direction TEXT, value REAL)")
        c.execute("INSERT INTO extra_thresholds VALUES ('Cholesterol','ceiling',200)")
    with pytest.raises(AssertionError, match="extra_thresholds.Cholesterol"):
        I.check_threshold_names_reach_data(db_path=str(sdb.path))


@pytest.mark.owner_data
def test_sensor_ignores_daily_metrics(sdb):
    """hrv/steps живут в daily_metrics колонками, не в lab_results — не красное."""
    test_sensor_green_when_thresholds_meet_data(sdb)
    assert sdb.count("absolute_thresholds", "metric='hrv'") > 0
    I.check_threshold_names_reach_data(db_path=str(sdb.path))


# ── 3. Миграция ───────────────────────────────────────────────────────────────

@pytest.mark.owner_data
def test_canonize_renames_old_rows(sdb):
    with sdb.conn() as c:
        c.execute("UPDATE absolute_thresholds SET metric='Hemoglobin' WHERE metric='HGB'")
        n = health_db._canonize_threshold_metrics(c, "absolute_thresholds")
    assert n >= 3
    assert sdb.count("absolute_thresholds", "metric='Hemoglobin'") == 0
    assert sdb.count("absolute_thresholds", "metric='HGB'") >= 3


@pytest.mark.owner_data
def test_canonize_keeps_clash_for_human(sdb, caplog):
    """Канон-строка уже есть → старую не трогаем (дубль решает человек, §13), но кричим."""
    with sdb.conn() as c:
        c.execute("INSERT INTO absolute_thresholds (metric,direction,value,reason_template,source,band_label,variant) "
                  "VALUES ('Hemoglobin','ceiling',1,'x','test','zzz','safety_net')")
        n = health_db._canonize_threshold_metrics(c, "absolute_thresholds")
    assert n == 0
    assert sdb.count("absolute_thresholds", "metric='Hemoglobin'") == 1
    assert any("дубль" in r.message for r in caplog.records)


def test_canonize_leaves_daily_metrics(sdb):
    before = sdb.count("absolute_thresholds", "metric='hrv'")
    with sdb.conn() as c:
        health_db._canonize_threshold_metrics(c, "absolute_thresholds")
    assert sdb.count("absolute_thresholds", "metric='hrv'") == before
