"""Withings: авторизация и замеры давления — каждый со своим временем.

Зачем (нить withings-bp, 2026-10-04): давление тонометра Withings шло к нам только через
Apple Health → Health Auto Export и с одной из летних дат перестало приходить — три месяца тишины никто не
заметил. Прямой API Withings — второй источник, независимый от того пути: отдаёт каждый замер
с временем и пульсом и позволяет сторожу увидеть «у Withings замер есть, у нас нет».

Секреты: secrets/withings_client_id, secrets/withings_client_secret; первичная выдача —
secrets/withings_oauth.json (только чтение), живой токен после ротации — data/withings_oauth.json.
Withings МЕНЯЕТ токен обновления при каждом обновлении (старый живёт 8 ч): новый сохраняется
до использования. Ответ Withings — HTTP 200 даже на ошибку, статус в теле: не 0 → отказ.
Публичный контракт: contracts/withings_api.json. Базы не касается.
"""
from contextlib import contextmanager
import fcntl
import http.server
import json
import math
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

from _time_inject import get_now
from secrets_paths import oauth_token_source, secrets_dir

AUTHORIZE_URL = "https://account.withings.com/oauth2_user/authorize2"
TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"
MEASURE_URL = "https://wbsapi.withings.net/measure"
REDIRECT_URI = "http://localhost:9877/withings/callback"
SCOPE = "user.metrics"
TOKEN_NAME = "withings_oauth.json"
SYSTOLIC, DIASTOLIC, PULSE = 10, 9, 11          # коды мер Withings; сверить живым ответом (C-28)
# attrib группы замеров (OpenAPI Withings): 1 — снят прибором, но может принадлежать другому
# пользователю. Тонометр общий (живой случай 06.10: замер другого человека попал в давление владельца),
# неотнесённый замер в давление владельца не идёт; отнесённый в приложении получает attrib 0.
AMBIGUOUS = 1


class WithingsError(RuntimeError):
    """Отказ Withings или нашей стороны; текст без токенов, кодов и тела ответа."""


@contextmanager
def _lock(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(data_dir / ".withings_oauth.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _store(path: Path, token: dict):
    """Атомарно, 0600, fsync до возврата: потеря записи после ротации = потеря доступа."""
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
        raise WithingsError("токен Withings не сохранён (права или место на диске); авторизуйте заново") from None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _client() -> dict:
    out = {}
    for field in ("client_id", "client_secret"):
        try:
            value = (secrets_dir() / f"withings_{field}").read_text().strip()
        except OSError:
            raise WithingsError(f"нет файла withings_{field} в каталоге секретов") from None
        if not value:
            raise WithingsError(f"пустой withings_{field} в каталоге секретов")
        out[field] = value
    return out


def _post(url: str, fields: dict, bearer: str | None = None) -> dict:
    """POST формы; тело Withings {status, body}. Статус ≠ 0 — отказ, даже при HTTP 200."""
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise WithingsError(f"Withings HTTP {exc.code}") from None
    except (OSError, ValueError):
        raise WithingsError("Withings: сеть или неразборчивый ответ") from None
    status = payload.get("status") if isinstance(payload, dict) else None
    if status != 0:
        raise WithingsError(f"Withings status {status}")
    return payload.get("body") or {}


def _exchange(fields: dict) -> dict:
    body = _post(TOKEN_URL, {"action": "requesttoken", **_client(), **fields})
    for field in ("access_token", "refresh_token"):
        if not isinstance(body.get(field), str) or not body[field].strip():
            raise WithingsError(f"в ответе Withings нет {field}; авторизуйте заново")
    life = body.get("expires_in")
    if isinstance(life, bool) or not isinstance(life, (int, float)) or not math.isfinite(life) or life <= 0:
        raise WithingsError("неверный expires_in от Withings; авторизуйте заново")
    return {"access_token": body["access_token"], "refresh_token": body["refresh_token"],
            "expires_at": get_now().timestamp() + life}


def withings_access_token(data_dir: Path) -> str | None:
    """None ТОЛЬКО если Withings не подключён (нет ни выдачи, ни живого токена)."""
    seed, live = secrets_dir() / TOKEN_NAME, data_dir / TOKEN_NAME
    if not oauth_token_source(seed, live).exists():
        return None
    with _lock(data_dir):
        try:
            token = json.loads(oauth_token_source(seed, live).read_text())
            expiry = float(token["expires_at"])
            refresh = token["refresh_token"]
        except (OSError, ValueError, KeyError, TypeError):
            raise WithingsError("токен Withings нечитаем; авторизуйте заново") from None
        if expiry <= get_now().timestamp() + 60:
            token = _exchange({"grant_type": "refresh_token", "refresh_token": refresh})
            _store(live, token)        # до возврата: старый токен обновления уже погашен
        return token["access_token"]


def fetch_bp(access_token: str, lastupdate: int) -> list[dict]:
    """Замеры давления, изменённые после lastupdate (unix): [{ts, systolic, diastolic, pulse, grpid}].
    Группа без систолы или диастолы — не замер давления (весы шлют пульс отдельно)."""
    out, offset = [], None
    while True:
        fields = {"action": "getmeas", "meastypes": f"{SYSTOLIC},{DIASTOLIC},{PULSE}",
                  "category": 1, "lastupdate": int(lastupdate)}
        if offset is not None:
            fields["offset"] = offset
        body = _post(MEASURE_URL, fields, bearer=access_token)
        for grp in body.get("measuregrps") or []:
            vals = {m["type"]: m["value"] * 10 ** m["unit"] for m in grp.get("measures") or []
                    if isinstance(m, dict) and {"type", "value", "unit"} <= m.keys()}
            if grp.get("attrib") == AMBIGUOUS:   # прибор общий: замер, не отнесённый к владельцу, — не его
                continue
            if SYSTOLIC in vals and DIASTOLIC in vals:
                out.append({"ts": int(grp["date"]), "systolic": vals[SYSTOLIC],
                            "diastolic": vals[DIASTOLIC], "pulse": vals.get(PULSE),
                            "grpid": grp.get("grpid")})
        if not body.get("more"):
            return out
        if body.get("offset") in (None, offset):
            raise WithingsError("Withings: more=1 без нового offset — страница повторилась бы бесконечно")
        offset = body.get("offset")


def authorize(seed_path: Path) -> None:
    """Разовое подключение на машине с браузером: ссылка → «Allow» → код ловит локальный
    приёмник (код живёт 30 секунд — руками не успеть) → токен в seed_path (0600).
    Дальше seed_path кладётся в каталог секретов той установки, что будет читать данные."""
    state = secrets.token_urlsafe(32)
    url = AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": _client()["client_id"], "scope": SCOPE,
        "redirect_uri": REDIRECT_URI, "state": state})
    got: dict = {}
    path = urllib.parse.urlsplit(REDIRECT_URI).path

    class _Catch(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — имя метода задано http.server
            parts = urllib.parse.urlsplit(self.path)
            q = urllib.parse.parse_qs(parts.query)
            ok = (parts.path == path and q.get("state") == [state] and len(q.get("code", [])) == 1)
            if ok:
                got["code"] = q["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(("Готово, окно можно закрыть." if ok else "Не тот ответ — запустите заново.").encode())

        def log_message(self, *a):  # код авторизации не пишем в журнал
            pass

    # В контейнере браузер приходит через проброшенный порт хоста (docker compose run -p
    # 127.0.0.1:9877:9877) — внутри слушать надо все адреса контейнера: WITHINGS_CALLBACK_BIND=0.0.0.0.
    # Наружу порт открыт только на localhost хоста; state сверяется в любом случае.
    bind = os.environ.get("WITHINGS_CALLBACK_BIND", "127.0.0.1")
    srv = http.server.HTTPServer((bind, urllib.parse.urlsplit(REDIRECT_URI).port), _Catch)
    th = threading.Thread(target=srv.handle_request, daemon=True)
    th.start()
    print("Откройте ссылку в браузере и нажмите Allow:\n" + url, flush=True)
    th.join(timeout=300)
    srv.server_close()
    if "code" not in got:
        raise WithingsError("ответа от Withings не было за 5 минут или state не совпал; запустите заново")
    token = _exchange({"grant_type": "authorization_code", "code": got["code"], "redirect_uri": REDIRECT_URI})
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    _store(seed_path, token)
    os.chmod(seed_path, 0o600)
    print(f"Withings подключён; токен сохранён в {seed_path} (0600).")


if __name__ == "__main__":
    try:
        authorize(Path(sys.argv[1]) if len(sys.argv) > 1 else secrets_dir() / TOKEN_NAME)
    except (WithingsError, OSError, KeyboardInterrupt) as exc:
        print(f"Подключение Withings остановлено: {exc}", file=sys.stderr)
        sys.exit(1)
