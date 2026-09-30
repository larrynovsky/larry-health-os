#!/usr/bin/env python3
"""Проба чистого клона — оракул онбординга (plans/PLAN_onboarding_2026-09-23.md, Э0).

Что проверяет: посторонний получает ТОЛЬКО публичную зону (pii_census.public_files) и
поднимает систему на своей машине. Ступени:
  export  — публичная зона во временный каталог (без private/ и без данных владельца);
  install — scripts/install.py --apply --non-interactive во временный дом;
  init_db — схема БД создаётся и пишется в каталоге установщика;
  launchd — плисты фоновых сервисов собраны из шаблонов и читаются как plist (не грузятся);
  smoke   — ключевые модули импортируются в окружении установщика.

Изоляция: HOME, HEALTH_DATA_DIR и HEALTH_SECRETS_DIR — временные, данные и секреты
владельца недостижимы. НЕ изолировано (названо): python-пакеты — берутся из окружения
запускающего (user-site пробрасывается через PYTHONPATH); установка зависимостей не
проверяется. Имя хоста — настоящее: машина пробы — «чужая» только если конфиг установки
не называет её основной.

Выход 0 — все ступени зелёные; 1 — печатает каждую ступень и причину первой красной.
"""
from __future__ import annotations

import os
import shutil
import site
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SMOKE_MODULES = ["health_db", "labs_db", "clinical_kb", "region_pack", "norm_documents",
                 "safety_net", "signal_family", "telegram_bot", "dashboard", "jobs.scheduled",
                 "night_cycle", "gp_agent", "food_floor", "constitution_analysis",
                 "pubmed_client", "treatment_extractor", "infra_config"]


def _env(work: Path) -> dict:
    """Окружение с НУЛЯ, не наследованное: у чужого нет наших переменных. Наследование
    дало ложный ответ под pytest — conftest ставит ALLOW_WRITE_NONPRIMARY, и проба
    «проходила» ступень основной машины (замер 2026-09-23)."""
    keep = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TMPDIR") if k in os.environ}
    return {**keep, "HOME": str(work / "home"),
            "HEALTH_DATA_DIR": str(work / "home" / "health"),
            "HEALTH_SECRETS_DIR": str(work / "home" / ".health_secrets"),
            "PYTHONPATH": site.getusersitepackages()}


def _run(cmd: list[str], cwd: Path, env: dict) -> tuple[bool, str]:
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
    tail = (r.stdout + r.stderr).strip().splitlines()[-6:]
    return r.returncode == 0, "\n".join(tail)


def probe(work: Path) -> list[tuple[str, bool, str]]:
    sys.path.insert(0, str(ROOT))
    import pii_census
    repo = work / "repo"
    for f in pii_census.public_files(ROOT):
        src = ROOT / f
        if src.is_file():
            dst = repo / f
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    (work / "home" / "health" / "data").mkdir(parents=True, exist_ok=True)
    env, py = _env(work), sys.executable
    out = [("export", (repo / "health_db.py").exists(), f"{len(list(repo.rglob('*')))} путей")]
    inst = repo / "scripts" / "install.py"
    out.append(("install", *(_run([py, str(inst), "--apply", "--non-interactive", "--launchd"], repo, env)
                             if inst.exists() else (False, "нет scripts/install.py"))))
    out.append(("init_db", *_run([py, "-c", "import health_db as d; d.init_db(); "
                                  "import sqlite3; c=sqlite3.connect(d.DB_PATH); "
                                  "c.execute('CREATE TABLE _probe(x)'); c.commit()"], repo, env)))
    built = sorted((repo / "build" / "launchd").glob("*.plist"))
    import plistlib
    bad = []
    for f in built:
        try:
            plistlib.loads(f.read_bytes())
        except Exception as e:   # plist, который launchd не прочтёт
            bad.append(f"{f.name}: {e}")
    out.append(("launchd", bool(built) and not bad, "; ".join(bad) or "плисты не собраны"))
    smoke = "; ".join(f"import {m}" for m in SMOKE_MODULES)
    out.append(("smoke", *_run([py, "-c", smoke], repo, env)))
    return out


def main(argv: list[str] | None = None) -> int:
    with tempfile.TemporaryDirectory(prefix="clean_clone_") as t:
        res = probe(Path(t))
    for name, ok, why in res:
        print(f"{'✅' if ok else '❌'} {name}" + ("" if ok else f"\n   {why.replace(chr(10), chr(10) + '   ')}"))
    return 0 if all(ok for _, ok, _ in res) else 1


if __name__ == "__main__":
    sys.exit(main())
