"""health_mcp — сервер MCP Health OS для облачных помощников (Claude, ChatGPT).

Решения владельца 06.10: Health OS — одна память, помощники — окна в неё; сразу облако
и с телефона; ChatGPT тоже. Транспорт — streamable HTTP (один POST → один JSON-ответ),
вход — mcp_auth. Шаг 0 проверил вход обоих клиентов (ping). Шаг 1 — инструменты чтения
(mcp_tools): только через существующие функции *_db, с источником и датой, «нет данных»
вместо пустоты, без имени человека.

Окружение: MCP_PUBLIC_BASE — публичный https-адрес без хвоста; MCP_PORT (по умолчанию 8765);
MCP_BIND (по умолчанию 127.0.0.1). Тенант задаёт HEALTH_DATA_DIR процесса, а не аргумент
запроса; состояние входа лежит рядом с базой тенанта — data/mcp_auth.sqlite. Не в каталоге
секретов: в контейнере он смонтирован только на чтение (поймал полный прогон 06.10 —
сторож классов секретов увидел новое имя).
"""
from __future__ import annotations

import html
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import mcp_auth
import mcp_tools

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = ("Health OS — личная медицинская память владельца. Отвечай только по ответам "
                "инструментов; если инструмент вернул «нет данных», так и скажи и не достраивай. "
                "Указывай источник и дату.")
TOOLS = [{
    "name": "ping",
    "description": "Проверка связи с Health OS. Данных о здоровье не возвращает.",
    "inputSchema": {"type": "object", "properties": {}},
}]
HTML = "text/html; charset=utf-8"
FORM_ACTION = " ".join(["'self'", *sorted({"https://" + u.split("/")[2] for u in
                                          [*mcp_auth.ALLOWED_REDIRECTS, *mcp_auth.ALLOWED_REDIRECT_PREFIXES]})])


def resource(base: str) -> str:
    return base + "/mcp"


def as_metadata(base: str) -> dict:
    """RFC 8414: что умеет сервер авторизации."""
    return {"issuer": base, "authorization_endpoint": base + "/authorize",
            "token_endpoint": base + "/token", "registration_endpoint": base + "/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": [mcp_auth.SCOPE], "client_id_metadata_document_supported": False,
            "authorization_response_iss_parameter_supported": True}


def pr_metadata(base: str) -> dict:
    """RFC 9728: где брать вход для этого ресурса."""
    return {"resource": resource(base), "authorization_servers": [base],
            "scopes_supported": [mcp_auth.SCOPE], "bearer_methods_supported": ["header"]}


def rpc(msg: dict) -> "dict | None":
    """JSON-RPC 2.0. Уведомление (без id) → None."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None
    if method == "initialize":
        want = params.get("protocolVersion")
        res = {"protocolVersion": want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
               "capabilities": {"tools": {}}, "serverInfo": {"name": "health-os", "version": "0"},
               "instructions": INSTRUCTIONS}
    elif method == "ping":
        res = {}
    elif method == "tools/list":
        res = {"tools": TOOLS + mcp_tools.TOOLS}
    elif method == "tools/call":
        name = params.get("name")
        if name == "ping":
            res = {"content": [{"type": "text", "text": "Health OS на связи."}], "isError": False}
        elif name in mcp_tools.NAMES:
            args = params.get("arguments")
            text, err = mcp_tools.call(name, args if isinstance(args, dict) else {})
            res = {"content": [{"type": "text", "text": text}], "isError": err}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "нет такого инструмента"}}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "метод не поддержан"}}
    return {"jsonrpc": "2.0", "id": mid, "result": res}


_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Health OS — подключение</title>
<style>body{{font:17px system-ui;max-width:28em;margin:3em auto;padding:0 1em}}
input{{font-size:1.4em;width:7em;letter-spacing:.2em}}button{{font-size:1.1em;padding:.4em 1.2em}}
.e{{color:#b00}}</style></head><body><h2>Подключение к Health OS</h2><p>{msg}</p>{form}</body></html>"""


def page(msg: str, req_id: "str | None" = None, err: str = "") -> bytes:
    """msg — наш текст; всё пришедшее снаружи экранируется до вызова."""
    form = ""
    if req_id:
        form = ('<form method="post" action="/authorize">'
                f'<input type="hidden" name="req_id" value="{html.escape(req_id)}">'
                '<input name="pin" inputmode="numeric" autocomplete="one-time-code" maxlength="6" autofocus> '
                '<button>Разрешить</button></form>')
    if err:
        msg = f'<span class="e">{html.escape(err)}</span><br>' + msg
    return _PAGE.format(msg=msg, form=form).encode()


def make_handler(store: "mcp_auth.Store", base: str, notify_owner):
    res = resource(base)

    class H(BaseHTTPRequestHandler):
        server_version = "health-os"

        def log_message(self, fmt, *args):
            # Токен, коды и параметры запроса в журнал не пишем: только метод, путь и статус.
            sys.stderr.write(f"health_mcp {self.command} {urlparse(self.path).path} "
                             f"{args[1] if len(args) > 1 else ''}\n")

        def _send(self, code: int, body: bytes = b"", ctype="application/json", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            # form-action действует и на редирект ПОСЛЕ отправки формы: с одним 'self' браузер не
            # пускал 302 на адрес возврата клиента, код сгорал, а человек видел ту же страницу
            # (замер 06.10, первое живое подключение Claude). Хосты — те же, что в mcp_auth.
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; style-src 'unsafe-inline'; form-action " + FORM_ACTION)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj, extra=None):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), extra=extra)

        def _body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1_000_000:
                raise mcp_auth.AuthError("invalid_request", "слишком большой запрос")
            return self.rfile.read(n) if n else b""

        def _form(self) -> dict:
            return {k: v[0] for k, v in parse_qs(self._body().decode("utf-8", "replace")).items()}

        def do_GET(self):
            u = urlparse(self.path)
            if u.path.startswith("/.well-known/oauth-protected-resource"):
                return self._json(200, pr_metadata(base))
            if (u.path.startswith("/.well-known/oauth-authorization-server")
                    or u.path.startswith("/.well-known/openid-configuration")):
                return self._json(200, as_metadata(base))
            if u.path == "/authorize":
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                try:
                    req_id, pin = store.begin(q, res)
                except mcp_auth.AuthError as e:
                    return self._send(400, page("Подключение отклонено: " + html.escape(e.detail or e.error)), HTML)
                if pin is None:
                    return self._send(200, page("Код уже отправлен в Telegram. Введите его:", req_id), HTML)
                name = store.client_name(q.get("client_id", ""))
                if not notify_owner(f"Health OS: «{name}» просит доступ к твоим данным. Код: {pin}\n"
                                    "Если подключал не ты — ничего не вводи."):
                    return self._send(503, page("Не удалось отправить код в Telegram — попробуйте позже."), HTML)
                return self._send(200, page("Код из 6 цифр пришёл в Telegram. Введите его:", req_id), HTML)
            if u.path == "/mcp":
                return self._send(405, extra={"Allow": "POST"})
            return self._send(404)

        def do_POST(self):
            u = urlparse(self.path)
            try:
                if u.path == "/register":
                    return self._json(201, store.register(json.loads(self._body() or b"{}")))
                if u.path == "/token":
                    return self._json(200, store.token(self._form(), res))
                if u.path == "/authorize":
                    f = self._form()
                    try:
                        loc = store.confirm(f.get("req_id", ""), f.get("pin", ""), base)
                    except mcp_auth.AuthError as e:
                        again = f.get("req_id") if e.detail == "неверный код" else None
                        return self._send(400, page("Введите код ещё раз:" if again else "Начните подключение заново.",
                                                    again, e.detail), HTML)
                    return self._send(302, extra={"Location": loc})
                if u.path == "/mcp":
                    auth = self.headers.get("Authorization", "")
                    tok = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
                    if not store.verify(tok, res):
                        return self._json(401, {"error": "invalid_token"}, extra={
                            "WWW-Authenticate": f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"'})
                    msg = json.loads(self._body() or b"null")
                    if isinstance(msg, list):
                        out = [r for r in (rpc(m) for m in msg if isinstance(m, dict)) if r]
                    else:
                        out = rpc(msg) if isinstance(msg, dict) else None
                    return self._json(200, out) if out else self._send(202)
                return self._send(404)
            except mcp_auth.AuthError as e:
                return self._json(400, {"error": e.error, "error_description": e.detail})
            except ValueError:          # json.JSONDecodeError — подкласс ValueError
                return self._json(400, {"error": "invalid_request"})

    return H


def main() -> None:
    import notify
    base = os.environ.get("MCP_PUBLIC_BASE", "").rstrip("/")
    if not base.startswith("https://"):
        raise SystemExit("health_mcp: MCP_PUBLIC_BASE обязан быть https-адресом")
    data = os.environ.get("HEALTH_DATA_DIR")
    if not data:
        raise SystemExit("health_mcp: нет HEALTH_DATA_DIR — чей это сервер, неизвестно")
    from pathlib import Path
    store = mcp_auth.Store(Path(data) / "data" / "mcp_auth.sqlite")
    srv = ThreadingHTTPServer((os.environ.get("MCP_BIND", "127.0.0.1"), int(os.environ.get("MCP_PORT", "8765"))),
                              make_handler(store, base, lambda text: notify.notify(text, fallback=False) == "telegram"))
    srv.serve_forever()


if __name__ == "__main__":
    main()
