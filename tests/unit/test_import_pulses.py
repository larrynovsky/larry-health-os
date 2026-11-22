"""Пульс двух плановых задач приёма (карточка warn:producer_registry 24.09, закрыта 13.11).

Оракулы:
  • перепись: каждая задача ПО РАСПИСАНИЮ из templates/launchd классифицирована реестром
    производителей (MONITORED / EXEMPT / вычисленное покрытие) — до правки import-watchdog
    и withings-import не были ни там, ни там;
  • скрипты пишут пульс на каждом успешном прогоне;
  • датчик check_import_pulses_alive предупреждает на отсутствующем/старом пульсе и молчит на
    свежем. Плисты — свои, во временном каталоге; дата, часы и предикат «владелец в контейнере»
    подставлены (§20: исход решает тест, а не календарь и не машина);
  • один тест идёт весь путь: настоящие скрипты пишут пульс в настоящую таблицу, датчик читает.
Негативный контроль — plans/mutate_import_pulses_2026-11-14.sh."""
import plistlib
import re
from datetime import date
from pathlib import Path

import pytest

from _time_inject import set_test_clock, clear_test_clock

REPO = Path(__file__).resolve().parents[2]
_LABEL_RE = re.compile(r"<key>Label</key>\s*<string>([^<]+)</string>")


def _scheduled_template_labels() -> list[str]:
    out = []
    for p in sorted((REPO / "templates" / "launchd").glob("com.larry.health*.plist.tmpl")):
        text = p.read_text(encoding="utf-8")
        if "StartCalendarInterval" not in text and "StartInterval" not in text:
            continue
        m = _LABEL_RE.search(text)
        if m:
            out.append(m.group(1))
    return out


def test_every_scheduled_template_is_classified():
    import producer_registry as pr
    labels = _scheduled_template_labels()
    assert len(labels) > 10, "перепись шаблонов не нашла задач — сломан обход, а не реестр"
    judged = pr.schedule_judged_labels()
    classified = set(pr.MONITORED) | set(pr.EXEMPT) | set(judged)
    missing = sorted(set(labels) - classified)
    assert not missing, f"задачи по расписанию без датчика и без EXEMPT: {missing}"


# ── скрипты пишут пульс ───────────────────────────────────────────────────────

@pytest.fixture
def written(monkeypatch):
    import config_db
    got = {}
    monkeypatch.setattr(config_db, "upsert_config",
                        lambda key, value_text=None, **kw: got.__setitem__(key, value_text))
    return got


def test_import_watchdog_marks_pulse_when_fresh(monkeypatch, written):
    import import_status_db
    import import_watchdog
    monkeypatch.setattr(import_status_db, "get_import_staleness", lambda src: (False, 1.0))
    set_test_clock("2026-11-13T10:00")
    try:
        assert import_watchdog.main() == 0
    finally:
        clear_test_clock()
    assert written.get(import_watchdog.PULSE_KEY, "").startswith("2026-11-13T10:00")


def test_import_withings_marks_pulse_when_not_connected(monkeypatch, written):
    import health_db
    import import_withings
    monkeypatch.setattr(health_db, "init_db", lambda: None)
    monkeypatch.setattr(import_withings.db, "init_db", lambda: None)
    monkeypatch.setattr(import_withings, "withings_access_token", lambda d: None)
    set_test_clock("2026-11-13T09:10")
    try:
        assert import_withings.run() == 0
    finally:
        clear_test_clock()
    assert written.get(import_withings.PULSE_KEY, "").startswith("2026-11-13T09:10")


# ── датчик ────────────────────────────────────────────────────────────────────

@pytest.fixture
def plists(tmp_path, monkeypatch):
    for label, key, val in (
            ("com.larry.health.withings-import", "StartCalendarInterval",
             [{"Hour": 9, "Minute": 10}, {"Hour": 21, "Minute": 10}]),
            ("com.larry.health.import-watchdog", "StartInterval", 7200)):
        with open(tmp_path / f"{label}.plist", "wb") as fh:
            plistlib.dump({"Label": label, key: val}, fh)
    monkeypatch.setenv("HEALTH_LAUNCHAGENTS_DIR", str(tmp_path))


def _judge(monkeypatch, now: str) -> list[str]:
    """Метки предупреждений датчика при «сейчас» = now. Подставлено ВСЁ, от чего исход мог бы
    зависеть помимо теста (§20): дата модуля (её читает _schedule_covers — ревью 13.11: без
    подмены три теста из пяти зеленели только 13.11), часы, и предикат «владелец в контейнере»
    (на хосте Studio он истинен, и датчик молча пропустил бы суд)."""
    import integrity_tests as it
    import secrets_paths
    got = []
    monkeypatch.setattr(it, "warn", lambda label, detail="": got.append(label))
    monkeypatch.setattr(it, "today", date.fromisoformat(now[:10]))
    monkeypatch.setattr(secrets_paths, "owner_runs_elsewhere", lambda: False)
    set_test_clock(now)
    try:
        it.check_import_pulses_alive()
    finally:
        clear_test_clock()
    return got


def _run_sensor(monkeypatch, pulses: dict, now: str) -> list[str]:
    import config_db
    monkeypatch.setattr(config_db, "get_config", lambda key, default=None, conn=None:
                        pulses.get(key, default))
    return _judge(monkeypatch, now)


# Дата намеренно далёкая: тест, зелёный только «сегодня», здесь не выживет.
DAY = "2041-05-17"


def test_sensor_quiet_on_fresh_pulses(plists, monkeypatch):
    assert _run_sensor(monkeypatch, {"withings_import.ran_at": f"{DAY}T09:10:40",
                                     "import_watchdog.ran_at": f"{DAY}T09:30:00"},
                       f"{DAY}T10:00") == []


def test_sensor_warns_on_missing_pulses(plists, monkeypatch):
    got = _run_sensor(monkeypatch, {}, f"{DAY}T10:00")
    assert len(got) == 2 and any("Withings" in g for g in got) and any("сторож" in g for g in got)


def test_sensor_warns_on_missed_withings_fire(plists, monkeypatch):
    """Отметка вчерашняя вечерняя, утренний запуск 09:10 прошёл — задача не отметилась."""
    got = _run_sensor(monkeypatch, {"withings_import.ran_at": "2041-05-16T21:10:30",
                                    "import_watchdog.ran_at": f"{DAY}T09:30:00"}, f"{DAY}T10:00")
    assert len(got) == 1 and "Withings" in got[0]


def test_sensor_warns_on_first_missed_watchdog_run(plists, monkeypatch):
    """Интервал 2 ч, отметке 2.5 ч — пропущен запуск. Допуска нет (общее правило 23.09)."""
    got = _run_sensor(monkeypatch, {"withings_import.ran_at": f"{DAY}T09:10:40",
                                    "import_watchdog.ran_at": f"{DAY}T07:30:00"}, f"{DAY}T10:00")
    assert len(got) == 1 and "сторож" in got[0]


def test_sensor_quiet_inside_watchdog_interval(plists, monkeypatch):
    assert _run_sensor(monkeypatch, {"withings_import.ran_at": f"{DAY}T09:10:40",
                                     "import_watchdog.ran_at": f"{DAY}T08:30:00"},
                       f"{DAY}T10:00") == []


def test_sensor_says_unjudged_without_plists(tmp_path, monkeypatch):
    """Нет плиста — «не знаю» звучит тем же каналом, а не тишиной (§14)."""
    monkeypatch.setenv("HEALTH_LAUNCHAGENTS_DIR", str(tmp_path))
    got = _run_sensor(monkeypatch, {}, f"{DAY}T10:00")
    assert len(got) == 2 and all("не судима" in g for g in got)


# ── весь путь: настоящий писатель → настоящая таблица → датчик ─────────────────

@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    return health_db


def test_pulse_written_by_scripts_is_read_by_sensor(db, plists, monkeypatch):
    """Подставлен только внешний мир (свежесть Apple Health, токен Withings); пульс пишут и
    читают настоящие config_db и system_config. Ключ или формат отметки разъедутся — красный."""
    import import_status_db
    import import_watchdog
    import import_withings
    monkeypatch.setattr(import_status_db, "get_import_staleness", lambda src: (False, 1.0))
    monkeypatch.setattr(import_withings, "withings_access_token", lambda d: None)
    assert len(_judge(monkeypatch, f"{DAY}T10:00")) == 2          # до прогонов — оба молчат
    set_test_clock(f"{DAY}T09:40")
    try:
        assert import_watchdog.main() == 0
        assert import_withings.run() == 0
    finally:
        clear_test_clock()
    assert _judge(monkeypatch, f"{DAY}T10:00") == []
