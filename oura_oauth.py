"""Oura credentials: manual browser authorization and serialized, durable rotation.

Seed: secrets/oura_oauth.json (optional, read only). Live: data/oura_oauth.json.
Docker setup writes the initial grant to data too: secrets is mounted read only.
Public contract: contracts/oura_oauth.json. No health-data API calls here.
"""
import base64
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from _time_inject import get_now
from secrets_paths import oauth_token_source, secrets_dir

# Cloud endpoints also work for new developer-portal apps: owner checked 2026-10-02.
AUTHORIZE_URL = "https://cloud.ouraring.com/oauth/authorize"
TOKEN_URL = "https://api.ouraring.com/oauth/token"
_PORTALS = {"cloud": (AUTHORIZE_URL, TOKEN_URL)}
REDIRECT_URI = "http://localhost:9876/callback/"
SCOPES = "daily workout spo2 stress"
TOKEN_NAME = "oura_oauth.json"


def _now() -> float:
    return get_now().timestamp()


@contextmanager
def _locked(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(data_dir / ".oura_oauth.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _save(path: Path, token: dict):
    """0600 temp on the same filesystem; fsync before rename and before returning."""
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
            tmp = Path(f.name)
            json.dump(token, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        raise RuntimeError("Oura OAuth token could not be saved in data; access stopped. "
                           "Fix data permissions/free space and authorize again.") from None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _client() -> dict:
    result = {}
    for field, path in (("client_id", secrets_dir() / "oura_client_id"),
                        ("client_secret", secrets_dir() / "oura_client_secret")):
        try:
            value = path.read_text().strip()
        except OSError:
            raise RuntimeError(f"Missing/unreadable oura_{field} in secrets directory.") from None
        if not value:
            raise RuntimeError(f"Empty oura_{field} in secrets directory.")
        result[field] = value
    return result


def _validate(token: dict):
    if not isinstance(token, dict):
        raise RuntimeError("Invalid Oura OAuth credential; authorize again.")
    for field in ("access_token", "refresh_token"):
        if not isinstance(token.get(field), str) or not token[field].strip():
            raise RuntimeError(f"Oura OAuth response/credential lacks {field}; authorize again.")
    kind = token.get("token_type", "bearer")
    if not isinstance(kind, str) or kind.lower() != "bearer":
        raise RuntimeError("Oura OAuth token_type must be bearer.")


def _exchange(fields: dict, token_url: str = TOKEN_URL) -> dict:
    if token_url != TOKEN_URL:
        raise RuntimeError("Unsupported Oura OAuth token endpoint; authorize again.")
    fields = {**fields, **_client()}
    request = urllib.request.Request(token_url, data=urllib.parse.urlencode(fields).encode(),
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            token = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # Never include provider bodies or URLs: they can echo codes or credentials.
        raise RuntimeError(f"Oura OAuth exchange failed (HTTP {exc.code}); check client files "
                           "and cloud portal registration, then authorize again.") from None
    except (OSError, ValueError):
        raise RuntimeError("Oura OAuth exchange failed (network or invalid JSON); "
                           "authorize again if a refresh may already have rotated.") from None
    _validate(token)
    lifetime = token.get("expires_in")
    if isinstance(lifetime, bool) or not isinstance(lifetime, (int, float)) or not math.isfinite(lifetime) or lifetime <= 0:
        raise RuntimeError("Invalid Oura OAuth expires_in; authorize again.")
    token["expires_at"] = _now() + lifetime
    token["token_url"] = token_url
    return token


def get_access_token(data_dir: Path) -> str | None:
    """None ONLY if neither OAuth file exists; broken OAuth never falls back to PAT."""
    seed, live = secrets_dir() / TOKEN_NAME, data_dir / TOKEN_NAME
    if not oauth_token_source(seed, live).exists():
        return None
    with _locked(data_dir):
        source = oauth_token_source(seed, live)
        try:
            token = json.loads(source.read_text())
        except (OSError, ValueError):
            raise RuntimeError("Unreadable/invalid Oura OAuth credential; authorize again.") from None
        _validate(token)
        expiry = token.get("expires_at", 0)
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry):
            raise RuntimeError("Invalid Oura OAuth expiry; authorize again.")
        if expiry <= _now() + 60:
            token = _exchange({"grant_type": "refresh_token", "refresh_token": token["refresh_token"]},
                              token.get("token_url", TOKEN_URL))
            _save(live, token)
        return token["access_token"]


def _parse_callback(url: str, state: str) -> str:
    parsed, expected = urllib.parse.urlsplit(url.strip()), urllib.parse.urlsplit(REDIRECT_URI)
    if (parsed.scheme, parsed.netloc, parsed.path) != (expected.scheme, expected.netloc, expected.path) or parsed.fragment:
        raise ValueError("Wrong redirect URL; paste the full localhost callback address.")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    returned = query.get("state", [])
    if len(returned) != 1 or not secrets.compare_digest(returned[0].encode(), state.encode()):
        raise ValueError("Wrong state; use the URL from this authorization attempt.")
    if "error" in query:
        raise ValueError("Oura authorization denied; run authorization again and approve access.")
    codes = query.get("code", [])
    if len(codes) != 1 or not codes[0]:
        raise ValueError("Missing/duplicate code in callback URL.")
    return codes[0]


def authorize_oura(data_dir: Path, portal: str = "cloud"):
    """Paste flow: browser on host, terminal in container; no listener or port mapping."""
    if portal not in _PORTALS:
        raise ValueError("Unsupported Oura portal; use the default cloud flow for all apps.")
    client = _client()
    authorize_url, token_url = _PORTALS[portal]
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    url = authorize_url + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client["client_id"], "redirect_uri": REDIRECT_URI,
        "scope": SCOPES, "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
    print("Open this URL in your host browser and approve access:\n" + url)
    print("The localhost page will not load. Copy its FULL address; do not share it in chat.")
    # getpass avoids echoing the code into terminal transcripts.
    from getpass import getpass
    code = _parse_callback(getpass("Paste the full redirect URL (hidden): "), state)
    with _locked(data_dir):
        token = _exchange({"grant_type": "authorization_code", "code": code,
                           "redirect_uri": REDIRECT_URI, "code_verifier": verifier}, token_url)
        _save(data_dir / TOKEN_NAME, token)
    print("Oura OAuth connected; credential saved in the data directory (mode 600).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Connect Oura by pasting a browser redirect URL.")
    parser.add_argument("--portal", choices=_PORTALS, default="cloud",
                        help="cloud flow for both old and new portal apps; no automatic endpoint retry")
    args = parser.parse_args()
    try:
        authorize_oura(Path(os.environ.get("HEALTH_DATA_DIR", str(Path.home() / "health"))).expanduser() / "data", args.portal)
    except (RuntimeError, ValueError, OSError, EOFError, KeyboardInterrupt) as exc:
        print(f"Oura authorization stopped: {exc}", file=sys.stderr)
        sys.exit(1)
