#!/usr/bin/env python3.11
"""security_sensors.py — квартальный чеклист SECURITY.md как код (2026-07-06).

Одна публичная функция: collect_security_findings() → dict[категория, list[находка]].
Пустые списки = чисто. Категории:

  db_perms          — *.db в health*/backups, health*/data, <neighbour>/backups строго 600
  secrets_perms     — ~/.health_secrets и <neighbour-secrets>: каталог 700, файлы 600
  listen_ports      — не-loopback листенеры вне явного allowlist (lsof, не ss — macOS)
  hardcoded_tokens  — значения токенов в *.py/*.sh/*.plist репо (baseline-ратчет: ноль)

Дизайн-инварианты:
  * Никогда не бросает наружу: сбой категории → находка «датчик не отработал»
    в самой категории (громкий отказ вместо тихого, feedback_fallback_needs_sensor).
  * Находки токен-скана НЕ содержат само значение — иначе датчик нарушал бы
    трипвайр на секреты (CLAUDE.md §19, 2026-07-06).
  * Вход в integrity_tests в статусе WARN (burn-in), не гейтит утренний отчёт.
  * Не импортирует health_db — работает на любом хосте.

Потребитель: integrity_tests.py §[12]. Тесты: tests/unit/test_security_sensors.py.
Источник требований: SECURITY.md «Квартальный чеклист» + план
plan_agents_md_adoption_2026-07-06.md (iCloud health).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import infra_config

_LSOF = "/usr/sbin/lsof"  # launchd PATH пуст — только полный путь

# ── Allowlists (каждый пункт — с обоснованием; правки только с рацио) ─────────

# Файлы в каталогах секретов, которым разрешено НЕ быть 600.
_SECRETS_FILE_ALLOW: set[str] = set()
# ⚰️ "uncommitted_watchdog.heartbeat" — мьют снят 2026-08-03: файл переехал в
# logs/ (см. scripts/uncommitted_watchdog.py). Пустой allowlist здесь и есть
# норма: в каталоге секретов лежат только секреты, и каждый обязан быть 600.

# Не-loopback листенеры, признанные легитимными (baseline 2026-07-06, живой lsof Studio).
_LISTEN_ALLOW = {
    ("*", 5000),                      # AirPlay Receiver (ControlCenter), macOS-дефолт
    ("*", 7000),                      # AirPlay Receiver (ControlCenter), macOS-дефолт
    ("*", 18573),                     # локальный помощник соседнего проекта; SEC-17: bind localhost — рекомендация, принято
    (infra_config.STUDIO_HOST, 8001), # HTMX dashboard (F-1: без auth осознанно, tailnet)
    (infra_config.STUDIO_HOST, 8002), # dashboard тенанта-партнёра (F-1)
}
# Разрешение, привязанное к ПРОЦЕССУ, а не только к порту: (процесс, хост, порт).
# Порт 53 на всех интерфейсах, открытый чем-то другим, — это уже открытый DNS-сервер,
# и сторож обязан о нём звенеть; разрешён только этот конкретный источник.
_LISTEN_ALLOW_BY_PROCESS = {
    # Решение владельца 23.09 (вариант А). Colima (Docker для соседнего проекта,
    # его контейнеры; поставлена 17.09) пробрасывает DNS своей виртуальной машины на все
    # интерфейсы Studio. Замер 23.09 с MacBook: TCP-подключение по tailnet и по LAN
    # принимается, DNS-запрос обрывается сбросом — это не работающий резолвер; UDP 53
    # Studio не слушает. Хозяин Colima — соседний проект: закрыть проброс (правило ignore для
    # гостевого порта 53) — при её плановом перезапуске, не отсюда. Граница: «не отвечает»
    # — замер, не гарантия; сторож это больше не перепроверяет.
    ("limactl", "*", 53),
}
_EPHEMERAL_PORT_MIN = 49152  # rapportd/системные динамические — не наша зона

# Пути в репо, где совпадение токен-паттерна признано ложным (пока пусто —
# baseline = ноль совпадений, проверено на Studio 2026-07-06).
_TOKEN_SCAN_ALLOW: frozenset[str] = frozenset()

_TOKEN_PATTERNS = (
    ("anthropic", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("telegram", re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b")),
    ("github", re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("aws", re.compile(r"AKIA[0-9A-Z]{16}")),
)
_SCAN_SUFFIXES = {".py", ".sh", ".plist"}
_SCAN_SKIP_PARTS = {".git", "__pycache__", "node_modules", ".venv"}


# ── Категории (приватные, чистые где возможно) ────────────────────────────────

def _db_perms(home: Path) -> list[str]:
    roots: list[Path] = []
    roots += sorted(home.glob("health*/backups"))
    roots += sorted(home.glob("health*/data"))
    roots += [n["path"] / "backups" for n in infra_config.NEIGHBORS.values()
              if n.get("db") and (n["path"] / "backups").is_dir()]
    found = []
    for root in roots:
        for f in sorted(root.iterdir()):
            if f.suffix != ".db" or not f.is_file():
                continue
            mode = f.stat().st_mode & 0o777
            if mode & 0o077:
                found.append(f"{f} perms {oct(mode)[2:]} (канон 600)")
    return found


def repair_db_perms(home: Path | None = None, log=None) -> list[str]:
    """Авто-ремонт прав на .db (§13, ступень 1 — а не эскалация к человеку).

    Почему авто, а не алерт: `chmod 600` проходит весь конверт §13 — (a) safe-предикат
    тотальный и машинный (`mode & 0o077` на *.db в известных корнях), (b) ресурс не
    редактируется активно (это снапшот), (c) уникальная информация не теряется —
    права УЖЕСТОЧАЮТСЯ и операция обратима, (d) действие логируется. Держать это
    человеком значит еженощно просить его напечатать одну и ту же команду — то есть
    тренировать игнорировать алерты (§13 rationale, banner-blindness).

    ЗАМЕР 2026-08-07, отменивший исходную гипотезу: я планировал «чинить создателя,
    а не файлы», подозревая `backup_studio.sh`. Прочтение источника показало, что он
    УЖЕ делает `chmod 600` на каждый свой бэкап. Все 12 нарушителей — ad-hoc снапшоты
    перед разрушающими операциями (`health_preop_*`, `health_before_*`, §3), которые
    делает человек или агент в сессии. У такого создателя нет одного дома, поэтому
    единственный тотальный сторож — на стороне ЧТЕНИЯ прав, а не записи.

    Возвращает список починенного (след рецидива: если файл чинится каждую ночь —
    значит кто-то его каждую ночь и роняет, и это уже вопрос к создателю).
    Не чинится (нет прав) → остаётся находкой `_db_perms` и едет человеку.
    """
    home = home or Path.home()
    repaired = []
    for entry in _db_perms(home):
        path = Path(entry.split(" perms ")[0])
        try:
            path.chmod(0o600)
        except OSError:
            continue          # silent-ok: останется находкой _db_perms и доедет человеку
        repaired.append(str(path))
        if log:
            log(f"security:db_perms авто-ремонт: {path} → 600")
    return repaired


def _secrets_perms(home: Path) -> list[str]:
    found = []
    for d in (home / ".health_secrets", *(n["secrets"] for n in infra_config.NEIGHBORS.values()
                                          if n.get("secrets"))):
        if not d.is_dir():
            continue
        dmode = d.stat().st_mode & 0o777
        if dmode != 0o700:
            found.append(f"{d} perms {oct(dmode)[2:]} (канон 700)")
        # РЕКУРСИВНО (2026-09-02): iterdir() видел только верхний уровень, и подкаталог
        # с 644-файлами был слепым пятном — замер нашёл 12 таких в _env_cache. Чинить
        # только сам кэш значило бы чинить экземпляр: следующий подкаталог повторит.
        for f in sorted(d.rglob("*")):
            if f.name in _SECRETS_FILE_ALLOW:
                continue
            fmode = f.stat().st_mode & 0o777
            if f.is_dir():
                if fmode != 0o700:
                    found.append(f"{f} perms {oct(fmode)[2:]} (канон 700, подкаталог)")
            elif f.is_file() and fmode & 0o077:
                found.append(f"{f} perms {oct(fmode)[2:]} (канон 600)")
    return found


def _parse_listen(lsof_text: str) -> list[str]:
    """Чистый парсер вывода `lsof -nP -iTCP -sTCP:LISTEN` (тестируется напрямую)."""
    found: dict[tuple[str, int], str] = {}
    for line in lsof_text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        addr = next((p for p in reversed(parts) if ":" in p and not p.startswith("(")), None)
        if not addr:
            continue
        host, _, port_s = addr.rpartition(":")
        try:
            port = int(port_s)
        except ValueError:
            continue
        if host in ("*", "[::]", "0.0.0.0"):
            host = "*"
        if host.startswith("127.") or host in ("[::1]", "localhost"):
            continue  # loopback — зона F-5 закрыта, не наш датчик
        if port >= _EPHEMERAL_PORT_MIN:
            continue  # rapportd и системные динамические
        if (host, port) in _LISTEN_ALLOW or (parts[0], host, port) in _LISTEN_ALLOW_BY_PROCESS:
            continue
        found.setdefault((host, port), f"{parts[0]} слушает {host}:{port} — нет в allowlist")
    return [found[k] for k in sorted(found)]


def _listen_ports(lsof_text: str | None) -> list[str]:
    if lsof_text is None:
        proc = subprocess.run(
            [_LSOF, "-nP", "-iTCP", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=30,
        )
        # lsof возвращает 1 и при «ничего не найдено» — это не ошибка
        lsof_text = proc.stdout or ""
    return _parse_listen(lsof_text)


def _hardcoded_tokens(repo: Path) -> list[str]:
    found = []
    for f in sorted(repo.rglob("*")):
        if f.suffix not in _SCAN_SUFFIXES or not f.is_file():
            continue
        if _SCAN_SKIP_PARTS.intersection(f.parts):
            continue
        rel = str(f.relative_to(repo))
        if rel in _TOKEN_SCAN_ALLOW:
            continue
        try:
            if f.stat().st_size > 1_000_000:
                continue
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for kind, pat in _TOKEN_PATTERNS:
            for m in pat.finditer(text):
                line_no = text.count("\n", 0, m.start()) + 1
                # ЗНАЧЕНИЕ не включаем — трипвайр на секреты
                found.append(f"{rel}:{line_no} похоже на {kind}-токен")
    return found


# ── pip-audit (SEC-19): читатель weekly-файла, без сети ──────────────────────

# Advisory без доступного/применимого фикса — id сюда с обоснованием-комментом.
_PIP_AUDIT_ALLOW: frozenset[str] = frozenset()
_PIP_AUDIT_MAX_AGE_D = 8  # weekly (пн 03:30) + люфт; старше → джоб мёртв


def _pip_audit(repo: Path) -> list[str]:
    f = repo / "logs" / "pip_audit_latest.json"
    if not f.exists():
        return ["pip-audit ещё не запускался — джоб com.larry.health.pipaudit (пн 03:30)"]
    data = json.loads(f.read_text())
    found = []
    age_d = (time.time() - float(data.get("generated", 0))) / 86400
    if age_d > _PIP_AUDIT_MAX_AGE_D:
        found.append(f"pip_audit_latest.json устарел ({age_d:.0f}д > {_PIP_AUDIT_MAX_AGE_D}д) — weekly job мёртв?")
    if data.get("status") == "error":
        found.append(f"pip-audit упал: {str(data.get('error'))[:120]}")
    for v in data.get("vulns", []):
        if v.get("id") in _PIP_AUDIT_ALLOW:
            continue
        fix = ", ".join(v.get("fix_versions") or []) or "фикса нет"
        found.append(f"{v.get('name')} {v.get('version')}: {v.get('id')} (fix: {fix})")
    return found


# ── tailscale exposure (SEC-20): live-экспозиция ↔ канон infra_config ────────

def _parse_tailscale(json_text: str) -> list[str]:
    """Чистый парсер `tailscale serve status --json`. Нераспарсили = КРАСНЫЙ:
    формат CLI — не API; слепой датчик обязан кричать, а не зеленеть."""
    try:
        data = json.loads(json_text)
        assert isinstance(data, dict)
    except Exception:
        return ["tailscale serve status --json не распарсился — датчик слеп, формат сменился?"]

    found: list[str] = []

    def _port(hostport: str) -> int | None:
        try:
            return int(hostport.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            return None

    funnel_ports = {_port(hp) for hp, on in (data.get("AllowFunnel") or {}).items() if on}
    funnel_ports.discard(None)

    for p in sorted(funnel_ports - infra_config.EXPECTED_FUNNEL_PORTS):
        found.append(f"ПУБЛИЧНО торчит порт :{p} — не в EXPECTED_FUNNEL_PORTS (канон infra_config)")
    for p in sorted(infra_config.EXPECTED_FUNNEL_PORTS - funnel_ports):
        found.append(f"ожидаемый Funnel :{p} выключен — публичный фасад соседнего проекта недоступен?")

    # TCP-пробросы (нить serve-exposure, 03.10): лежат в секции TCP, а не Web, и до сегодня не
    # судились — случайный `serve --tcp` на новый порт не загорелся бы. HTTPS-строки той же секции
    # (HTTPS: true) судит ветка Web ниже.
    for port, cfg in sorted((data.get("TCP") or {}).items(), key=lambda kv: str(kv[0])):
        fwd = (cfg or {}).get("TCPForward")
        if not fwd:
            continue
        want = infra_config.EXPECTED_TCP_FORWARDS.get(_port(f"x:{port}"))
        if want is None:
            found.append(f"неожиданный TCP-проброс :{port} → {fwd} — не в EXPECTED_TCP_FORWARDS (канон infra_config)")
        elif fwd != want:
            found.append(f"TCP-проброс :{port} → {fwd}, а в каноне {want}")

    web = data.get("Web") or {}
    for hostport, cfg in sorted(web.items()):
        port = _port(hostport)
        if port not in infra_config.EXPECTED_SERVE_PORTS:
            found.append(f"неожиданный Serve-эндпоинт {hostport} — дрейф экспозиции")
            continue
        handlers = {path: (h or {}).get("Proxy", "") for path, h in (cfg.get("Handlers") or {}).items()}
        if port in funnel_ports:
            expected = infra_config.EXPECTED_FUNNEL_HANDLERS.get(port, {})
            if handlers != expected:
                found.append(f"публичная поверхность :{port} != канону: {sorted(handlers)} vs {sorted(expected)}")
        else:
            for path, proxy in sorted(handlers.items()):
                m = re.match(r"https?://(127\.0\.0\.1|localhost):(\d+)", proxy)
                if not m or int(m.group(2)) not in infra_config.EXPECTED_SERVE_BACKEND_PORTS:
                    found.append(f"Serve {hostport}{path} → {proxy} — бекенд вне канона")
    return found


def _tailscale_exposure(ts_json: str | None) -> list[str]:
    if ts_json is None:
        proc = subprocess.run(
            [infra_config.TAILSCALE_BIN, "serve", "status", "--json"],
            capture_output=True, text=True, timeout=30,
        )
        ts_json = proc.stdout or ""
    return _parse_tailscale(ts_json)


# ── Публичная поверхность сервера MCP (нить mcp-finish, 07.10) ───────────────
# Узел tailnet health-mcp — единственное, что Health OS открывает в интернет (решения владельца
# 06.10: облачные помощники, отдельный узел). Датчик выше судит Funnel самой Studio, а этот узел ему
# не виден. Судим снаружи — тем же путём, каким ходят Claude и ChatGPT: публичный DNS → публичный
# вход Tailscale → наш сервер. Обещание: без токена — отказ, всё кроме MCP — 404. Если завтра
# на этот узел попадёт дашборд или вход перестанет требовать токен, датчик покраснеет.

_MCP_EXPECT_404 = ("/", "/labs", "/api/health")       # дашборд и его API не должны отвечать


def _mcp_resolve(host: str) -> list[str]:
    """A-записи из ПУБЛИЧНОГО DNS (DNS-over-HTTPS): системный резолвер в tailnet отдал бы
    адрес 100.x и обошёл бы Funnel — проверка была бы не про интернет."""
    import urllib.request
    req = urllib.request.Request(f"https://cloudflare-dns.com/dns-query?name={host}&type=A",
                                 headers={"Accept": "application/dns-json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    return [a["data"] for a in data.get("Answer") or [] if a.get("type") == 1]


def _mcp_fetch(ip: str, host: str, method: str, path: str) -> tuple[int, dict, bytes]:
    """HTTPS к конкретному адресу с проверкой сертификата по имени узла (как curl --resolve)."""
    import http.client
    import socket
    import ssl
    sock = ssl.create_default_context().wrap_socket(
        socket.create_connection((ip, 443), timeout=15), server_hostname=host)
    conn = http.client.HTTPSConnection(host, timeout=15)
    conn.sock = sock
    try:
        conn.request(method, path, body=b"{}" if method == "POST" else None,
                     headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read(4096)
    finally:
        conn.close()


def _mcp_public_surface(base: str | None, resolve=_mcp_resolve, fetch=_mcp_fetch) -> list[str]:
    """Пусто = снаружи отвечает только MCP, и только со входом. Сервер не заведён (нет
    MCP_PUBLIC_BASE) — судить нечего. Граница: проверяется один публичный адрес из DNS и
    перечисленные пути, а не всё пространство путей."""
    if not base:
        return []
    host = base.split("//", 1)[-1].split("/", 1)[0]
    ips = resolve(host)
    if not ips:
        return [f"{host}: публичной DNS-записи нет — облачные помощники сервер не найдут"]
    ip, found = ips[0], []
    st, hdr, _ = fetch(ip, host, "POST", "/mcp")
    if st >= 500:
        # 07.10 живьём: сразу после деплоя Funnel отдал 502 — сервер ещё поднимался. Это не дыра во
        # входе, а недоступность; назвать её надо своими словами, иначе разбор пойдёт не туда.
        return [f"POST /mcp → {st}: сервер не отвечает — облачные помощники сейчас не подключатся"]
    if st != 401:
        found.append(f"POST /mcp без токена → {st}, ждали 401: вход не требуется?")
    elif "resource_metadata=" not in hdr.get("www-authenticate", ""):
        found.append("POST /mcp → 401 без resource_metadata: клиенты не найдут, где входить")
    st, _, body = fetch(ip, host, "GET", "/.well-known/oauth-authorization-server")
    try:
        issuer = json.loads(body).get("issuer") if st == 200 else None
    except ValueError:
        issuer = None
    if issuer != base:
        found.append(f"метаданные входа: {st}, issuer={issuer!r} — ждали {base}")
    for path in _MCP_EXPECT_404:
        st, _, _ = fetch(ip, host, "GET", path)
        if st != 404:
            found.append(f"GET {path} → {st}, ждали 404: наружу открыто не только MCP")
    return found


# ── Публичный интерфейс ───────────────────────────────────────────────────────

def collect_security_findings(
    home: Path | None = None,
    repo: Path | None = None,
    lsof_text: str | None = None,
    ts_json: str | None = None,
) -> dict[str, list[str]]:
    """SEC-чеклист одним вызовом. Пустые списки = чисто.

    Аргументы — только для инъекции в тестах; в проде без аргументов.
    Сбой категории не роняет остальные: превращается в находку
    «датчик ... не отработал» в этой же категории.
    """
    home = home or Path.home()
    repo = repo or Path(__file__).resolve().parent
    categories = {
        "db_perms": lambda: _db_perms(home),
        "secrets_perms": lambda: _secrets_perms(home),
        "listen_ports": lambda: _listen_ports(lsof_text),
        "hardcoded_tokens": lambda: _hardcoded_tokens(repo),
        "pip_audit": lambda: _pip_audit(repo),
        "tailscale_exposure": lambda: _tailscale_exposure(ts_json),
        # Сервер MCP судится снаружи и из контейнера тоже: адрес — из окружения ночной проверки
        # (scripts/install.py кладёт MCP_PUBLIC_BASE в службу cron, когда сервер заведён).
        "mcp_public_surface": lambda: _mcp_public_surface(os.environ.get("MCP_PUBLIC_BASE")),
    }
    import plist_env_liveness
    if plist_env_liveness.in_container() and lsof_text is None and ts_json is None:
        # docker-install, этап 4: слушающие порты и Serve — свойства ХОСТА (Colima публикует
        # порты на Маке, Serve живёт в Tailscale Мака); из контейнера их не видно. Пустой список
        # здесь означал бы «чисто» — ложь; «не отработал» — ложь другого рода. Говорим прямо.
        why = "не судимо в контейнере: порты и Serve — свойства хоста, их судит хостовая проверка"
        categories["listen_ports"] = lambda: [why]
        categories["tailscale_exposure"] = lambda: [why]
    findings: dict[str, list[str]] = {}
    for name, fn in categories.items():
        try:
            findings[name] = fn()
        except Exception as e:  # noqa: BLE001 — громкий отказ вместо тихого
            findings[name] = [f"датчик {name} не отработал: {type(e).__name__}: {e}"]
    return findings
