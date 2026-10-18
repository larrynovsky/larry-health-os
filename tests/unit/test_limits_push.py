"""Пол безопасности: без повторов после перезапуска, личный порог — не сообщением (18.10).

18.10 одна тревога о личном пороге сна пришла шесть раз за час: «уже сказано сегодня»
жило в памяти бота, а каждый деплой пересоздавал контейнер. Решение владельца того же дня
(вариант «а»): личный порог (свои нижние 10%) сообщением не идёт, клинический — идёт.
Оракулы краснеют, если вернуть отметку в bot_data или убрать фильтр личного порога.
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


def _run_job(bot_data, limits=None):
    from jobs.scheduled import check_recommendations_scheduled
    bot = MagicMock()
    bot.send_message = AsyncMock()
    ctx = SimpleNamespace(bot=bot, bot_data=bot_data, job=None)
    fake_now = SimpleNamespace(hour=14, date=lambda: date.today())
    extra = patch("jobs.scheduled.evaluate_safety_limits", return_value=limits) if limits \
        else patch("jobs.scheduled.log")   # без подмены пола — настоящий путь из базы
    with patch("jobs.scheduled.get_chat_id", return_value=42), extra, \
         patch("jobs.scheduled._dt") as dt_mock:
        dt_mock.now.return_value = fake_now
        asyncio.run(check_recommendations_scheduled(ctx))
    return bot.send_message


def _send_on():
    import config_db
    from services import recommendations as reco
    config_db.upsert_config(reco.LIMITS_SEND_KEY, value_num=1, category="recommendations")


def test_restart_does_not_repeat_todays_alarm(db):
    with db.get_conn() as c:
        ceil = c.execute("SELECT value FROM absolute_thresholds WHERE metric='bp_systolic_max' "
                         "AND direction='ceiling' AND COALESCE(variant,'')=''").fetchone()[0]
        c.execute("INSERT INTO daily_metrics(date, raw) VALUES (?, ?)",
                  (str(date.today() - timedelta(days=1)), json.dumps({"bp_systolic_max": ceil + 10})))
        c.commit()
    _send_on()
    assert _run_job({}).call_count == 1, "пик давления не дошёл"
    # перезапуск бота = новая пустая память процесса
    assert _run_job({}).call_count == 0, "после перезапуска та же тревога пришла снова"


def test_personal_floor_stays_quiet_clinical_goes(db):
    _send_on()
    limits = {"fired": [
        {"metric": "hrv", "reason": "ЛИЧНЫЙ-ПОЛ", "push": False},
        {"metric": "bp_systolic_max", "reason": "КЛИНИЧЕСКИЙ-ПОТОЛОК", "push": True},
    ]}
    send = _run_job({}, limits)
    assert send.call_count == 1
    text = send.call_args.kwargs["text"]
    assert "КЛИНИЧЕСКИЙ-ПОТОЛОК" in text and "ЛИЧНЫЙ-ПОЛ" not in text


def test_only_personal_kind_is_held_back():
    from services.recommendations import _pushed
    assert _pushed({"norm_kind": "personal"}) is False
    assert _pushed({"norm_kind": "decision_threshold"}) is True
    assert _pushed({}) is True, "строка без вида обязана доходить (fail-safe), а не глушиться"


def test_leaky_text_scrubbed_after_personalization(db):
    import i18n
    with db.get_conn() as c:
        c.execute("UPDATE absolute_thresholds SET source='p10_personal', reason_template=? "
                  "WHERE metric='hrv' AND direction='floor' AND kind='absolute' "
                  "AND COALESCE(variant,'')=''", ("ВСР {val:.0f} мс — ниже порога N (устаревший ярлык)",))
        c.commit()
    db._scrub_leaky_seed_reasons()
    with db.get_conn() as c:
        txt = c.execute("SELECT reason_template FROM absolute_thresholds WHERE metric='hrv' "
                        "AND direction='floor' AND kind='absolute' AND COALESCE(variant,'')=''"
                        ).fetchone()[0]
    assert txt == i18n.t("health_db.reason.hrv_low", "ru"), txt
