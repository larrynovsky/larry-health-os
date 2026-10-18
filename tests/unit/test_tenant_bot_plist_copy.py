"""Копия плиста бота человека в `launchd/` несёт то, что ставит ему установщик.

`scripts/install.py::render_tenant` с 01.10 кладёт боту человека HEALTH_TRIAGE_LOGS =
<данные>/logs (иначе бриф судится вердиктом владельца). Живой плист партнёра это несёт,
копия в репозитории — нет: ночной датчик `check_launchd_inventory` варнил расхождение
по ключу env:HEALTH_TRIAGE_LOGS (карточка 2026-10-04). Тест читает только файл
репозитория — зелёный не зависит от того, что лежит в ~/Library/LaunchAgents (§20).
"""
from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

LAUNCHD = Path(__file__).resolve().parents[2] / "launchd"


def _tenant_bot_copies():
    return sorted(LAUNCHD.glob("com.larry.health.bot.*.plist"))


def test_tenant_bot_copies_exist():
    assert _tenant_bot_copies(), "нет ни одной копии плиста бота человека в launchd/"


@pytest.mark.parametrize("path", _tenant_bot_copies(), ids=lambda p: p.name)
def test_tenant_bot_copy_carries_triage_logs(path):
    env = plistlib.loads(path.read_bytes()).get("EnvironmentVariables", {})
    data = env.get("HEALTH_DATA_DIR")
    assert data, f"{path.name}: нет HEALTH_DATA_DIR"
    assert env.get("HEALTH_TRIAGE_LOGS") == f"{data}/logs", (
        f"{path.name}: HEALTH_TRIAGE_LOGS должен быть {data}/logs, как ставит install.render_tenant")
