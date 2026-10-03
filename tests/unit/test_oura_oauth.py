"""Offline Oura authorization/rotation oracles. All credentials are invented."""
import io
import json
import logging
import os
from urllib.parse import parse_qs, urlsplit
from urllib.error import HTTPError

import pytest

import oura_oauth as oauth

pytestmark = pytest.mark.unit


@pytest.fixture
def stand(tmp_path, monkeypatch):
    sec, data = tmp_path / "secrets", tmp_path / "health" / "data"
    sec.mkdir()
    data.mkdir(parents=True)
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(sec))
    monkeypatch.setenv("HEALTH_DATA_DIR", str(data.parent))
    monkeypatch.setattr(logging, "FileHandler", lambda *a, **k: logging.NullHandler())
    import import_oura as importer
    monkeypatch.setattr(importer, "METRICS_DIR", data / "daily_metrics")
    monkeypatch.setattr(oauth, "_now", lambda: 1000.0)
    (sec / "oura_client_id").write_text("invented-client")
    (sec / "oura_client_secret").write_text("invented-secret")
    (sec / "oura_token").write_text("  invented-PAT\n")
    # Default network oracle fails closed. Individual tests replace it with a fake.
    def forbidden(*args, **kwargs):
        pytest.fail("unexpected urllib network call")
    monkeypatch.setattr(oauth.urllib.request, "urlopen", forbidden)
    return sec, data, importer


def _credential(expiry=0, access="old-access", refresh="old-refresh"):
    return {"access_token": access, "refresh_token": refresh, "expires_at": expiry}


def _seed(sec, token=None):
    path = sec / oauth.TOKEN_NAME
    path.write_text(json.dumps(_credential() if token is None else token))
    return path


def _response(payload):
    return io.BytesIO(json.dumps(payload).encode())


def _rotated():
    return {"token_type": "bearer", "access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}


def test_rotation_is_saved_before_first_data_request(stand, monkeypatch, caplog):
    sec, data, importer = stand
    seed = _seed(sec)
    before = seed.read_bytes()
    seed.chmod(0o400)
    sec.chmod(0o500)
    calls = []
    def network(req, timeout):
        calls.append(req.full_url)
        if req.full_url == oauth.TOKEN_URL:
            body = parse_qs(req.data.decode())
            assert body == {"grant_type": ["refresh_token"], "refresh_token": ["old-refresh"],
                            "client_id": ["invented-client"], "client_secret": ["invented-secret"]}
            return _response(_rotated())
        saved = json.loads((data / oauth.TOKEN_NAME).read_text())
        assert saved["refresh_token"] == "new-refresh"
        assert saved["access_token"] == "new-access" and saved["expires_at"] == 4600
        assert req.get_header("Authorization") == "Bearer new-access"
        return _response({"data": [{"day": "2040-01-01"}]})
    monkeypatch.setattr(oauth.urllib.request, "urlopen", network)
    try:
        with caplog.at_level(logging.INFO):
            assert importer.oura_get("sleep", "2040-01-01", "2040-01-02") == [{"day": "2040-01-01"}]
            assert importer.get_token() == "new-access"  # next run uses persisted live token
        assert calls == [oauth.TOKEN_URL, "https://api.ouraring.com/v2/usercollection/sleep?start_date=2040-01-01&end_date=2040-01-02"]
        assert seed.read_bytes() == before
        assert (data / oauth.TOKEN_NAME).stat().st_mode & 0o777 == 0o600
        assert "OAuth2" in caplog.text and "new-access" not in caplog.text
    finally:
        sec.chmod(0o700)


def test_failed_atomic_replace_is_loud_and_does_not_use_access_or_pat(stand, monkeypatch):
    sec, data, importer = stand
    live = data / oauth.TOKEN_NAME
    live.write_text(json.dumps(_credential()))
    before = live.read_bytes()
    calls = []
    def network(req, timeout):
        calls.append(req.full_url)
        assert req.full_url == oauth.TOKEN_URL
        return _response(_rotated())
    def broken_replace(*args):
        raise OSError("invented disk failure")
    monkeypatch.setattr(oauth.urllib.request, "urlopen", network)
    monkeypatch.setattr(oauth.os, "replace", broken_replace)
    with pytest.raises(RuntimeError, match="could not be saved"):
        importer.oura_get("sleep", "2040-01-01", "2040-01-02")
    assert calls == [oauth.TOKEN_URL] and live.read_bytes() == before
    assert set(p.name for p in data.iterdir()) == {oauth.TOKEN_NAME, ".oura_oauth.lock"}


def test_pat_keeps_stripping_bearer_and_pagination(stand, monkeypatch, caplog):
    sec, data, importer = stand
    # PAT must not need OAuth client files or a writable data directory.
    (sec / "oura_client_id").unlink()
    (sec / "oura_client_secret").unlink()
    calls = []
    def network(req, timeout):
        calls.append(req.full_url)
        assert req.get_header("Authorization") == "Bearer invented-PAT"
        return _response({"data": [len(calls)], "next_token": "page2" if len(calls) == 1 else None})
    monkeypatch.setattr(oauth.urllib.request, "urlopen", network)
    with caplog.at_level(logging.INFO):
        assert importer.oura_get("sleep", "2040-01-01", "2040-01-02") == [1, 2]
    assert calls[1].endswith("&next_token=page2")
    assert list(data.iterdir()) == []
    assert "PAT" in caplog.text and "invented-PAT" not in caplog.text


def test_new_seed_beats_old_live_shared_with_calendar(stand):
    sec, data, importer = stand
    seed = _seed(sec, _credential(5000, "reauthorized"))
    live = data / oauth.TOKEN_NAME
    live.write_text(json.dumps(_credential(5000, "stale-live")))
    os.utime(live, (100, 100))
    os.utime(seed, (200, 200))
    assert importer.get_token() == "reauthorized"
    from secrets_paths import oauth_token_source
    assert oauth_token_source(seed, live) == seed
    os.utime(live, (200, 200))
    assert oauth_token_source(seed, live) == live


def test_broken_oauth_does_not_fall_back_to_pat(stand):
    sec, _, importer = stand
    _seed(sec, {"access_token": "invented", "expires_at": 5000})
    with pytest.raises(RuntimeError, match="refresh_token"):
        importer.get_token()


def test_rotated_response_must_contain_new_refresh_token(stand, monkeypatch):
    sec, data, importer = stand
    _seed(sec)
    monkeypatch.setattr(oauth.urllib.request, "urlopen", lambda *a, **k: _response({"access_token": "new", "expires_in": 3600}))
    with pytest.raises(RuntimeError, match="refresh_token"):
        importer.get_token()
    assert not (data / oauth.TOKEN_NAME).exists()


def test_provider_error_does_not_echo_credentials(stand, monkeypatch):
    sec, _, importer = stand
    _seed(sec)
    def fail(*a, **k):
        raise HTTPError(oauth.TOKEN_URL, 400, "ECHO-invented-secret", {}, io.BytesIO(b"old-refresh"))
    monkeypatch.setattr(oauth.urllib.request, "urlopen", fail)
    with pytest.raises(RuntimeError, match="HTTP 400") as err:
        importer.get_token()
    assert "invented-secret" not in str(err.value) and "old-refresh" not in str(err.value)


def test_pasted_url_checks_state_before_exchange(stand, monkeypatch):
    _, data, _ = stand
    import getpass
    monkeypatch.setattr(getpass, "getpass", lambda _: oauth.REDIRECT_URI + "?code=one-use-code&state=wrong")
    with pytest.raises(ValueError, match="state"):
        oauth.authorize_oura(data)
    assert not (data / oauth.TOKEN_NAME).exists()


@pytest.mark.parametrize("tail", ["?state=right", "?code=&state=right", "?code=a&code=b&state=right",
                                   "?code=a&state=right&state=right", "?error=access_denied&state=right"])
def test_callback_rejects_missing_duplicate_or_denied_fields(tail):
    with pytest.raises(ValueError):
        oauth._parse_callback(oauth.REDIRECT_URI + tail, "right")


@pytest.mark.parametrize("base", ["https://localhost:9876/callback/", "http://localhost:9876/callback",
                                   "http://attacker.example/callback/", "http://localhost:9876/callback/#x"])
def test_callback_requires_exact_redirect(base):
    with pytest.raises(ValueError, match="redirect"):
        oauth._parse_callback(base + "?code=c&state=s", "s")


def test_setup_paste_flow_saves_in_data_with_pkce(stand, monkeypatch, capsys):
    sec, data, _ = stand
    import getpass
    def paste(prompt):
        printed = capsys.readouterr().out
        url = next(line for line in printed.splitlines() if line.startswith("https://cloud.ouraring.com/oauth/authorize?"))
        params = parse_qs(urlsplit(url).query)
        assert params["scope"] == ["daily workout spo2 stress"]
        assert params["redirect_uri"] == [oauth.REDIRECT_URI]
        assert params["code_challenge_method"] == ["S256"]
        assert "invented-secret" not in printed
        paste.challenge = params["code_challenge"][0]
        return oauth.REDIRECT_URI + "?code=code%2Bdecoded&state=" + params["state"][0]
    def network(req, timeout):
        assert req.full_url == "https://api.ouraring.com/oauth/token"
        params = parse_qs(req.data.decode())
        assert params["grant_type"] == ["authorization_code"] and params["code"] == ["code+decoded"]
        assert params["redirect_uri"] == [oauth.REDIRECT_URI]
        challenge = oauth.base64.urlsafe_b64encode(oauth.hashlib.sha256(params["code_verifier"][0].encode()).digest()).decode().rstrip("=")
        assert challenge == paste.challenge
        return _response(_rotated())
    monkeypatch.setattr(getpass, "getpass", paste)
    monkeypatch.setattr(oauth.urllib.request, "urlopen", network)
    sec.chmod(0o500)
    try:
        oauth.authorize_oura(data)
        assert json.loads((data / oauth.TOKEN_NAME).read_text())["refresh_token"] == "new-refresh"
        assert not (sec / oauth.TOKEN_NAME).exists()
        assert "new-access" not in capsys.readouterr().out
    finally:
        sec.chmod(0o700)


def test_developer_portal_rejected_before_reading_client(stand, monkeypatch):
    _, data, _ = stand
    monkeypatch.setattr(oauth, "_client", lambda: pytest.fail("unsupported flow read client secrets"))
    with pytest.raises(ValueError, match="Unsupported Oura portal"):
        oauth.authorize_oura(data, "developer")


def test_cli_rejects_developer_portal(monkeypatch):
    import runpy
    import sys
    monkeypatch.setattr(sys, "argv", ["oura_oauth.py", "--portal", "developer"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("oura_oauth", run_name="__main__")
    assert exc.value.code == 2


@pytest.mark.parametrize("token_url", ["https://attacker.example/token",
                                      "https://moi.ouraring.com/oauth/v2/ext/oauth-token"])
def test_credential_cannot_send_client_secret_to_arbitrary_endpoint(stand, monkeypatch, token_url):
    sec, _, importer = stand
    token = _credential()
    token["token_url"] = token_url
    _seed(sec, token)
    monkeypatch.setattr(oauth, "_client", lambda: pytest.fail("unsupported endpoint read client secrets"))
    with pytest.raises(RuntimeError, match="Unsupported.*endpoint"):
        importer.get_token()


def test_parallel_refreshes_exchange_single_use_token_once(stand, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    sec, data, _ = stand
    _seed(sec)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def network(req, timeout):
        calls.append(req.full_url)
        assert len(calls) == 1, "single-use refresh token exchanged concurrently"
        entered.set()
        assert release.wait(5), "test did not release provider"
        return _response(_rotated())
    monkeypatch.setattr(oauth.urllib.request, "urlopen", network)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(oauth.get_access_token, data)
        assert entered.wait(5)
        second = workers.submit(oauth.get_access_token, data)
        try:
            # second is blocked on the file lock while the first owns the old refresh token
            with pytest.raises(TimeoutError):
                second.result(timeout=0.1)
        finally:
            release.set()
        assert first.result(timeout=5) == second.result(timeout=5) == "new-access"
    assert calls == [oauth.TOKEN_URL]
