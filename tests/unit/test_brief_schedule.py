"""Юнит-тесты этапа 2 morning-brief-timing: время-из-таблицы + tz-из-GPS + backstop.
Мутационные, оба направления (положительный и отрицательный контроль)."""
import asyncio
from datetime import time

import config_db
import location_signal
from jobs import scheduled


class _FakeJob:
    def __init__(self, name):
        self.name = name
        self.removed = False

    def schedule_removal(self):
        self.removed = True


class _FakeJQ:
    def __init__(self):
        self.daily = []      # (name, time)
        self.once = []       # (name, data)
        self.repeating = []  # name
        self.jobs = {}

    def get_jobs_by_name(self, n):
        return self.jobs.get(n, [])

    def run_daily(self, cb, time, name, job_kwargs=None):
        self.daily.append((name, time))
        self.jobs[name] = [_FakeJob(name)]

    def run_once(self, cb, when, name, data=None):
        self.once.append((name, data))

    def run_repeating(self, cb, interval, first, name):
        self.repeating.append(name)


class _FakeApp:
    def __init__(self):
        self.job_queue = _FakeJQ()


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


class _FakeCtx:
    def __init__(self, app):
        self.application = app
        self.bot = _FakeBot()


# ── _brief_time (параметр в ТАБЛИЦЕ) ─────────────────────────────────────────
def test_brief_time_from_config(monkeypatch):
    monkeypatch.setattr(config_db, "get_config", lambda k, d=None: {"hour": 9, "minute": 15})
    assert scheduled._brief_time() == time(9, 15)


def test_brief_time_missing_default(monkeypatch):
    monkeypatch.setattr(config_db, "get_config", lambda k, d=None: None)
    assert scheduled._brief_time() == time(8, 30)


def test_brief_time_garbage_default(monkeypatch):
    monkeypatch.setattr(config_db, "get_config", lambda k, d=None: "08:30")
    assert scheduled._brief_time() == time(8, 30)


def test_start_help_and_registered_jobs_share_schedule_sources(db, monkeypatch):
    from datetime import timezone
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from handlers import meta
    import i18n
    monkeypatch.setattr(i18n, "lang_of", lambda profile=None: "ru")
    monkeypatch.setattr(config_db, "get_config", lambda key, default=None:
        {"hour": 6, "minute": 17} if key == scheduled._BRIEF_TIME_KEY else default)
    monkeypatch.setattr(scheduled, "WEEKLY_REPORT_TIME", time(11, 23))
    monkeypatch.setattr(scheduled, "WEEKLY_REPORT_DAYS", (2,))
    monkeypatch.setattr(scheduled, "MONTHLY_REPORT_TIME", time(12, 34))
    monkeypatch.setattr(scheduled, "MONTHLY_REPORT_DAY", 4)
    monkeypatch.setattr(scheduled, "_tenant_tz", lambda default: timezone.utc)
    monkeypatch.setattr(scheduled, "_resolve_brief_tz", lambda: (timezone.utc, "UTC", True))
    monkeypatch.setattr(scheduled, "_store_active_tz", lambda name: None)
    monkeypatch.setattr(scheduled, "_schedule_morning_catchup", lambda app: None)
    monkeypatch.setattr(scheduled, "wd_send_hour", lambda: 9)
    monkeypatch.setattr(meta, "save_chat_id", lambda cid: None)
    monkeypatch.setattr(meta.db, "init_db", lambda: None)
    monkeypatch.setattr(meta.db, "get_patient_profile", lambda: {"identity.name": "Fictional person"})
    jq = SimpleNamespace(run_daily=Mock(), run_monthly=Mock(), run_once=Mock(),
                         run_repeating=Mock(), get_jobs_by_name=lambda name: [])
    scheduled.register(SimpleNamespace(job_queue=jq))
    # Пульс бота в контейнере (docker-install, этап 2б): без этой задачи датчик увидел бы
    # «пульса нет ни разу» у живого бота. Проверка здесь — единственный шов, зовущий register целиком.
    pulse = [c for c in jq.run_repeating.call_args_list if c.kwargs.get("name") == "service_pulse"]
    assert pulse
    # очередь PTB делает await результата: не-корутина = ERROR в журнале каждую минуту (30.09)
    import inspect
    assert inspect.iscoroutinefunction(pulse[0].args[0])
    # и шире — ЛЮБОЙ колбэк register(): следующая синхронная задача упадёт так же
    for m in (jq.run_daily, jq.run_monthly, jq.run_once, jq.run_repeating):
        for c in m.call_args_list:
            cb = c.args[0] if c.args else c.kwargs.get("callback")
            assert inspect.iscoroutinefunction(cb), \
                f"{c.kwargs.get('name')}: колбэк очереди задач не корутина"
    daily = {call.kwargs["name"]: call.kwargs for call in jq.run_daily.call_args_list}
    monthly = {call.kwargs["name"]: call.kwargs for call in jq.run_monthly.call_args_list}
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=7))
    asyncio.run(meta.cmd_start(update, None))
    welcome = message.reply_text.await_args.args[0]
    asyncio.run(meta.cmd_help(update, None))
    help_text = message.reply_text.await_args.kwargs["text"]
    assert daily["morning_report"]["time"].strftime("%H:%M") == "06:17"
    assert "06:17" in welcome and "06:17" in help_text
    assert daily["weekly_report"]["time"].strftime("%H:%M") == "11:23"
    assert daily["weekly_report"]["days"] == (2,)
    assert "вторник, 11:23" in help_text
    assert monthly["monthly_check"]["when"].strftime("%H:%M") == "12:34"
    assert monthly["monthly_check"]["day"] == 4
    assert "4-го числа, 12:34" in help_text and "UTC" in help_text


# ── _resolve_brief_tz (GPS → tz, UTC=провал, last-good backstop) ─────────────
def test_resolve_tz_ok(monkeypatch):
    monkeypatch.setattr(location_signal, "tenant_timezone", lambda: "Asia/Tokyo")
    _tz, name, ok = scheduled._resolve_brief_tz()
    assert name == "Asia/Tokyo" and ok is True


def test_resolve_tz_utc_is_failure_uses_lastgood(monkeypatch):
    # UTC от резолвера = провал (timezonefinder выпал) → берём last-good, ok=False
    monkeypatch.setattr(location_signal, "tenant_timezone", lambda: "UTC")
    monkeypatch.setattr(config_db, "get_config",
                        lambda k, d=None: "Europe/Prague" if k == scheduled._ACTIVE_TZ_KEY else None)
    _tz, name, ok = scheduled._resolve_brief_tz()
    assert name == "Europe/Prague" and ok is False


def test_resolve_tz_exception_uses_env(monkeypatch):
    def _boom():
        raise RuntimeError("gps down")
    monkeypatch.setattr(location_signal, "tenant_timezone", _boom)
    monkeypatch.setattr(config_db, "get_config", lambda k, d=None: None)
    monkeypatch.setenv("HEALTH_TZ", "Europe/Prague")
    _tz, name, ok = scheduled._resolve_brief_tz()
    assert name == "Europe/Prague" and ok is False


# ── _apply_morning_schedule (идемпотентная перерегистрация + сенсор) ─────────
def test_apply_registers_and_removes_prior(monkeypatch):
    monkeypatch.setattr(location_signal, "tenant_timezone", lambda: "Asia/Tokyo")
    monkeypatch.setattr(config_db, "get_config", lambda k, d=None: {"hour": 8, "minute": 30})
    monkeypatch.setattr(config_db, "upsert_config", lambda *a, **k: None)
    app = _FakeApp()
    prior = _FakeJob("morning_report")
    app.job_queue.jobs["morning_report"] = [prior]
    name = scheduled._apply_morning_schedule(app)
    assert name == "Asia/Tokyo"
    assert prior.removed is True
    assert app.job_queue.daily[-1][0] == "morning_report"
    assert str(app.job_queue.daily[-1][1].tzinfo) == "Asia/Tokyo"
    assert app.job_queue.daily[-1][1].hour == 8


def test_apply_fallback_raises_sensor(monkeypatch):
    monkeypatch.setattr(location_signal, "tenant_timezone", lambda: "UTC")
    monkeypatch.setattr(config_db, "get_config",
                        lambda k, d=None: "Europe/Prague" if k == scheduled._ACTIVE_TZ_KEY else None)
    app = _FakeApp()
    scheduled._apply_morning_schedule(app)
    assert "tz_fallback_alert" in [n for n, _ in app.job_queue.once]


# ── _reschedule_local (переезд → переставить; тот же пояс → no-op) ───────────
def test_reschedule_triggers_on_change(monkeypatch):
    monkeypatch.setattr(location_signal, "tenant_timezone", lambda: "Asia/Tokyo")
    monkeypatch.setattr(config_db, "get_config",
                        lambda k, d=None: "Europe/Prague" if k == scheduled._ACTIVE_TZ_KEY
                        else {"hour": 8, "minute": 30})
    monkeypatch.setattr(config_db, "upsert_config", lambda *a, **k: None)
    monkeypatch.setattr(scheduled, "get_chat_id", lambda: 123)
    app = _FakeApp()
    ctx = _FakeCtx(app)
    asyncio.run(scheduled._reschedule_local(ctx))
    assert any(n == "morning_report" for n, _ in app.job_queue.daily)
    assert ctx.bot.sent  # уведомление о смене пояса доставлено


def test_reschedule_noop_same_tz(monkeypatch):
    monkeypatch.setattr(location_signal, "tenant_timezone", lambda: "Asia/Tokyo")
    monkeypatch.setattr(config_db, "get_config", lambda k, d=None: "Asia/Tokyo")
    monkeypatch.setattr(scheduled, "get_chat_id", lambda: 123)
    app = _FakeApp()
    ctx = _FakeCtx(app)
    asyncio.run(scheduled._reschedule_local(ctx))
    assert not app.job_queue.daily  # без смены пояса не перерегистрируем


# ── Durable-фикс (2026-07-21): доставленный бриф САМ перевзводит на свежий tz ──
def test_send_morning_report_rearms_with_fresh_tz(monkeypatch, tmp_path):
    """После доставки брифа расписание на завтра перевзводится со СВЕЖИМ GPS-tz.
    RED: fake job_queue должен получить morning_report со свежим Europe/Prague.
    Смена пояса в этом сценарии независимо придумана."""
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    import location_signal, config_db
    from jobs import scheduled as sc

    monkeypatch.setattr(location_signal, "tenant_timezone", lambda: "Europe/Prague")
    monkeypatch.setattr(config_db, "get_config",
                        lambda k, d=None: {"hour": 8, "minute": 30} if "morning_brief" in k else "Asia/Tokyo")
    monkeypatch.setattr(config_db, "upsert_config", lambda *a, **k: None)
    monkeypatch.setattr(sc, "get_chat_id", lambda: 123)
    monkeypatch.setattr(sc.gp, "generate_daily_report", lambda target, sink: ("отчёт-стаб", {}))
    # Обновление данных перед брифом запускает НАСТОЯЩИЕ импортёры отдельными процессами (Oura,
    # Apple Health) — до 24.09 этот тест ходил в API Oura (conftest._no_real_importers).
    monkeypatch.setattr(sc, "refresh_data", lambda: None)

    async def _noop_send_long(bot, chat_id, text, **kw):
        return None
    monkeypatch.setattr(sc, "send_long", _noop_send_long)

    class _BoomDB:  # OURA-measure внутри try/except — БД в юните не нужна
        def get_conn(self):
            raise RuntimeError("no db in unit")
    monkeypatch.setattr(sc, "db", _BoomDB())

    class _Bot:
        async def send_chat_action(self, **kw):
            return None
        async def send_message(self, **kw):
            return None
    app = _FakeApp()
    ctx = _FakeCtx(app)
    ctx.bot = _Bot()

    asyncio.run(sc.send_morning_report(ctx))

    daily = dict(app.job_queue.daily)
    assert "morning_report" in daily, "доставка не перевзвела расписание"
    assert "Prague" in str(daily["morning_report"].tzinfo), \
        f"перевзвод впёк не свежий пояс: {daily['morning_report'].tzinfo}"


def test_reschedule_first_pulse_survives_deploy_restarts():
    """Пульс живости reschedule_local обязан случиться раньше, чем следующий деплой
    перезапустит бота (2026-09-01: 12-часовой first ни разу не дожил в день с 14
    коммитами → ложный «партнёр молчит >26ч»). Первый запуск — минуты, не часы."""
    assert scheduled._RESCHED_FIRST_S <= 600
    assert scheduled._RESCHED_FIRST_S < scheduled._RESCHED_INTERVAL_S
