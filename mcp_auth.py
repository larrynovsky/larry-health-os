"""mcp_auth — вход в Health OS для облачных помощников (Claude, ChatGPT) по OAuth 2.1.

Зачем (решения владельца 06.10, нить mcp-gateway). Health OS — одна память, Claude и
ChatGPT — окна в неё, доступ с телефона. Помощник ходит к серверу MCP из облака своего
поставщика, поэтому у сервера есть адрес в интернете, и единственная защита медицинской
истории — этот модуль. Отсюда правила: fail-closed везде и ни одного пароля.

Как устроено. Клиент регистрируется сам (DCR, RFC 7591) и только с адресом возврата из
ALLOWED_REDIRECTS. Иначе получится открытый редирект: код входа уйдёт на чужой адрес.
Страница «разрешить» не спрашивает пароль. Она просит 6 цифр, которые в этот момент
приходят владельцу в Telegram. Кто не видит Telegram владельца, войти не может. После пяти
неверных попыток или через 10 минут запрос сгорает. Код входа меняется на токен только
с верным PKCE S256 (RFC 7636) и тем же resource (RFC 8707). Храним только sha256 токенов.
Тенант задаёт файл состояния рядом с базой тенанта, а не аргумент запроса.

Граница: модуль ничего не знает о здоровье и базу здоровья не открывает — только свой файл.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

# Адреса возврата клиентов взяты из их документации (замер 06.10):
# Claude — support.claude.com, «Building custom connectors via remote MCP servers»;
# ChatGPT — developers.openai.com/apps-sdk/build/auth.
ALLOWED_REDIRECTS = frozenset({
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
    "https://chatgpt.com/connector_platform_oauth_redirect",
})
ALLOWED_REDIRECT_PREFIXES = ("https://chatgpt.com/connector/oauth/",)

CODE_TTL = 600            # секунд живут запрос «разрешить» и код входа
MAX_ATTEMPTS = 5
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 86400
SCOPE = "health.read"
MAX_PENDING = 5           # одновременных запросов «разрешить»: регистрация открыта всем (DCR),
                          # и без потолка чужой мог бы засыпать Telegram владельца кодами
MAX_CLIENTS_PER_DAY = 20


class AuthError(Exception):
    """Отказ входа. .error — код OAuth (invalid_request, invalid_grant, …)."""

    def __init__(self, error: str, detail: str = ""):
        super().__init__(f"{error}: {detail}")
        self.error, self.detail = error, detail


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def redirect_allowed(uri: str) -> bool:
    if uri in ALLOWED_REDIRECTS:
        return True
    p = urlparse(uri)
    # Префикс сверяется по разобранному адресу: «…/connector/oauth/../x»,
    # «https://chatgpt.com.evil/…» и адреса с параметрами не проходят.
    return (p.scheme == "https" and p.netloc == "chatgpt.com" and not p.query and not p.fragment
            and ".." not in p.path and any(uri.startswith(x) for x in ALLOWED_REDIRECT_PREFIXES))


class Store:
    """Состояние входа одного тенанта: свой файл SQLite рядом с базой тенанта."""

    def __init__(self, path: Path, now=time.time):
        self.path, self.now = Path(path), now
        with self._c() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS clients(client_id TEXT PRIMARY KEY, name TEXT,
                redirect_uris TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS pending(req_id TEXT PRIMARY KEY, client_id TEXT NOT NULL,
                redirect_uri TEXT NOT NULL, state TEXT, challenge TEXT NOT NULL, resource TEXT NOT NULL,
                pin_hash TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS codes(code_hash TEXT PRIMARY KEY, client_id TEXT NOT NULL,
                redirect_uri TEXT NOT NULL, challenge TEXT NOT NULL, resource TEXT NOT NULL,
                created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS tokens(token_hash TEXT PRIMARY KEY, kind TEXT NOT NULL,
                client_id TEXT NOT NULL, resource TEXT NOT NULL, expires REAL NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
            """)

    def _c(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    # ── регистрация клиента (RFC 7591) ──
    def register(self, meta: dict) -> dict:
        uris = meta.get("redirect_uris")
        if not isinstance(uris, list) or not uris or not all(isinstance(u, str) for u in uris):
            raise AuthError("invalid_redirect_uri", "redirect_uris обязателен")
        if not all(redirect_allowed(u) for u in uris):
            raise AuthError("invalid_redirect_uri", "адрес возврата не из списка клиентов")
        with self._c() as c:
            n = c.execute("SELECT count(*) FROM clients WHERE created>?", (self.now() - 86400,)).fetchone()[0]
        if n >= MAX_CLIENTS_PER_DAY:
            raise AuthError("temporarily_unavailable", "слишком много регистраций за сутки")
        cid = "hos_" + secrets.token_urlsafe(18)
        name = str(meta.get("client_name") or "")[:80]
        with self._c() as c:
            c.execute("INSERT INTO clients VALUES (?,?,?,?)", (cid, name, json.dumps(uris), self.now()))
        return {"client_id": cid, "client_name": name, "redirect_uris": uris,
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]}

    def _client(self, cid: str):
        with self._c() as c:
            return c.execute("SELECT * FROM clients WHERE client_id=?", (cid,)).fetchone()

    def client_name(self, cid: str) -> str:
        r = self._client(cid)
        return (r["name"] if r else "") or "?"

    # ── запрос «разрешить» ──
    def begin(self, q: dict, resource: str) -> "tuple[str, str | None]":
        """→ (req_id, pin). pin уходит владельцу в Telegram, а не в браузер; None — код уже отправлен."""
        cl = self._client(q.get("client_id", ""))
        if not cl:
            raise AuthError("invalid_client", "клиент не зарегистрирован")
        ru = q.get("redirect_uri", "")
        if ru not in json.loads(cl["redirect_uris"]) or not redirect_allowed(ru):
            raise AuthError("invalid_request", "адрес возврата не зарегистрирован")
        if q.get("response_type") != "code":
            raise AuthError("unsupported_response_type")
        if q.get("code_challenge_method") != "S256" or not q.get("code_challenge"):
            raise AuthError("invalid_request", "нужен PKCE S256")
        if q.get("resource", resource) != resource:
            raise AuthError("invalid_target", "чужой resource")
        # Тот же запрос второй раз (клиент открывает страницу входа дважды — замер 06.10, Claude:
        # два GET /authorize подряд) — тот же req_id и БЕЗ нового кода: иначе владельцу приходят
        # два кода, а принимается только последний.
        with self._c() as c:
            c.execute("DELETE FROM pending WHERE created<?", (self.now() - CODE_TTL,))
            same = c.execute("SELECT req_id FROM pending WHERE client_id=? AND redirect_uri=? AND challenge=?"
                             " AND COALESCE(state,'')=? AND attempts<?",
                             (cl["client_id"], ru, q["code_challenge"], q.get("state") or "", MAX_ATTEMPTS)).fetchone()
            busy = c.execute("SELECT count(*) FROM pending").fetchone()[0] >= MAX_PENDING
        if same:
            return same["req_id"], None
        if busy:
            raise AuthError("temporarily_unavailable", "слишком много запросов подряд")
        req_id, pin = secrets.token_urlsafe(18), f"{secrets.randbelow(10**6):06d}"
        with self._c() as c:
            c.execute("INSERT INTO pending(req_id,client_id,redirect_uri,state,challenge,resource,pin_hash,created)"
                      " VALUES (?,?,?,?,?,?,?,?)",
                      (req_id, cl["client_id"], ru, q.get("state"), q["code_challenge"], resource,
                       _h(pin), self.now()))
        return req_id, pin

    def confirm(self, req_id: str, pin: str, issuer: "str | None" = None) -> str:
        """Верные цифры → адрес возврата с кодом входа. Неверные → AuthError, попытка списана."""
        # Отказ поднимается ПОСЛЕ выхода из `with`: исключение внутри него откатывает транзакцию,
        # и списанная попытка не сохранилась бы — подбор шёл бы без ограничения (поймал тест
        # test_wrong_pin_burns_request_after_five при постройке).
        verdict = None
        with self._c() as c:
            r = c.execute("SELECT * FROM pending WHERE req_id=?", (req_id,)).fetchone()
            if not r or self.now() - r["created"] > CODE_TTL or r["attempts"] >= MAX_ATTEMPTS:
                if r:
                    c.execute("DELETE FROM pending WHERE req_id=?", (req_id,))
                verdict = "этот запрос уже использован или истёк — начните подключение заново"
            elif not hmac.compare_digest(r["pin_hash"], _h((pin or "").strip())):
                c.execute("UPDATE pending SET attempts=attempts+1 WHERE req_id=?", (req_id,))
                verdict = "неверный код"
        if verdict:
            raise AuthError("access_denied", verdict)
        with self._c() as c:
            if not c.execute("DELETE FROM pending WHERE req_id=?", (req_id,)).rowcount:
                raise AuthError("access_denied", "запрос уже использован")      # гонка двух вводов
            code = secrets.token_urlsafe(32)
            c.execute("INSERT INTO codes VALUES (?,?,?,?,?,?)",
                      (_h(code), r["client_id"], r["redirect_uri"], r["challenge"], r["resource"], self.now()))
        q = {"code": code}
        if r["state"] is not None:
            q["state"] = r["state"]
        if issuer:
            q["iss"] = issuer          # RFC 9207: клиент сверяет, от кого пришёл код
        return r["redirect_uri"] + ("&" if "?" in r["redirect_uri"] else "?") + urlencode(q)

    # ── токены ──
    def _issue(self, c, client_id: str, resource: str) -> dict:
        at, rt, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), self.now()
        c.execute("INSERT INTO tokens VALUES (?,?,?,?,?,0,?)",
                  (_h(at), "access", client_id, resource, now + ACCESS_TTL, now))
        c.execute("INSERT INTO tokens VALUES (?,?,?,?,?,0,?)",
                  (_h(rt), "refresh", client_id, resource, now + REFRESH_TTL, now))
        return {"access_token": at, "token_type": "Bearer", "expires_in": ACCESS_TTL,
                "refresh_token": rt, "scope": SCOPE}

    def token(self, form: dict, resource: str) -> dict:
        gt = form.get("grant_type")
        if form.get("resource", resource) != resource:
            raise AuthError("invalid_target", "чужой resource")
        if gt == "authorization_code":
            with self._c() as c:
                r = c.execute("SELECT * FROM codes WHERE code_hash=?", (_h(form.get("code", "")),)).fetchone()
                if r:
                    c.execute("DELETE FROM codes WHERE code_hash=?", (r["code_hash"],))   # одноразовый
            if not r:
                raise AuthError("invalid_grant", "код неизвестен или уже использован")
            if self.now() - r["created"] > CODE_TTL:
                raise AuthError("invalid_grant", "код истёк")
            if form.get("client_id") != r["client_id"] or form.get("redirect_uri") != r["redirect_uri"]:
                raise AuthError("invalid_grant", "клиент или адрес не совпадают")
            v = form.get("code_verifier", "")
            s = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
            if not v or not hmac.compare_digest(s, r["challenge"]):
                raise AuthError("invalid_grant", "PKCE не сошёлся")
            with self._c() as c:
                return self._issue(c, r["client_id"], r["resource"])
        if gt == "refresh_token":
            with self._c() as c:
                r = c.execute("SELECT * FROM tokens WHERE token_hash=? AND kind='refresh'",
                              (_h(form.get("refresh_token", "")),)).fetchone()
                if (not r or r["revoked"] or r["expires"] < self.now()
                        or form.get("client_id", r["client_id"]) != r["client_id"]):
                    raise AuthError("invalid_grant", "refresh недействителен")
                c.execute("UPDATE tokens SET revoked=1 WHERE token_hash=?", (r["token_hash"],))   # ротация
                return self._issue(c, r["client_id"], r["resource"])
        raise AuthError("unsupported_grant_type")

    def verify(self, bearer: str, resource: str) -> "str | None":
        """→ client_id или None. Любая неясность — None (fail-closed)."""
        if not bearer:
            return None
        with self._c() as c:
            r = c.execute("SELECT * FROM tokens WHERE token_hash=? AND kind='access'", (_h(bearer),)).fetchone()
        if not r or r["revoked"] or r["expires"] < self.now() or r["resource"] != resource:
            return None
        return r["client_id"]

    def revoke_client(self, client_id: str) -> int:
        with self._c() as c:
            return c.execute("UPDATE tokens SET revoked=1 WHERE client_id=? AND revoked=0", (client_id,)).rowcount

    def revoke_all(self) -> int:
        with self._c() as c:
            return c.execute("UPDATE tokens SET revoked=1 WHERE revoked=0").rowcount
