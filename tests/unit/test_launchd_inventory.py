"""Инвентарь launchd: копия плиста в репозитории не должна врать молча.

Решение владельца делегировало выбор («реши как выгоднее долгосрочно»), и замер его
принял: в `launchd/` лежало 14 копий, ОДНА уже врала (партнёрский бот без
MORNING_BRIEF_GATE, который в живом плисте стоит с 14.07), а живых health-джоб 38 —
24 без копии вовсе, включая бота владельца, вотчер, триаж и бэкап.

Копию поэтому не добавляем: у неё нет гасителя (§18). Репозиторий получает счётчик.
Ядро сверки чистое — тестируется без launchctl и без плистов на диске (§20: зелёный
причинён тестом, а не тем, что на этой машине случайно лежит нужный файл).
"""
from __future__ import annotations

import pytest

import plist_env_liveness as pl

pytestmark = pytest.mark.unit


def _job(env=None, program="/bin/bash", **extra):
    d = {"Label": "com.larry.health.x", "ProgramArguments": [program]}
    if env is not None:
        d["EnvironmentVariables"] = env
    d.update(extra)
    return d


def test_identical_inventories_are_quiet():
    same = {"a": _job({"HEALTH_DATA_DIR": "/x"})}
    r = pl.compare_inventories(same, {"a": _job({"HEALTH_DATA_DIR": "/x"})})
    assert r == {"drifted": [], "live_only": [], "repo_only": []}


def test_real_drift_is_caught_by_env_key_name():
    """Воспроизведение находки: живой плист несёт MORNING_BRIEF_GATE, копия — нет."""
    repo = {"com.larry.health.bot.partner": _job({"HEALTH_DATA_DIR": "/p"})}
    live = {"com.larry.health.bot.partner": _job({"HEALTH_DATA_DIR": "/p",
                                                  "MORNING_BRIEF_GATE": "1"})}
    r = pl.compare_inventories(repo, live)
    assert r["drifted"] == [("com.larry.health.bot.partner", ["env:MORNING_BRIEF_GATE"])]


def test_secret_values_never_leave_the_comparison():
    """§19: наружу идут ИМЕНА ключей, не значения — путь к секретам это тоже значение."""
    repo = {"a": _job({"HEALTH_SECRETS_DIR": "/Users/zz/.health_secrets"})}
    live = {"a": _job({"HEALTH_SECRETS_DIR": "/Users/zz/.health_secrets_partner"})}
    r = pl.compare_inventories(repo, live)
    flat = repr(r)
    assert "env:HEALTH_SECRETS_DIR" in flat
    assert "health_secrets" not in flat.replace("env:HEALTH_SECRETS_DIR", "")


def test_live_only_and_repo_only_are_separate_answers():
    """Джоба без копии и копия без джобы — разные находки, не одна «расхождение»."""
    r = pl.compare_inventories({"ghost": _job()}, {"fresh": _job()})
    assert r["live_only"] == ["fresh"] and r["repo_only"] == ["ghost"]
    assert r["drifted"] == []


def test_schedule_change_is_caught_too():
    """Расхождение не только по env: сменилось расписание — копия тоже врёт."""
    repo = {"a": _job(StartCalendarInterval={"Hour": 3})}
    live = {"a": _job(StartCalendarInterval={"Hour": 5})}
    assert pl.compare_inventories(repo, live)["drifted"] == [("a", ["StartCalendarInterval"])]


def test_negative_control_comparison_is_alive(monkeypatch):
    """ИСПОЛНЕННЫЙ негативный контроль: глушим сравнение (всё считается равным) —
    позитив обязан покраснеть. Без этого зелёный набор выше неотличим от зелёного на
    сравнении, которое всегда возвращает пустоту (§20).
    """
    repo = {"a": _job({"HEALTH_DATA_DIR": "/x"})}
    live = {"a": _job({"HEALTH_DATA_DIR": "/y"})}
    assert pl.compare_inventories(repo, live)["drifted"], "признак мёртв до глушения"

    real = pl.compare_inventories
    monkeypatch.setattr(pl, "compare_inventories",
                        lambda r, l: {"drifted": [], "live_only": [], "repo_only": []})
    assert not pl.compare_inventories(repo, live)["drifted"], "глушение не подействовало"

    monkeypatch.setattr(pl, "compare_inventories", real)
    assert pl.compare_inventories(repo, live)["drifted"], "признак не восстановился"


def test_off_studio_is_silent(monkeypatch):
    """Вне Studio датчик молчит: у MacBook свой набор джоб, сверять его с этим
    репозиторием значит краснеть на здоровом — и приучать игнорировать датчик."""
    import socket
    monkeypatch.setattr(socket, "gethostname", lambda: "MacBook-Pro.local")
    assert pl.repo_plist_drift() == {"drifted": [], "live_only": [], "repo_only": []}
