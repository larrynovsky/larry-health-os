"""6 новых watchdog-функций в integrity_tests.py (SX-17k)."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.unit


def test_check_assessments_freshness_warns_when_no_filling(db, tmp_path, monkeypatch):
    # подменяем каталог инструментов
    cat = tmp_path / "instruments"
    cat.mkdir()
    (cat / "isi.json").write_text(json.dumps({
        "id": "isi", "cadence_days": 30,
        "items": [], "subscales": [],
    }))
    import integrity_tests as it
    monkeypatch.setattr(it, "SX_INSTRUMENTS_DIR", cat)
    # warn() пишет в _warnings — проверим что добавилась запись
    initial = list(it._warnings)
    it.check_assessments_freshness()
    new_warns = it._warnings[len(initial):]
    assert any("isi" in w[1] for w in new_warns)


def _isi_catalog(tmp_path, monkeypatch):
    cat = tmp_path / "instruments"
    cat.mkdir(exist_ok=True)
    (cat / "isi.json").write_text(json.dumps({"id": "isi", "cadence_days": 30,
                                              "items": [], "subscales": []}))
    import integrity_tests as it
    monkeypatch.setattr(it, "SX_INSTRUMENTS_DIR", cat)
    return it


def _warns_of(it, fn):
    initial = len(it._warnings)
    fn()
    return it._warnings[initial:]


def test_assessment_overdue_is_silent_while_delivered_task_carries_it(db, tmp_path, monkeypatch):
    """Один канал (2026-09-03): доставленная (sent_at) открытая задача с deadline в
    будущем несёт вопрос — датчик просрочки молчит; deadline прошёл — звонит снова."""
    it = _isi_catalog(tmp_path, monkeypatch)
    fut = (date.today() + timedelta(days=4)).isoformat()
    db.execute("INSERT INTO tasks (source, type, priority, content, status, fingerprint, sent_at, deadline)"
               " VALUES ('assessment_scheduler','assessment','medium','isi','open','assessment:isi',"
               " '2026-09-03 10:00', ?)", (fut,))
    assert not any("просрочены" in w[0] for w in _warns_of(it, it.check_assessments_freshness))
    past = (date.today() - timedelta(days=1)).isoformat()
    db.execute("UPDATE tasks SET deadline=? WHERE fingerprint='assessment:isi'", (past,))
    assert any("просрочены" in w[0] for w in _warns_of(it, it.check_assessments_freshness))


def test_assessment_task_not_delivered_is_liveness_warn(db, tmp_path, monkeypatch):
    """Задача с sent_at IS NULL старше 2 суток — outbox бота мёртв (§14); просрочка при
    этом продолжает звонить (канала нет). Свежая недоставленная — ещё не находка."""
    it = _isi_catalog(tmp_path, monkeypatch)
    old = (date.today() - timedelta(days=3)).isoformat() + " 00:30:01"
    db.execute("INSERT INTO tasks (created_at, source, type, priority, content, status, fingerprint)"
               " VALUES (?, 'assessment_scheduler','assessment','medium','isi','open','assessment:isi')",
               (old,))
    ws = _warns_of(it, it.check_assessments_freshness)
    assert any("не доставлена боту" in w[0] and "isi" in w[1] for w in ws)
    assert any("просрочены" in w[0] for w in ws)
    db.execute("UPDATE tasks SET created_at=datetime('now') WHERE fingerprint='assessment:isi'")
    assert not any("не доставлена боту" in w[0] for w in _warns_of(it, it.check_assessments_freshness))


def test_pro_score_two_units_under_one_name_is_red(db):
    """Решение владельца 2026-09-03: PRO-балл под одним именем — одна размерность.
    Позитив: 'score' и 'score_0_100' под isi_total → FAIL; негативный контроль: после
    приведения к одной единице — зелёный. Значения — синтетика (12/28 → 42.86 по шкале 0–100)."""
    import integrity_tests as it
    db.execute("INSERT INTO lab_results (date, source, test_name, value, unit) VALUES "
               "('2021-02-10','instrument:isi','isi_total',12.0,'score')")
    db.execute("INSERT INTO lab_results (date, source, test_name, value, unit) VALUES "
               "('2021-05-10','instrument:isi','isi_total',38.0,'score_0_100')")
    with pytest.raises(AssertionError, match="isi_total"):
        it.check_pro_score_unit_single(db_path=db.path)
    db.execute("UPDATE lab_results SET unit='score_0_100', value=42.86 WHERE date='2021-02-10'")
    assert it.check_pro_score_unit_single(db_path=db.path) == {"mixed": 0}


def test_check_literature_freshness_warns_when_old(db, tmp_path, monkeypatch):
    # Insert старый literature_search (>10 дней)
    old = (date.today() - timedelta(days=20)).isoformat()
    # С 23.09 судится момент записи (created_at, UTC) против последнего планового запуска.
    db.execute(
        "INSERT INTO agent_reports (date, agent_type, agent_name, has_findings, created_at) "
        "VALUES (?, 'literature_search', 'test', 0, ?)",
        (old, old + " 01:00:00"),
    )
    import integrity_tests as it
    initial = list(it._warnings)
    it.check_literature_freshness()
    new_warns = it._warnings[len(initial):]
    assert any("literature_search" in str(w) for w in new_warns)


def test_check_pending_proposals_ageing_warns_on_old(db):
    old = (date.today() - timedelta(days=30)).isoformat() + " 00:00:00"
    db.execute(
        "INSERT INTO problem_list_proposals (created_at, source, proposed, status) "
        "VALUES (?, 'test', '[]', 'pending')",
        (old,),
    )
    import integrity_tests as it
    initial = list(it._warnings)
    it.check_pending_proposals_ageing()
    new_warns = it._warnings[len(initial):]
    assert any("21" in w[0] or "pending" in w[0] for w in new_warns)


def _report(db, agent_type, created_at, day=None):
    db.execute("INSERT INTO agent_reports (date, agent_type, agent_name, has_findings, created_at) "
               "VALUES (?, ?, ?, 0, ?)", (day or created_at[:10], agent_type, agent_type, created_at))


def _new_warns(it, fn):
    before = len(it._warnings)
    fn()
    return [w[0] for w in it._warnings[before:]]


def test_literature_fresh_after_last_scheduled_run_is_silent(db):
    """Позитив к тесту выше: запись ПОСЛЕ последнего планового запуска — тишина, даже если
    `date` отчёта старше (у агентов это бывает дата периода)."""
    import integrity_tests as it
    now_utc = it.get_utcnow().strftime("%Y-%m-%d %H:%M:%S")
    _report(db, "literature_search", now_utc, day="2000-01-01")
    assert not any("literature_search не запускался" in w
                   for w in _new_warns(it, it.check_literature_freshness))


def test_consilium_judged_by_monthly_plist_not_by_40_days(db):
    """23.09: консилиум судится по живому плисту (1-го числа 04:00), след — created_at.
    Запись за день ДО последнего запуска — «не бежал»; запись после — тишина.
    Литерала «40 дней» больше нет: 39 дней без запуска тоже видны."""
    import integrity_tests as it
    import plist_env_liveness as pl
    fire = pl.last_scheduled_fire(it.CONSILIUM_LABEL, it.get_now())
    assert fire is not None, "тестовый плист консилиума (tests/conftest.py) не читается"
    before = (fire - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    _report(db, "monthly_consilium", before)
    assert any("MDT-консилиум не бежал" in w for w in _new_warns(it, it.check_consilium_freshness))
    # Запись ПОСЛЕ запуска, датированная периодом раньше запуска (как живой 31.08 → 01.09):
    # судится момент записи, а не `date`.
    _report(db, "monthly_consilium", it.get_utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            day=before[:10])
    assert not any("MDT-консилиум не бежал" in w
                   for w in _new_warns(it, it.check_consilium_freshness))


def test_sqlite_utc_stamp_gets_explicit_zone():
    """datetime('now') SQLite — UTC без пояса. Без явного +00:00 момент читался бы как
    местный: запись консилиума 01:06 UTC (04:06 по местному времени дома) «опоздала» бы к запуску 04:00."""
    import integrity_tests as it
    assert it._utc_trace("2026-09-01 01:06:30") == "2026-09-01T01:06:30+00:00"
    assert it._utc_trace("2026-09-01T01:06:30+03:00") == "2026-09-01T01:06:30+03:00"
    assert it._utc_trace(None) is None


def test_instruments_dir_is_the_tenant_home():
    """28.09.2026: integrity читал ~/health/data/instruments у любого тенанта; дом один —
    каталог планировщика (у партнёра — его data dir)."""
    import assessment_scheduler, integrity_tests as it
    assert it.SX_INSTRUMENTS_DIR == assessment_scheduler.INSTRUMENTS_DIR
