"""OAuth-клиент приложения — общий; токен и аккаунт — тенантские.

Замер 2026-09-02: client_id у владельца и у партнёра ОДИН (проект
<gcp-project-id>) — это одно приложение. Прежде `_client_file()` читал через
`secrets_dir()`, то есть требовал КОПИЮ общего секрета в каталоге каждого тенанта; копия
у партнёра появилась, а у владельца на Studio файла нет вовсе. Реестр классов при этом
описывал бы реализацию вместо замысла.

ЧТО СТЕРЕЖЁТ: границу «что общее, а что чьё». Она тонкая и её легко потерять в обратную
сторону — если однажды `_token_file()` уедет в owner-дом, партнёр начнёт ходить в
календарь владельца, а это кросс-тенант утечка, ради которой вся подсистема и построена.
Поэтому тест пинует ОБЕ стороны, а не одну.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.consistency

ROOT = Path(__file__).resolve().parents[2]


def _paths_under(secrets_dir: str) -> tuple[str, str]:
    """(client, token) в процессе тенанта — как в проде: отдельный процесс с env."""
    code = ("import google_calendar_fetcher as g;"
            "print(g._client_file());print(g._token_file())")
    env = {**os.environ, "HEALTH_SECRETS_DIR": secrets_dir,
           "HEALTH_DATA_DIR": "/tmp/health_tenant_probe"}
    r = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(ROOT),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    client, token = r.stdout.strip().splitlines()
    return client, token


def test_client_is_shared_token_is_per_tenant(tmp_path):
    tenant = str(tmp_path / "tenant_secrets")
    client, token = _paths_under(tenant)
    assert tenant not in client, (
        "клиент приложения обязан жить в ОДНОМ доме: один client_id на оба тенанта "
        "(замер 02.09), а per-tenant путь заставлял размножать общий секрет по каталогам")
    assert client.endswith("/.health_secrets/google_calendar_client.json")
    assert token.startswith(tenant), (
        "токен обязан следовать за тенантом: он говорит, КТО авторизовался. "
        "Уехал в owner-дом → партнёр читает календарь владельца (кросс-тенант утечка)")


def test_registry_agrees_with_the_reader():
    """Реестр классов и код не должны расходиться в понимании «чьё это»."""
    from secrets_paths import SECRET_SCOPE
    assert SECRET_SCOPE["google_calendar_client.json"] == "owner"
    assert SECRET_SCOPE["google_calendar_token.json"] == "tenant"
    assert SECRET_SCOPE["google_calendar_account"] == "tenant"
