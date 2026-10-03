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


def _setup_stand(tmp_path, monkeypatch, capsys, query="state={state}&code=invented-code"):
    """Real InstalledAppFlow/PKCE/credentials; only browser input and token HTTP are replaced."""
    from urllib.parse import parse_qs, urlsplit
    from requests_oauthlib import OAuth2Session
    import google_calendar_fetcher as g

    owner, tenant = tmp_path / "owner", tmp_path / "tenant-keys"
    (owner / ".health_secrets").mkdir(parents=True)
    tenant.mkdir()
    monkeypatch.setattr(g.Path, "home", classmethod(lambda cls: owner))
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(tenant))
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health-tenant"))
    monkeypatch.setenv("HEALTH_RUNTIME", "container")
    g._client_file().write_text(json.dumps({"installed": {
        "client_id": "invented-client", "client_secret": "invented-secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"]}}))
    assert g._client_file().parent != tenant, "owner client must not resolve through tenant keys"
    exchanges, authorization = [], {}

    def paste(prompt):
        output = capsys.readouterr().out
        url = next(line for line in output.splitlines() if line.startswith("https://"))
        authorization.update(parse_qs(urlsplit(url).query))
        callback = query.format(state=authorization["state"][0])
        return callback if callback.startswith("http") else g.MANUAL_REDIRECT_URI + "?" + callback

    def exchange(session, token_url, **kwargs):
        exchanges.append(kwargs)
        token = dict(access_token="invented-access", refresh_token="invented-refresh",
                     token_type="Bearer", expires_at=time.time() + 3600)
        session.token = token
        return token

    monkeypatch.setattr("getpass.getpass", paste)
    monkeypatch.setattr(OAuth2Session, "fetch_token", exchange)
    return g, g._live_token_file(), g._token_file(), exchanges, authorization


@pytest.mark.parametrize("query", [
    "state=foreign-state&code=invented-code", "code=invented-code",
    "state={state}&state={state}&code=invented-code",
    "state={state}&code=a&code=b", "state={state}&code=",
    "state={state}&error=access_denied&code=a", "state={state}&code=a#fragment",
    "https://localhost:9877/?state={state}&code=a",
    "http://example.com:9877/?state={state}&code=a",
    "http://localhost:9876/?state={state}&code=a",
    "http://localhost:9877/wrong?state={state}&code=a",
])
def test_setup_rejects_invalid_callback_before_exchange(tmp_path, monkeypatch, capsys, query):
    g, live, seed, exchanges, _ = _setup_stand(tmp_path, monkeypatch, capsys, query)
    with pytest.raises(ValueError):
        g.setup_auth()
    assert exchanges == []
    assert not live.exists() and not seed.exists()


def test_setup_saves_data_token_before_calendar_use(tmp_path, monkeypatch, capsys):
    from unittest.mock import Mock
    import hashlib
    import base64

    g, live, seed, exchanges, authorization = _setup_stand(tmp_path, monkeypatch, capsys)
    os.chmod(seed.parent, 0o500)
    try:
        g.setup_auth()  # HEALTH_RUNTIME=container must select manual without a flag
        assert not seed.exists()
        assert g._token_source() == live
        assert live.stat().st_mode & 0o777 == 0o600
        assert json.loads(live.read_text())["refresh_token"] == "invented-refresh"
        verifier = exchanges[0]["code_verifier"]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        assert authorization["code_challenge"] == [challenge]
        assert authorization["code_challenge_method"] == ["S256"]
        assert authorization["access_type"] == ["offline"]
        assert authorization["prompt"] == ["consent"]
        assert authorization["scope"] == [" ".join(g.SCOPES)]
        assert exchanges[0]["code"] == "invented-code"

        def build(*args, **kwargs):
            assert json.loads(live.read_text())["token"] == kwargs["credentials"].token
            service = Mock()
            service.calendarList.return_value.list.return_value.execute.return_value = {"items": []}
            return service

        monkeypatch.setattr("googleapiclient.discovery.build", build)
        assert g.fetch_and_cache()
        assert json.loads(g.cache_path().read_text())["events"] == []
    finally:
        os.chmod(seed.parent, 0o700)


def test_setup_write_failure_is_loud_and_preserves_old_token(tmp_path, monkeypatch, capsys):
    g, live, _, exchanges, _ = _setup_stand(tmp_path, monkeypatch, capsys)
    live.parent.mkdir(parents=True)
    live.write_text("old-invented-grant")

    def fail_replace(*args):
        raise PermissionError("invented write failure")

    monkeypatch.setattr(g.os, "replace", fail_replace)
    with pytest.raises(RuntimeError, match="token could not be saved; access stopped"):
        g.setup_auth(manual=True)
    assert len(exchanges) == 1
    assert live.read_text() == "old-invented-grant"
    assert list(live.parent.iterdir()) == [live]
    assert "Токен сохранён" not in capsys.readouterr().out


def test_setup_native_still_uses_browser_and_seed(tmp_path, monkeypatch, capsys):
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.oauth2.credentials import Credentials

    g, live, seed, exchanges, _ = _setup_stand(tmp_path, monkeypatch, capsys)
    monkeypatch.delenv("HEALTH_RUNTIME")
    calls = []

    def browser(flow, **kwargs):
        calls.append(kwargs)
        return Credentials.from_authorized_user_info(json.loads(_token(False, "native-invented")), g.SCOPES)

    monkeypatch.setattr(InstalledAppFlow, "run_local_server", browser)
    g.setup_auth()
    assert calls == [{"port": 0, "open_browser": True, "prompt": "consent"}]
    assert json.loads(seed.read_text())["token"] == "native-invented"
    assert seed.stat().st_mode & 0o777 == 0o600
    assert not live.exists() and exchanges == []


def test_setup_manual_flag_works_outside_container(tmp_path, monkeypatch, capsys):
    from google_auth_oauthlib.flow import InstalledAppFlow
    g, live, seed, exchanges, _ = _setup_stand(tmp_path, monkeypatch, capsys)
    monkeypatch.delenv("HEALTH_RUNTIME")

    def unexpected_browser(*args, **kwargs):
        raise AssertionError("manual setup must not start a callback server")

    monkeypatch.setattr(InstalledAppFlow, "run_local_server", unexpected_browser)
    g.setup_auth(manual=True)
    assert live.exists() and not seed.exists() and len(exchanges) == 1


def test_setup_exchange_error_does_not_echo_credentials(tmp_path, monkeypatch, capsys):
    from requests_oauthlib import OAuth2Session
    g, live, seed, _, _ = _setup_stand(tmp_path, monkeypatch, capsys)

    def fail(*args, **kwargs):
        raise RuntimeError("SENTINEL-provider-secret-or-code")

    monkeypatch.setattr(OAuth2Session, "fetch_token", fail)
    with pytest.raises(RuntimeError, match="Google OAuth exchange failed") as error:
        g.setup_auth()
    assert "SENTINEL" not in str(error.value) + capsys.readouterr().out
    assert not live.exists() and not seed.exists()


def test_setup_requires_refresh_token(tmp_path, monkeypatch, capsys):
    from requests_oauthlib import OAuth2Session
    g, live, seed, _, _ = _setup_stand(tmp_path, monkeypatch, capsys)

    def exchange(session, *args, **kwargs):
        session.token = dict(access_token="invented-access", token_type="Bearer", expires_at=time.time() + 3600)
        return session.token

    monkeypatch.setattr(OAuth2Session, "fetch_token", exchange)
    with pytest.raises(RuntimeError, match="did not return a refresh token"):
        g.setup_auth()
    assert not live.exists() and not seed.exists()


def test_refresh_write_failure_stops_credentials_use(stand, monkeypatch, caplog):
    g, sec, live = stand

    def fail(*args):
        raise PermissionError("invented refresh write failure")

    monkeypatch.setattr(g.os, "replace", fail)
    assert g._get_credentials() is None
    assert "token could not be saved; access stopped" in caplog.text
    assert not live.exists()
    assert json.loads((sec / "google_calendar_token.json").read_text())["token"] == "a-old"


def test_shared_token_source_selects_seed_live_and_nanosecond_order(tmp_path):
    from secrets_paths import oauth_token_source
    seed, live = tmp_path / "seed", tmp_path / "live"
    assert oauth_token_source(seed, live) == seed
    live.touch()
    assert oauth_token_source(seed, live) == live
    seed.touch()
    os.utime(seed, ns=(2_000_000_000_000_000_002, 2_000_000_000_000_000_002))
    os.utime(live, ns=(2_000_000_000_000_000_001, 2_000_000_000_000_000_001))
    assert oauth_token_source(seed, live) == seed
    os.utime(live, ns=(2_000_000_000_000_000_002, 2_000_000_000_000_000_002))
    assert oauth_token_source(seed, live) == live
