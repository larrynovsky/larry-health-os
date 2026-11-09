"""M3 (01.10): досылка брифа после рестарта судит по квитанции ДОСТАВКИ, а не по файлу отчёта.
reports/<день>.md пишется до отправки: рестарт между записью и отправкой терял бриф молча."""
import asyncio
import types

import pytest
from telegram.error import BadRequest, TimedOut

import config_db
import triage_agent as _tri

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
    monkeypatch.setattr(scheduled, "get_now", _Now.now)
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


# ── Повтор при сетевом сбое Telegram (нить brief-net-retry, 28.10) ──────────────
# Инцидент 28.10: ConnectTimeout на «печатает…» в 08:30 — задача упала, брифа не было весь
# день, в журнал сбоев ничего. Подменён только внешний мир (Telegram, модель, хранилище
# конфига); своя цепочка morning_report_job → send_morning_report → .sent — настоящая (C-138).


class _Bot:
    def __init__(self, typing_fails=False):
        self.typing_fails = typing_fails

    async def send_chat_action(self, chat_id, action):
        if self.typing_fails:
            raise TimedOut("ConnectTimeout")

    async def send_message(self, *a, **k):
        return None


class _RetryJQ:
    def __init__(self):
        self.once = []

    def run_once(self, cb, when, name, data=None):
        self.once.append({"cb": cb, "when": when, "name": name, "data": data})


def _wire(monkeypatch, tmp_path, send_fails=0, exc=TimedOut):
    """Бриф целиком, кроме внешнего мира. send_fails — сколько первых send_long падают."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(scheduled, "get_today", lambda: DAY)
    monkeypatch.setattr(scheduled, "get_chat_id", lambda: 111222333)
    monkeypatch.setattr(scheduled, "refresh_data", lambda *a, **k: None)
    monkeypatch.setattr(_tri, "latest_verdict", lambda require_today=False: ({}, ""))
    built, sent, store = [], [], {}

    def fake_report(target, sink=None):
        built.append(target)
        return "текст отчёта", None
    monkeypatch.setattr(scheduled.gp, "generate_daily_report", fake_report)

    left = [send_fails]

    async def fake_send_long(bot, chat_id, text, **kw):
        if left[0] > 0:
            left[0] -= 1
            raise exc("сеть")
        sent.append(text)
    monkeypatch.setattr(scheduled, "send_long", fake_send_long)
    monkeypatch.setattr(config_db, "get_config", lambda k, d=None, conn=None: store.get(k, d))
    monkeypatch.setattr(config_db, "upsert_config",
                        lambda k, value_json=None, **kw: store.__setitem__(k, value_json))
    (tmp_path / "data" / "reports").mkdir(parents=True, exist_ok=True)
    return built, sent, store


def _ctx(bot, jq, attempt=None):
    job = types.SimpleNamespace(data={"attempt": attempt} if attempt else None)
    return types.SimpleNamespace(bot=bot, job=job, job_queue=jq)


def _sent(tmp_path):
    return (tmp_path / "data" / "reports" / f"{DAY}.sent").exists()


def test_сбой_индикатора_печатает_не_отменяет_бриф(monkeypatch, tmp_path, fault_journal):
    built, sent, _ = _wire(monkeypatch, tmp_path)
    jq = _RetryJQ()
    asyncio.run(scheduled.morning_report_job(_ctx(_Bot(typing_fails=True), jq)))
    assert sent == ["текст отчёта"], "сбой косметического индикатора отменил бриф (инцидент 28.10)"
    assert _sent(tmp_path) and jq.once == []


def test_сетевой_сбой_ставит_повтор_и_повтор_доставляет(monkeypatch, tmp_path, fault_journal):
    built, sent, store = _wire(monkeypatch, tmp_path, send_fails=1)
    jq = _RetryJQ()
    asyncio.run(scheduled.morning_report_job(_ctx(_Bot(), jq)))
    assert sent == [] and not _sent(tmp_path)
    assert len(jq.once) == 1, "сетевой сбой до доставки обязан ставить повтор, а не терять бриф"
    r = jq.once[0]
    assert r["cb"] is scheduled.morning_report_job and r["data"] == {"attempt": 1}
    assert r["when"] == scheduled.BRIEF_RETRY_DELAYS_S[0]
    assert store[scheduled.BRIEF_NET_RETRY_KEY]["last"]["outcome"] == "retrying"

    asyncio.run(scheduled.morning_report_job(_ctx(_Bot(), jq, attempt=1)))
    assert sent == ["текст отчёта"] and _sent(tmp_path)
    v = store[scheduled.BRIEF_NET_RETRY_KEY]
    assert v["last"]["outcome"] == "delivered"
    assert v["first_delivered"] == str(DAY), "машинный признак «работает в жизни» не записан"


def test_доставленный_бриф_повтор_не_шлёт(monkeypatch, tmp_path, fault_journal):
    built, sent, _ = _wire(monkeypatch, tmp_path)
    (tmp_path / "data" / "reports" / f"{DAY}.sent").touch()
    jq = _RetryJQ()
    asyncio.run(scheduled.morning_report_job(_ctx(_Bot(), jq, attempt=2)))
    assert built == [] and sent == [], "повтор после доставки задвоил бриф"


def test_исчерпанные_повторы_уходят_в_журнал_сбоев(monkeypatch, tmp_path, fault_journal):
    import json
    n = len(scheduled.BRIEF_RETRY_DELAYS_S)
    built, sent, store = _wire(monkeypatch, tmp_path, send_fails=1)
    jq = _RetryJQ()
    asyncio.run(scheduled.morning_report_job(_ctx(_Bot(), jq, attempt=n)))
    assert jq.once == [], "повторы обязаны кончаться"
    recs = [json.loads(x) for x in fault_journal.read_text().splitlines()]
    assert any("morning_report_job" in r["text"] for r in recs), "исчерпанный повтор молчит"
    assert store[scheduled.BRIEF_NET_RETRY_KEY]["last"]["outcome"] == "exhausted"
    assert "first_delivered" not in store[scheduled.BRIEF_NET_RETRY_KEY]


def test_ошибка_разметки_не_повторяется(monkeypatch, tmp_path, fault_journal):
    """BadRequest в PTB 22.7 — подкласс NetworkError; повтор её не лечит и прятал бы поломку."""
    _wire(monkeypatch, tmp_path, send_fails=1, exc=BadRequest)
    jq = _RetryJQ()
    with pytest.raises(BadRequest):
        asyncio.run(scheduled.morning_report_job(_ctx(_Bot(), jq)))
    assert jq.once == []


def test_расписание_и_досылка_зовут_задачу_с_повтором():
    """Регистрация через обёртку — иначе повтор есть в коде, но не в жизни."""
    import inspect
    src = inspect.getsource(scheduled._apply_morning_schedule)
    assert "morning_report_job," in src and "send_morning_report," not in src
    from unittest.mock import patch
    app = _App()
    with patch.object(scheduled, "_report_slot", return_value=(
        datetime(2026, 10, 1, 9), datetime(2026, 10, 1, 8),
        types.SimpleNamespace(exists=lambda: False, name="synthetic.sent"),
    )), patch.object(app.job_queue, "run_once") as once:
        scheduled._schedule_morning_catchup(app)
    assert once.call_args.args == (scheduled.morning_report_job,)


# Synthetic dates and reports; no model calls, patient records or Telegram traffic.
@pytest.mark.parametrize("kind,stamp,filename,target", [
    ("weekly", "2027-01-03T08:00", "weekly-2026-W53.sent", date(2027, 1, 2)),
    ("monthly", "2026-11-08T12:00", "monthly-2026-11.sent", date(2026, 10, 31)),
    ("weekly", "2026-11-09T10:00", "weekly-2026-W45.sent", date(2026, 11, 7)),
    ("weekly", "2026-11-14T23:00", "weekly-2026-W45.sent", date(2026, 11, 7)),
    ("monthly", "2026-11-15T12:00", "monthly-2026-11.sent", date(2026, 10, 31)),
    ("weekly", "2027-01-04T10:00", "weekly-2026-W53.sent", date(2027, 1, 2)),
    ("monthly", "2027-01-01T08:00", "monthly-2026-12.sent", date(2026, 11, 30)),
])
def test_periodic_catchup_delivery_once(monkeypatch, tmp_path, kind, stamp, filename, target):
    from unittest.mock import AsyncMock
    from telegram.error import TelegramError
    from _time_inject import set_test_clock

    set_test_clock(stamp)
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HEALTH_TZ", "UTC")
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    older = "weekly-2025-W01.sent" if kind == "weekly" else "monthly-2025-01.sent"
    (reports / older).touch()  # Receipt mechanism already initialized for this kind.
    monkeypatch.setattr(scheduled, "get_chat_id", lambda: 111222333)
    monkeypatch.setattr(scheduled, "refresh_data", lambda: None)
    monkeypatch.setattr(scheduled.hai, "get_active_protocols", lambda: [])
    monkeypatch.setattr(scheduled, "_send_tasks_from_report", AsyncMock())
    built, delivered = [], []

    def generate(day, *args):
        built.append(day)
        return "synthetic report"

    monkeypatch.setattr(scheduled.gp, f"generate_{kind}_report", generate)

    async def send(bot, chat_id, text):
        assert not (tmp_path / "data" / "reports" / filename).exists()
        await asyncio.sleep(0)  # overlap cron and catch-up while delivery is pending
        delivered.append(text)

    monkeypatch.setattr(scheduled, "send_long", send)
    bot = types.SimpleNamespace(send_chat_action=AsyncMock(side_effect=TelegramError("typing")))
    ctx = types.SimpleNamespace(bot=bot, bot_data={})
    callback = scheduled.send_weekly_report if kind == "weekly" else scheduled.send_monthly_check
    app = types.SimpleNamespace(job_queue=_RetryJQ())
    scheduled._schedule_report_catchup(app, kind, callback, kind)
    assert len(app.job_queue.once) == 1

    async def run():
        await asyncio.gather(app.job_queue.once[0]["cb"](ctx), callback(ctx))
        # A new process has empty bot_data but the same durable receipt.
        await callback(types.SimpleNamespace(bot=bot, bot_data={}))

    asyncio.run(run())
    assert built == [target] and len(delivered) == 1
    assert (tmp_path / "data" / "reports" / filename).exists()
    scheduled._schedule_report_catchup(app, kind, callback, kind)
    assert len(app.job_queue.once) == 1


@pytest.mark.parametrize("kind,stamp,delivered", [
    ("weekly", "2026-11-02T12:00", "weekly-2026-W44.sent"),
    ("weekly", "2026-11-08T06:59", "weekly-2026-W44.sent"),
    ("weekly", "2026-11-08T06:00", "weekly-2026-W44.sent"),
    ("monthly", "2026-11-01T09:29", "monthly-2026-10.sent"),
    ("monthly", "2026-11-01T08:00", "monthly-2026-10.sent"),
])
def test_periodic_catchup_not_before_slot(monkeypatch, tmp_path, kind, stamp, delivered):
    from unittest.mock import AsyncMock
    from _time_inject import set_test_clock

    set_test_clock(stamp)
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HEALTH_TZ", "UTC")
    monkeypatch.setattr(scheduled, "get_chat_id", lambda: 111222333)
    receipt = tmp_path / "data" / "reports" / delivered
    receipt.parent.mkdir(parents=True)
    receipt.touch()  # Previous slot delivered; the next slot must not be sent early.
    bot = types.SimpleNamespace(send_chat_action=AsyncMock())
    callback = scheduled.send_weekly_report if kind == "weekly" else scheduled.send_monthly_check
    app = _App()
    scheduled._schedule_report_catchup(app, kind, callback, kind)
    assert app.job_queue.once == []
    asyncio.run(callback(types.SimpleNamespace(bot=bot, bot_data={})))
    bot.send_chat_action.assert_not_awaited()
    assert list(tmp_path.rglob("*.sent")) == [receipt]


@pytest.mark.parametrize("failure", ["generation", "delivery"])
def test_periodic_failure_has_no_delivery_receipt(monkeypatch, tmp_path, failure):
    from unittest.mock import AsyncMock, Mock
    from _time_inject import set_test_clock

    set_test_clock("2026-11-01T10:00")
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HEALTH_TZ", "UTC")
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    older = reports / "monthly-2025-01.sent"
    older.touch()
    monkeypatch.setattr(scheduled, "get_chat_id", lambda: 111222333)
    monkeypatch.setattr(scheduled, "refresh_data", lambda: None)
    generate = Mock(return_value="synthetic report")
    send = AsyncMock()
    if failure == "generation":
        generate.side_effect = RuntimeError("synthetic generation failure")
    else:
        send.side_effect = TimedOut("synthetic delivery failure")
    monkeypatch.setattr(scheduled.gp, "generate_monthly_report", generate)
    monkeypatch.setattr(scheduled, "send_long", send)
    monkeypatch.setattr(scheduled.notify, "afault", AsyncMock(return_value="failure notice"))
    ctx = types.SimpleNamespace(bot=_Bot(), bot_data={})
    if failure == "delivery":
        with pytest.raises(TimedOut):
            asyncio.run(scheduled.send_monthly_check(ctx))
    else:
        asyncio.run(scheduled.send_monthly_check(ctx))
    assert list(tmp_path.rglob("*.sent")) == [older]
    app = _App()
    scheduled._schedule_report_catchup(app, "monthly", scheduled.send_monthly_check, "monthly_check")
    assert app.job_queue.once == ["monthly_check_catchup"]


def test_startup_registers_periodic_catchups(monkeypatch, tmp_path):
    from unittest.mock import Mock
    from _time_inject import set_test_clock

    set_test_clock("2026-11-01T12:00")
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HEALTH_TZ", "UTC")
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    (reports / "weekly-2025-W01.sent").touch()
    (reports / "monthly-2025-01.sent").touch()
    monkeypatch.setattr(scheduled, "_apply_morning_schedule", lambda app: None)
    monkeypatch.setattr(scheduled, "_resolve_brief_tz", lambda: (ZoneInfo("UTC"), "UTC", True))
    monkeypatch.setattr(scheduled, "_brief_time", lambda: time(8, 30))
    monkeypatch.setattr(scheduled, "wd_send_hour", lambda: 9)
    jq = Mock()
    scheduled.register(types.SimpleNamespace(job_queue=jq))
    once = {c.kwargs["name"]: c.args[0] for c in jq.run_once.call_args_list}
    assert once["weekly_report_catchup"] is scheduled.send_weekly_report
    assert once["monthly_check_catchup"] is scheduled.send_monthly_check


@pytest.mark.parametrize("kind,stamp,filename", [
    ("weekly", "2026-11-08T07:00", "weekly-2026-W45.sent"),
    ("monthly", "2027-01-01T09:30", "monthly-2027-01.sent"),
])
def test_periodic_cron_uses_new_slot_at_scheduled_time(monkeypatch, tmp_path, kind, stamp, filename):
    from _time_inject import set_test_clock

    set_test_clock(stamp)
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HEALTH_TZ", "UTC")
    now, slot, receipt = scheduled._report_slot(kind)
    assert slot == now == datetime.fromisoformat(stamp).replace(tzinfo=ZoneInfo("UTC"))
    assert receipt == tmp_path / "data" / "reports" / filename


@pytest.mark.parametrize("kind,filename", [
    ("weekly", "weekly-2026-W45.sent"),
    ("monthly", "monthly-2026-11.sent"),
])
@pytest.mark.parametrize("other_kind_present", [False, True])
def test_periodic_first_start_seeds_receipt_without_sending(
    monkeypatch, tmp_path, caplog, kind, filename, other_kind_present,
):
    from _time_inject import set_test_clock

    set_test_clock("2026-11-08T10:00")
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HEALTH_TZ", "UTC")
    reports = tmp_path / "data" / "reports"
    if other_kind_present:
        reports.mkdir(parents=True)
        other = "monthly-2026-10.sent" if kind == "weekly" else "weekly-2026-W44.sent"
        (reports / other).touch()
    callback = scheduled.send_weekly_report if kind == "weekly" else scheduled.send_monthly_check
    with caplog.at_level("INFO", logger=scheduled.log.name):
        for _ in range(2):
            app = _App()  # new startup, same durable receipts
            scheduled._schedule_report_catchup(app, kind, callback, kind)
            assert app.job_queue.once == []
            assert (reports / filename).is_file()
    assert len(caplog.records) == 1
    assert filename in caplog.records[0].message
