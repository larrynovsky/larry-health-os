#!/usr/bin/env python3
"""Мост почты агентов: каталог тенанта health ↔ почтамт infra (post.py). Исполняется на ХОСТЕ Studio.

Договор — docs/reference/agent_mail_bridge.md (единственный дом). Зачем мост, а не почтамт в контейнере:
почтамт не удостоверяет отправителя (`deliver` проверяет только «есть в книге»), и процесс с правом писать
в его корень может послать письмо от имени любого агента. Мост ставит отправителя сам — по каталогу тенанта —
и не пускает письмо к агенту другого хозяина (почтамт этого не проверяет: инвариант owner_privacy в infra
не построен, замер реестра 2026-11-05).

  agent_mail_bridge.py            один проход по всем тенантам (так зовёт launchd раз в 60 с)
  agent_mail_bridge.py --selftest внесённые поломки краснеют
"""
from __future__ import annotations

import email
import email.policy
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
TENANTS = REPO / "private" / "agent_mail.yaml"
INFRA = Path(os.path.expanduser(os.environ.get("AGENT_POST_INFRA", "~/Projects/infra/agent_post")))
PY = os.environ.get("AGENT_POST_PY", sys.executable)
ID_RE = re.compile(r"^[0-9a-f]{32}$")
KEY_RE = re.compile(r"^[0-9a-f]{24}$")
BODY_MAX = 8000
TIMEOUT = 60


def _post(*args: str, stdin: str = "") -> str:
    r = subprocess.run([PY, str(INFRA / "post.py"), *args], input=stdin, capture_output=True, text=True, timeout=TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError(f"post {args[0]}: код {r.returncode}: {r.stderr.strip()[:300]}")
    return r.stdout


def _book() -> dict:
    doc = yaml.safe_load((INFRA / "addresses.yaml").read_text(encoding="utf-8"))
    return {a["id"]: a for a in doc["agents"]}


def _write(p: Path, data: dict) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _refuse(req: dict, book: dict, me: str) -> str | None:
    if not isinstance(req, dict) or not isinstance(req.get("to"), list) or not req["to"]:
        return "bad_json"
    if "from" in req:
        return "from_field"
    if not isinstance(req.get("body"), str) or len(req["body"]) > BODY_MAX:
        return "too_long"
    for r in req["to"]:
        if r not in book:
            return "unknown_rcpt"
        if book[r]["owner"] != book[me]["owner"]:
            return "foreign_owner"
    return None


def send_out(d: Path, me: str, book: dict) -> int:
    n = 0
    for f in sorted((d / "out").glob("*.json")):
        rid = f.stem
        if not ID_RE.match(rid):
            print(f"тревога: в out/ чужое имя {f.name}", file=sys.stderr)
            continue
        try:
            req = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            req = None
        why = _refuse(req, book, me)
        rec = {"id": rid, "message_id": None, "statuses": {}, "refused": why, "at": time.time()}
        if not why:
            args = ["send", "--from", me, "--to", ",".join(req["to"]), "--subject", str(req.get("subject") or "")]
            if req.get("reply_to"):
                args += ["--reply-to", str(req["reply_to"])]
            for line in _post(*args, stdin=req["body"]).splitlines():
                k, _, v = line.partition(" ")
                if k == "message-id":
                    rec["message_id"] = v.strip()
                elif k in req["to"]:
                    rec["statuses"][k] = v.strip()
        _write(d / "sent" / f"{rid}.json", rec)
        f.unlink()
        n += 1
    return n


def _raw(me_box: Path, mid: str) -> email.message.EmailMessage | None:
    for p in (me_box / "cur").glob("*"):
        try:
            m = email.message_from_bytes(p.read_bytes(), policy=email.policy.default)
        except OSError:
            continue
        if (m.get("Message-ID") or "").strip() == mid:
            return m
    return None


def pull_in(d: Path, me: str, book: dict, post_root: Path) -> int:
    n = 0
    for line in _post("inbox", me).splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[1] != "от" or not KEY_RE.match(parts[0]):
            continue
        key, sender, mid = parts[0], parts[2], " ".join(parts[3:])
        if (d / "in" / f"{key}.json").exists() or (d / "acked" / key).exists():
            continue
        wrapped = _post("read", me, key)                      # письмо уезжает в cur/ — дальше пишем сразу
        raw = _raw(post_root / "boxes" / book[me]["owner"] / me, mid)
        auto = bool(raw and raw.get("Auto-Submitted", "no").lower() != "no")
        if auto:
            _post("ack", me, key)
            (d / "acked" / key).touch()
            continue
        _write(d / "in" / f"{key}.json", {"key": key, "message_id": mid, "from": sender,
                                           "subject": str(raw.get("Subject", "")) if raw else "",
                                           "auto_submitted": False, "wrapped": wrapped, "at": time.time()})
        n += 1
    return n


def push_acks(d: Path, me: str) -> int:
    n = 0
    for f in sorted((d / "ack").glob("*")):
        if not KEY_RE.match(f.name):
            continue
        _post("ack", me, f.name)
        os.replace(f, d / "acked" / f.name)
        n += 1
    return n


def run_once() -> list[str]:
    """Один проход. Сбой одного тенанта не останавливает другого; книга не читается — не шлём никому."""
    tenants = yaml.safe_load(TENANTS.read_text(encoding="utf-8"))["tenants"]
    book = _book()
    post_root = Path(os.path.expanduser(os.environ.get("AGENT_POST_ROOT", "~/agent_post")))
    report = []
    for name, t in tenants.items():
        d, me = Path(os.path.expanduser(t["dir"])), t["agent"]
        try:
            if me not in book:
                raise RuntimeError(f"адреса {me} нет в книге почтамта")
            for sub in ("out", "sent", "in", "ack", "acked"):
                (d / sub).mkdir(parents=True, exist_ok=True)
            s, i, a = send_out(d, me, book), pull_in(d, me, book, post_root), push_acks(d, me)
            (d / "alive").write_text(str(int(time.time())))
            report.append(f"{name}: отправлено {s}, принято {i}, подтверждено {a}")
        except Exception as e:                                  # тенант не роняет соседа; alive не обновляется
            report.append(f"{name}: СБОЙ {type(e).__name__}: {e}")
    return report


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", str(REPO / "tests/unit/test_agent_mail_bridge.py")]))
    out = run_once()
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "; ".join(out))
    sys.exit(1 if any("СБОЙ" in x for x in out) else 0)
