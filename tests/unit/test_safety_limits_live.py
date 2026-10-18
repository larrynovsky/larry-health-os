"""Пол безопасности исполняет ЗАДАЧА, а не только функция (нить floors-live, 07.10).

До нити абсолютные пороги жили в evaluate_domain_need; задача звала её только для доменов
активных протоколов, домен пуст у всех протоколов обоих тенантов → потолок давления 140 (решение
владельца 26.09) и все полы ни разу не проверялись вживую, а тесты функции зеленели.
Здесь путь целиком: строка дня в настоящей базе → задача → сообщение; внешний мир — только бот
и chat_id. Протоколов с доменом нет — как в бою.
"""
import asyncio
import json
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setenv("ALLOW_WRITE_NONPRIMARY", "1")
    (tmp_path / "health" / "data").mkdir(parents=True)
    import health_db
    monkeypatch.setattr(health_db, "DB_PATH", tmp_path / "health" / "data" / "health.db")
    health_db.init_db()
    return health_db


def _yesterday_peak(db, systolic):
    day = str(date.today() - timedelta(days=1))
    with db.get_conn() as c:
        c.execute("INSERT INTO daily_metrics(date, raw) VALUES (?, ?)",
                  (day, json.dumps({"bp_systolic_max": systolic})))
        c.commit()


def _run_job(bot_data):
    from jobs.scheduled import check_recommendations_scheduled
    bot = MagicMock()
    bot.send_message = AsyncMock()
    ctx = SimpleNamespace(bot=bot, bot_data=bot_data, job=None)
    fake_now = SimpleNamespace(hour=14, date=lambda: date.today())
    with patch("jobs.scheduled.get_chat_id", return_value=42), \
         patch("jobs.scheduled._dt") as dt_mock:
        dt_mock.now.return_value = fake_now
        asyncio.run(check_recommendations_scheduled(ctx))
    return bot.send_message


def test_bp_peak_reaches_owner_without_any_protocol_domain(db):
    with db.get_conn() as c:
        assert not c.execute("SELECT 1 FROM protocols WHERE domain IS NOT NULL").fetchone()
        ceil = c.execute("SELECT value FROM absolute_thresholds WHERE metric='bp_systolic_max' "
                         "AND direction='ceiling' AND active=1 AND COALESCE(variant,'')=''").fetchone()
    assert ceil, "сид потолка давления пропал — тест судил бы пустоту"
    _yesterday_peak(db, ceil[0] + 10)
    import config_db
    from services import recommendations as reco
    assert _run_job({}).call_count == 0, "доставка без включения у тенанта"
    config_db.upsert_config(reco.LIMITS_SEND_KEY, value_num=1, category="recommendations")
    bot_data = {}
    send = _run_job(bot_data)
    assert send.call_count == 1, "пик давления выше потолка не дошёл до человека"
    assert str(int(ceil[0] + 10)) in send.call_args.kwargs["text"]
    # второй прогон в тот же день молчит: задача бежит раз в 2 часа
    assert _run_job(bot_data).call_count == 0, "одна тревога повторяется каждые 2 часа"


def test_pulse_written_and_quiet_day_sends_nothing(db):
    import config_db
    from services import recommendations as reco
    assert _run_job({}).call_count == 0
    at = config_db.get_config(reco.LIMITS_CHECKED_KEY)
    assert at and "+00:00" in str(at), "пульс пола не записан (или без пояса) — ночная проверка ослепнет"



def test_stale_pulse_is_named_fresh_is_not(db):
    """Судья ночной проверки: пульс старше паузы расписания — молчит; свежий — нет; нет — молчит."""
    from datetime import datetime, timedelta, timezone
    import config_db
    from services import recommendations as reco
    now = datetime(2026, 10, 8, 7, 50, tzinfo=timezone.utc)
    with db.get_conn() as c:
        assert reco.limits_stale(c, now), "пол, не бежавший ни разу, назван живым"
    lim = reco.limits_max_silence_s()
    for age, stale in ((lim - 60, False), (lim + 60, True)):
        config_db.upsert_config(reco.LIMITS_CHECKED_KEY,
                                value_text=(now - timedelta(seconds=age)).isoformat())
        with db.get_conn() as c:
            assert reco.limits_stale(c, now) is stale, f"возраст {age} с судится неверно"
