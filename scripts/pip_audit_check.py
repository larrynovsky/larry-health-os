#!/usr/bin/env python3.11
"""scripts/pip_audit_check.py — weekly CVE-аудит установленного окружения (SEC-19).

Запускается launchd `com.larry.health.pipaudit` (пн 03:30, Studio). Сеть нужна
только здесь; nightly-читатель — security_sensors._pip_audit (integrity §[12],
без сети) — доставляет находки через triage → Telegram.

Разделение ролей (feedback_stale_intermediate_layer: слой обязан иметь
staleness-датчик): этот скрипт пишет logs/pip_audit_latest.json с timestamp;
читатель поднимает тревогу и на уязвимости, И на протухший файл (джоб мёртв).

Сам скрипт НЕ алертит и НЕ решает — только собирает факт.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "logs" / "pip_audit_latest.json"
PY = "/opt/homebrew/bin/python3.11"  # launchd PATH пуст — полный путь


def main() -> int:
    res: dict = {
        "generated": time.time(),
        "generated_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "error",
        "vulns": [],
        "error": None,
    }
    try:
        proc = subprocess.run(
            [PY, "-m", "pip_audit", "-f", "json", "--progress-spinner", "off"],
            capture_output=True, text=True, timeout=1800,
        )
        # exit 0 = чисто, exit 1 = найдены уязвимости; прочее — сбой самого аудита
        if proc.returncode in (0, 1) and proc.stdout.strip():
            data = json.loads(proc.stdout)
            deps = data.get("dependencies", data if isinstance(data, list) else [])
            for d in deps:
                for v in d.get("vulns") or []:
                    res["vulns"].append({
                        "name": d.get("name"),
                        "version": d.get("version"),
                        "id": v.get("id"),
                        "fix_versions": v.get("fix_versions") or [],
                    })
            res["status"] = "vulns" if res["vulns"] else "ok"
        else:
            res["error"] = (proc.stderr or "пустой вывод")[-500:]
    except Exception as e:  # noqa: BLE001 — статус error доставит читатель
        res["error"] = f"{type(e).__name__}: {e}"

    OUT.parent.mkdir(exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    tmp.replace(OUT)  # атомарно (§7.3.1 Таненбаума, как в import-пайплайне)
    print(f"pip-audit: status={res['status']} vulns={len(res['vulns'])}"
          + (f" error={res['error'][:80]}" if res["error"] else ""))
    return 0  # сбой аудита — не сбой джоба: факт записан, тревогу поднимет читатель


if __name__ == "__main__":
    sys.exit(main())
