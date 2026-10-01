"""M3 (01.10): досылка брифа после рестарта судит по квитанции ДОСТАВКИ, а не по файлу отчёта.
reports/<день>.md пишется до отправки: рестарт между записью и отправкой терял бриф молча."""
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from jobs import scheduled

DAY = date(2026, 10, 1)


class _JQ:
    def __init__(self):
        self.once = []

    def run_once(self, cb, when, name, data=None):
        self.once.append(name)


class _App:
    def __init__(self):
        self.job_queue = _JQ()


def _setup(monkeypatch, tmp_path, now_hm):
    tz = ZoneInfo("UTC")
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(scheduled, "_resolve_brief_tz", lambda: (tz, "UTC", True))
    monkeypatch.setattr(scheduled, "_brief_time", lambda: time(8, 30))
    monkeypatch.setattr(scheduled, "get_today", lambda: DAY)

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 1, *now_hm, tzinfo=tz)
    monkeypatch.setattr(scheduled, "_dt", _Now)
    d = tmp_path / "data" / "reports"
    d.mkdir(parents=True)
    return d


def test_собран_но_не_доставлен_досылается(monkeypatch, tmp_path):
    d = _setup(monkeypatch, tmp_path, (8, 31))
    (d / f"{DAY}.md").write_text("бриф собран, рестарт до отправки")
    app = _App()
    scheduled._schedule_morning_catchup(app)
    assert app.job_queue.once == ["morning_report_catchup"]


def test_доставлен_не_досылается(monkeypatch, tmp_path):
    d = _setup(monkeypatch, tmp_path, (9, 40))
    (d / f"{DAY}.md").write_text("x")
    (d / f"{DAY}.sent").touch()
    app = _App()
    scheduled._schedule_morning_catchup(app)
    assert app.job_queue.once == []


def test_до_времени_брифа_досылки_нет(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, (8, 29))
    app = _App()
    scheduled._schedule_morning_catchup(app)
    assert app.job_queue.once == []


def test_квитанция_пишется_после_отправки():
    """Порядок в источнике: touch квитанции стоит ПОСЛЕ send_long отчёта."""
    import inspect
    src = inspect.getsource(scheduled.send_morning_report)
    assert 0 < src.index("await send_long(context.bot, chat_id, report)") < src.index("_sent_marker(target).touch()")
