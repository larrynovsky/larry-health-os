"""Вход в Health OS для облачных помощников (нить mcp-gateway, 06.10).

Адрес сервера публичный, и единственная защита медицинской истории — вход. Каждый тест —
один способ войти без права, и он обязан получить отказ. Мутация, снимающая проверку,
красит свой тест (проверено при постройке, список — в contracts/mcp_auth.json, D5).
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

import health_mcp
import mcp_auth

pytestmark = pytest.mark.unit
BASE = "https://hos.example.test"
RES = BASE + "/mcp"
CB = "https://claude.ai/api/mcp/auth_callback"


class Clock:
    t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return mcp_auth.Store(tmp_path / "a.sqlite", now=clock)


def _pkce():
    v = "v" * 50
    return v, base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()


def _begin(store, cid, ch, **kw):
    q = {"client_id": cid, "redirect_uri": CB, "response_type": "code", "code_challenge": ch,
         "code_challenge_method": "S256", "state": "s1", "resource": RES}
    q.update(kw)
    return store.begin(q, RES)


def _login(store):
    cid = store.register({"redirect_uris": [CB], "client_name": "Claude"})["client_id"]
    v, ch = _pkce()
    req, pin = _begin(store, cid, ch)
    code = parse_qs(urlparse(store.confirm(req, pin)).query)["code"][0]
    return cid, v, code


def _code_grant(cid, v, code):
    return {"grant_type": "authorization_code", "code": code, "code_verifier": v,
            "client_id": cid, "redirect_uri": CB}


@pytest.mark.parametrize("uri, ok", [
    (CB, True),
    ("https://claude.com/api/mcp/auth_callback", True),
    ("https://chatgpt.com/connector_platform_oauth_redirect", True),
    ("https://chatgpt.com/connector/oauth/abc123", True),
    ("https://evil.example/cb", False),
    ("https://chatgpt.com.evil.example/connector/oauth/x", False),
    ("http://chatgpt.com/connector/oauth/x", False),
    ("https://chatgpt.com/connector/oauth/../../evil", False),
    ("https://chatgpt.com/connector/oauth/x?next=https://evil", False),
    ("https://claude.ai/api/mcp/auth_callback/../x", False),
])
def test_redirect_allowlist(uri, ok):
    assert mcp_auth.redirect_allowed(uri) is ok


def test_register_refuses_foreign_redirect(store):
    with pytest.raises(mcp_auth.AuthError):
        store.register({"redirect_uris": [CB, "https://evil.example/cb"]})


def test_full_flow_gives_token_bound_to_resource(store):
    cid, v, code = _login(store)
    tok = store.token(dict(_code_grant(cid, v, code), resource=RES), RES)
    assert store.verify(tok["access_token"], RES) == cid
    assert store.verify(tok["access_token"], "https://other.example/mcp") is None


def test_wrong_pin_burns_request_after_five(store):
    cid = store.register({"redirect_uris": [CB]})["client_id"]
    req, pin = _begin(store, cid, _pkce()[1])
    wrong = "000000" if pin != "000000" else "111111"
    for _ in range(mcp_auth.MAX_ATTEMPTS):
        with pytest.raises(mcp_auth.AuthError):
            store.confirm(req, wrong)
    with pytest.raises(mcp_auth.AuthError):
        store.confirm(req, pin)                 # верный, но попытки кончились


def test_pin_expires(store, clock):
    cid = store.register({"redirect_uris": [CB]})["client_id"]
    req, pin = _begin(store, cid, _pkce()[1])
    clock.t += mcp_auth.CODE_TTL + 1
    with pytest.raises(mcp_auth.AuthError):
        store.confirm(req, pin)


def test_code_needs_pkce_and_is_single_use(store):
    cid, v, code = _login(store)
    with pytest.raises(mcp_auth.AuthError):
        store.token(_code_grant(cid, "w" * 50, code), RES)       # чужой verifier
    with pytest.raises(mcp_auth.AuthError):
        store.token(_code_grant(cid, v, code), RES)              # код уже сгорел на первой попытке


def test_code_bound_to_client_and_redirect(store):
    cid, v, code = _login(store)
    with pytest.raises(mcp_auth.AuthError):
        store.token(dict(_code_grant(cid, v, code), client_id="hos_other"), RES)


def test_begin_requires_s256_registered_redirect_and_own_resource(store):
    cid = store.register({"redirect_uris": [CB]})["client_id"]
    with pytest.raises(mcp_auth.AuthError):
        _begin(store, cid, "x", code_challenge_method="plain")
    with pytest.raises(mcp_auth.AuthError):
        _begin(store, cid, "x", redirect_uri="https://claude.com/api/mcp/auth_callback")  # не регистрировал
    with pytest.raises(mcp_auth.AuthError):
        _begin(store, cid, "x", resource="https://other.example/mcp")
    with pytest.raises(mcp_auth.AuthError):
        _begin(store, "hos_unknown", "x")


def test_refresh_rotates_and_revoke_kills(store):
    cid, v, code = _login(store)
    t1 = store.token(_code_grant(cid, v, code), RES)
    t2 = store.token({"grant_type": "refresh_token", "refresh_token": t1["refresh_token"], "client_id": cid}, RES)
    with pytest.raises(mcp_auth.AuthError):
        store.token({"grant_type": "refresh_token", "refresh_token": t1["refresh_token"]}, RES)
    assert store.verify(t2["access_token"], RES) == cid
    store.revoke_client(cid)
    assert store.verify(t2["access_token"], RES) is None


def test_access_token_expires(store, clock):
    cid, v, code = _login(store)
    t = store.token(_code_grant(cid, v, code), RES)
    clock.t += mcp_auth.ACCESS_TTL + 1
    assert store.verify(t["access_token"], RES) is None


def test_pending_cap_protects_owner_telegram(store):
    cid = store.register({"redirect_uris": [CB]})["client_id"]
    for i in range(mcp_auth.MAX_PENDING):
        _begin(store, cid, f"x{i}")
    with pytest.raises(mcp_auth.AuthError):
        _begin(store, cid, "y")


def test_same_request_twice_sends_one_code(store):
    """Клиент открывает страницу входа дважды (замер 06.10) — второй раз тот же запрос и без кода."""
    cid = store.register({"redirect_uris": [CB]})["client_id"]
    r1, pin1 = _begin(store, cid, "x")
    r2, pin2 = _begin(store, cid, "x")
    assert pin1 and pin2 is None and r1 == r2
    assert parse_qs(urlparse(store.confirm(r1, pin1)).query)["code"]


def test_tokens_stored_only_as_hash(store, tmp_path):
    cid, v, code = _login(store)
    t = store.token(_code_grant(cid, v, code), RES)
    raw = (tmp_path / "a.sqlite").read_bytes()
    assert t["access_token"].encode() not in raw and t["refresh_token"].encode() not in raw


# ── HTTP: сервер целиком ──
@pytest.fixture
def server(store):
    sent = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0),
                              health_mcp.make_handler(store, BASE, lambda t: sent.append(t) or True))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", sent
    srv.shutdown()


def _post(url, data, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_mcp_without_valid_token_is_401_with_discovery(server):
    url, _ = server
    st, h, _ = _post(url + "/mcp", b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
                     {"Content-Type": "application/json"})
    assert st == 401 and "resource_metadata=" in h["WWW-Authenticate"]
    st, _, _ = _post(url + "/mcp", b"{}", {"Authorization": "Bearer forged"})
    assert st == 401


def test_metadata_advertises_s256(server):
    url, _ = server
    with urllib.request.urlopen(url + "/.well-known/oauth-authorization-server") as r:
        meta = json.loads(r.read())
    assert meta["code_challenge_methods_supported"] == ["S256"] and meta["issuer"] == BASE
    with urllib.request.urlopen(url + "/.well-known/oauth-protected-resource") as r:
        assert json.loads(r.read())["resource"] == RES


def test_http_flow_end_to_end(server):
    url, sent = server
    reg = json.loads(_post(url + "/register",
                           json.dumps({"redirect_uris": [CB], "client_name": "Claude"}).encode())[2])
    v, ch = _pkce()
    q = urlencode({"client_id": reg["client_id"], "redirect_uri": CB, "response_type": "code",
                   "code_challenge": ch, "code_challenge_method": "S256", "state": "z", "resource": RES})
    with urllib.request.urlopen(url + "/authorize?" + q) as r:
        page = r.read().decode()
    assert "Telegram" in page and len(sent) == 1
    assert sent[0].split("Код:")[1].strip()[:6] not in page          # цифры только в Telegram
    pin = sent[0].split("Код:")[1].strip()[:6]
    req_id = page.split('name="req_id" value="')[1].split('"')[0]

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    with pytest.raises(urllib.error.HTTPError) as exc:
        opener.open(urllib.request.Request(url + "/authorize",
                                           data=urlencode({"req_id": req_id, "pin": pin}).encode()))
    assert exc.value.code == 302
    loc = exc.value.headers["Location"]
    assert loc.startswith(CB + "?") and parse_qs(urlparse(loc).query)["state"] == ["z"]
    assert parse_qs(urlparse(loc).query)["iss"] == [BASE]                       # RFC 9207
    # Редирект на адрес возврата не должен запрещаться CSP страницы (замер 06.10: form-action 'self'
    # блокировал 302 на claude.ai, код сгорал)
    csp = exc.value.headers["Content-Security-Policy"]
    assert "https://claude.ai" in csp and "https://chatgpt.com" in csp
    code = parse_qs(urlparse(loc).query)["code"][0]
    tok = json.loads(_post(url + "/token", urlencode({
        "grant_type": "authorization_code", "code": code, "code_verifier": v,
        "client_id": reg["client_id"], "redirect_uri": CB, "resource": RES}).encode())[2])
    st, _, body = _post(url + "/mcp", b'{"jsonrpc":"2.0","id":7,"method":"tools/list"}',
                        {"Authorization": "Bearer " + tok["access_token"]})
    assert st == 200 and "ping" in [t["name"] for t in json.loads(body)["result"]["tools"]]
