"""Календарь при каталоге ключей только для чтения (нить calendar-token, 02.10).

В Докере каталог ключей смонтирован :ro; обновлённый токен писался туда, запись падала с Errno 30,
синхронизация бросала работу каждый час с 30.09, кэш календаря владельца застыл на двое суток.
Здесь каталог ключей реально без права записи, а Google подменён только на шаге обновления.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.unit

pytest.importorskip("google.oauth2.credentials")


def _token(expired: bool, value: str) -> str:
    expiry = datetime.utcnow() + (timedelta(hours=-1) if expired else timedelta(hours=1))
    return json.dumps({"token": value, "refresh_token": "r-invented", "client_id": "c-invented",
                       "client_secret": "s-invented", "token_uri": "https://oauth2.googleapis.com/token",
                       "scopes": ["https://www.googleapis.com/auth/calendar.readonly"],
                       "expiry": expiry.strftime("%Y-%m-%dT%H:%M:%S.%fZ")})


@pytest.fixture
def stand(tmp_path, monkeypatch):
    import google_calendar_fetcher as g
    from google.oauth2.credentials import Credentials

    sec, data = tmp_path / "sec", tmp_path / "health"
    sec.mkdir()
    (sec / "google_calendar_token.json").write_text(_token(True, "a-old"))
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(sec))
    monkeypatch.setenv("HEALTH_DATA_DIR", str(data))

    def refresh(self, request):
        self.token = "a-new"
        self.expiry = datetime.utcnow() + timedelta(hours=1)

    monkeypatch.setattr(Credentials, "refresh", refresh)
    os.chmod(sec / "google_calendar_token.json", 0o400)
    os.chmod(sec, 0o500)
    yield g, sec, data / "data" / "google_calendar_token.json"
    os.chmod(sec, 0o700)


def test_refresh_works_with_read_only_secrets(stand):
    g, sec, live = stand
    assert not os.access(sec, os.W_OK), "стенд не воспроизводит монтирование только для чтения"
    creds = g._get_credentials()
    assert creds is not None and creds.token == "a-new"
    assert json.loads(live.read_text())["token"] == "a-new"
    assert oct(live.stat().st_mode & 0o777) == "0o600"
    assert json.loads((sec / "google_calendar_token.json").read_text())["token"] == "a-old"


def test_next_run_reads_the_refreshed_token(stand):
    g, _, live = stand
    g._get_credentials()
    assert g._token_source() == live


def test_new_authorization_beats_old_refresh(stand):
    """Повторный --setup пишет в каталог ключей — он новее и обязан победить."""
    g, sec, live = stand
    g._get_credentials()
    os.chmod(sec, 0o700)
    seed = sec / "google_calendar_token.json"
    os.chmod(seed, 0o600)
    seed.write_text(_token(False, "a-reauth"))
    later = time.time() + 5
    os.utime(seed, (later, later))
    assert g._token_source() == seed
    assert g._get_credentials().token == "a-reauth"
