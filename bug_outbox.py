#!/usr/bin/env python3.11
"""bug_outbox.py — доставка отчёта о сбое от установки до приёмника разработчика.

Нить bug-report. Форма отчёта — bug_report; здесь только путь: номер отчёта, отправка,
очередь на случай отсутствия связи. «Доставлено» человеку говорится ТОЛЬКО по квитанции
приёмника (номер R-…); нет квитанции — отчёт ложится в очередь, cron повторяет раз в час,
через трое суток без доставки человеку уходит одна строка «не получилось» (урок C-130:
слово о доставке — только по подтверждению в точке потребления).

Номер отчёта рождает установка: (случайный id установки, порядковый номер). Повтор после обрыва
связи несёт тот же номер, и приёмник отвечает прежней квитанцией, а не заводит второй отчёт.
id установки ни с чем не связан — 16 случайных шестнадцатеричных знаков.
"""
from __future__ import annotations

import fcntl
import json
import os
import secrets
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Адрес приёмника разработчика приезжает в образ выпуска (release.yml → HEALTH_BUG_RECEIVER из
# переменной репозитория), а не литералом в коде: в нём имя сети Tailscale, а дерево судит перепись
# личного (решение владельца 19.10 — адрес публичен в образе, в дереве его нет). Пусто — сборка не
# из выпуска (в том числе своя у владельца): отправлять некуда, человеку так и говорится.
RECEIVER = os.environ.get("HEALTH_BUG_RECEIVER", "")
GIVE_UP = timedelta(days=3)
TIMEOUT_S = 15


def _dir() -> Path:
    import health_db
    return Path(health_db.DB_PATH).parent


def next_report_id() -> str:
    """«<id установки>:<номер>»; id рождается при первом отчёте, номер растёт."""
    p = _dir() / "bug_report_id.json"
    with p.open("a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        try:
            state = json.loads(f.read() or "{}")
        except ValueError:
            state = {}
        state.setdefault("install", secrets.token_hex(8))
        state["seq"] = int(state.get("seq", 0)) + 1
        f.seek(0)
        f.truncate()
        f.write(json.dumps(state))
    return f"{state['install']}:{state['seq']}"


def _post(report: dict) -> tuple[int, dict] | None:
    """(код, тело) ответа приёмника или None, если связи нет вовсе."""
    req = urllib.request.Request(RECEIVER, data=json.dumps(report).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "health-os"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}
    except (urllib.error.URLError, OSError, ValueError):
        return None


def send(report: dict, post=_post) -> tuple[str, str | None]:
    """('delivered', 'R-…') | ('queued', None) | ('refused', причина-ключ).

    refused — приёмник ответил и отказал (схема, лимит): повтор того же не поможет, в очередь
    не кладём. Нет ответа или 5xx — очередь."""
    import bug_report
    if bug_report.schema_problems(report):
        return "refused", "schema"
    if not RECEIVER:
        return "refused", "no_receiver"
    res = post(report)
    if res is not None:
        code, body = res
        number = body.get("number") if isinstance(body, dict) else None
        if code in (200, 201) and isinstance(number, str) and number.startswith("R-"):
            return "delivered", number
        if code == 429:
            return "refused", "limit"
        if 400 <= code < 500:
            return "refused", "schema"
    _queue(report)
    return "queued", None


def _outbox() -> Path:
    return _dir() / "bug_report_outbox.jsonl"


def _queue(report: dict) -> None:
    with _outbox().open("a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps({"queued_at": datetime.now(timezone.utc).isoformat(), "report": report}) + "\n")


def drain(post=_post, now: datetime | None = None) -> dict:
    """Повторить очередь. → {'delivered': [номера], 'gave_up': N, 'left': N}. Зовёт cron раз в час."""
    now = now or datetime.now(timezone.utc)
    out = {"delivered": [], "gave_up": 0, "left": 0}
    p = _outbox()
    if not p.exists() or not RECEIVER:
        return out
    with p.open("r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        keep = []
        for line in f.read().splitlines():
            try:
                item = json.loads(line)
                age = now - datetime.fromisoformat(item["queued_at"])
            except (ValueError, KeyError, TypeError):
                continue                              # битая строка очереди — выбросить
            res = post(item["report"])
            if res is not None and res[0] in (200, 201) and str(res[1].get("number", "")).startswith("R-"):
                out["delivered"].append(res[1]["number"])
            elif res is not None and 400 <= res[0] < 500:
                out["gave_up"] += 1                   # приёмник отказал — повтор не поможет
            elif age > GIVE_UP:
                out["gave_up"] += 1
            else:
                keep.append(line)
        f.seek(0)
        f.truncate()
        f.writelines(x + "\n" for x in keep)
    out["left"] = len(keep)
    return out


def stuck(now: datetime | None = None) -> int:
    """Сколько отчётов лежит в очереди дольше, чем позволяет drain (трое суток + два часовых
    захода). Больше нуля — повторщик в cron не работает: человеку обещано «отправлю сам», а
    отправлять некому. Читатель — integrity_tests.check_bug_outbox_drains."""
    now = now or datetime.now(timezone.utc)
    try:
        lines = _outbox().read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    n = 0
    for line in lines:
        try:
            if now - datetime.fromisoformat(json.loads(line)["queued_at"]) > GIVE_UP + timedelta(hours=2):
                n += 1
        except (ValueError, KeyError, TypeError):
            n += 1                                    # битая строка, которую drain не убрал, — тоже застряла
    return n


def main(argv: list[str] | None = None) -> int:
    """Запасной вход, когда бот не отвечает (урок установки: `docker compose run --rm cron python3 -m bug_outbox`).

    Без аргументов — показать отчёт и спросить «отправить?»; --print — только напечатать отчёт
    (вставить в задачу на GitHub: в нём нет ничего личного по устройству)."""
    import sys
    import bug_report
    argv = sys.argv[1:] if argv is None else argv
    rep = bug_report.collect("other", "0" * 16 + ":0")
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    if "--print" in argv:
        return 0
    if input("\nОтправить разработчику? / Send to the developer? [y/N] ").strip().lower() not in ("y", "yes", "д", "да"):
        print("Ничего не отправлено. / Nothing was sent.")
        return 0
    rep["report_id"] = next_report_id()
    status, detail = send(rep)
    print({"delivered": f"Доставлено / Delivered: {detail}",
           "queued": "Нет связи — отчёт в очереди, бот отправит сам. / No connection — queued.",
           }.get(status, f"Не принято / Not accepted: {detail}"))
    return 0 if status != "refused" else 1


if __name__ == "__main__":
    raise SystemExit(main())
