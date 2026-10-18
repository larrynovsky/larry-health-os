"""pip_audit_check зовёт pip_audit тем интерпретатором, которым запущен сам.

Джоб com.larry.health.pipaudit размещён в контейнере (placement.yaml), где плист подставляет
python3; жёсткий /opt/homebrew/bin/python3.11 там не существует. ЗАМЕР 15.10: в контейнере
logs/pip_audit_latest.json от 12.10 03:30 — status=error, «FileNotFoundError: [Errno 2] No such
file or directory: '/opt/homebrew/bin/python3.11'».

Тест не зависит от машины (ревью ремонта 11.10, §20): интерпретатор подменяется ДО загрузки модуля
на путь, которого нет ни на Маке, ни в контейнере, а поддельный запуск ведёт себя как ОС —
несуществующий путь даёт FileNotFoundError. Старый код красный на любой машине, новый — зелёный.
Реальный аудит не исполняется.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
FAKE_PY = "/nonexistent/test-interpreter/python3"


def _load(monkeypatch):
    monkeypatch.setattr(sys, "executable", FAKE_PY)
    spec = importlib.util.spec_from_file_location(
        "pip_audit_check_under_test", ROOT / "scripts" / "pip_audit_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_run(calls):
    def run(argv, **kw):
        calls.append(argv)
        if argv[0] != FAKE_PY:   # как ОС: «интерпретатора» по этому пути нет
            raise FileNotFoundError(2, "No such file or directory", argv[0])
        return subprocess.CompletedProcess(argv, 0, stdout='{"dependencies": []}', stderr="")
    return run


def test_audit_runs_with_current_interpreter(tmp_path, monkeypatch):
    mod = _load(monkeypatch)
    out = tmp_path / "logs" / "pip_audit_latest.json"
    monkeypatch.setattr(mod, "OUT", out)
    calls = []
    monkeypatch.setattr(mod.subprocess, "run", _fake_run(calls))
    mod.main()
    res = json.loads(out.read_text())
    assert calls and calls[0][0] == FAKE_PY and calls[0][1:3] == ["-m", "pip_audit"], calls
    assert res["status"] == "ok" and res["error"] is None, res
