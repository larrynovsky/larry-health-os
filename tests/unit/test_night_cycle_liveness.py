"""Негативные контроли датчиков живости §14 (нить night-cycle). Квитанции уведены
в tmp через env; главный контроль — via='none' обязан РОНЯТЬ (иначе мёртвая
доставка неотличима от 'нечего слать'). Импорт integrity_tests гоняет весь набор
один раз — меряем ДЕЛЬТЫ WARN/FAIL вокруг конкретного вызова, не абсолют."""
import json

from _time_inject import set_test_clock, clear_test_clock


def _write(p, d):
    p.write_text(json.dumps(d), encoding="utf-8")


def test_night_cycle_missing_warns(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_NIGHT_CYCLE_RECEIPT", str(tmp_path / "nope.json"))
    set_test_clock("2026-08-02")
    try:
        import integrity_tests as it
        before = it.WARN
        it.check_night_cycle_liveness()
        assert it.WARN == before + 1, "нет отметки движка — обязан warn"
    finally:
        clear_test_clock()


def test_night_cycle_fresh_is_quiet(tmp_path, monkeypatch):
    r = tmp_path / "nc.json"
    _write(r, {"ran_at": "2026-08-02T02:00", "seen": 0})
    monkeypatch.setenv("HEALTH_NIGHT_CYCLE_RECEIPT", str(r))
    set_test_clock("2026-08-02")
    try:
        import integrity_tests as it
        before = it.WARN
        res = it.check_night_cycle_liveness()
        assert it.WARN == before and "прогон" in res
    finally:
        clear_test_clock()


def test_doorbell_via_none_fails(tmp_path, monkeypatch):
    """Главный контроль: звонил, но оба канала легли → FAIL."""
    r = tmp_path / "nag.json"
    _write(r, {"ran_at": "2026-08-02T09:00", "rang": True, "via": "none", "open_count": 1})
    monkeypatch.setenv("HEALTH_NAG_RECEIPT", str(r))
    set_test_clock("2026-08-02")
    try:
        import integrity_tests as it
        before = it.FAIL
        it.check_doorbell_liveness()
        assert it.FAIL == before + 1, "via=none обязан РОНЯТЬ (мёртвая доставка)"
    finally:
        clear_test_clock()


def test_doorbell_delivered_is_quiet(tmp_path, monkeypatch):
    r = tmp_path / "nag.json"
    _write(r, {"ran_at": "2026-08-02T09:00", "rang": True, "via": "telegram", "open_count": 1})
    monkeypatch.setenv("HEALTH_NAG_RECEIPT", str(r))
    set_test_clock("2026-08-02")
    try:
        import integrity_tests as it
        bw, bf = it.WARN, it.FAIL
        it.check_doorbell_liveness()
        assert it.WARN == bw and it.FAIL == bf
    finally:
        clear_test_clock()


# ── Порог из расписания, а не литерал (нить schedule-liveness, 23.09) ──────────
# Плисты — из tests/conftest.py (цикл 08:00; колокол 8/11/14/17/20).

def test_night_cycle_missed_fire_warns_same_day(tmp_path, monkeypatch):
    """Квитанция вчерашняя, сегодняшний запуск 08:00 прошёл — литерал «>2 дня» молчал бы."""
    r = tmp_path / "nc.json"
    _write(r, {"ran_at": "2026-08-01T08:00:40", "seen": 0})
    monkeypatch.setenv("HEALTH_NIGHT_CYCLE_RECEIPT", str(r))
    set_test_clock("2026-08-02T09:00")
    try:
        import integrity_tests as it
        before = it.WARN
        it.check_night_cycle_liveness()
        assert it.WARN == before + 1, "пропущенный запуск 02.08 08:00 обязан звенеть"
    finally:
        clear_test_clock()


def test_doorbell_missed_fire_warns(tmp_path, monkeypatch):
    """Колокол отметился в 08:00, в 15:00 пропущен запуск 14:00 → warn."""
    r = tmp_path / "nag.json"
    _write(r, {"ran_at": "2026-08-02T08:00:05", "rang": False, "via": None, "open_count": 0})
    monkeypatch.setenv("HEALTH_NAG_RECEIPT", str(r))
    set_test_clock("2026-08-02T15:00")
    try:
        import integrity_tests as it
        before = it.WARN
        it.check_doorbell_liveness()
        assert it.WARN == before + 1
    finally:
        clear_test_clock()


def test_unjudged_schedule_is_loud(tmp_path, monkeypatch):
    """Плиста нет — «живость не судима» звучит, а не читается как «свежо»."""
    r = tmp_path / "nc.json"
    _write(r, {"ran_at": "2026-08-02T08:00:40", "seen": 0})
    monkeypatch.setenv("HEALTH_NIGHT_CYCLE_RECEIPT", str(r))
    monkeypatch.setenv("HEALTH_LAUNCHAGENTS_DIR", str(tmp_path / "пусто"))
    set_test_clock("2026-08-02T09:00")
    try:
        import integrity_tests as it
        before = it.WARN
        it.check_night_cycle_liveness()
        assert it.WARN == before + 1
    finally:
        clear_test_clock()
