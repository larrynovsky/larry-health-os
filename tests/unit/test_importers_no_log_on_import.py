"""Импорт импортёра не пишет файлов в домашнюю папку (нить treatment-tails, 04.10.2026).

Замер: на Studio в ~ лежало 392 пустых health_apple_import_run.*.log и столько же
health_oura_import_run.*.log — logging.basicConfig стоял на уровне модуля, и каждый прогон
тестов (каталог run.XXXX → суффикс журнала) создавал по файлу. Импорт идёт в отдельном
процессе: подмена модулей в общем прогоне — известная ловушка (sys.modules swap).
Красный на коде до нити.
"""
import os
import site
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("module", ["import_oura", "import_apple_health"])
def test_импорт_не_создаёт_журнал_в_домашней_папке(module, tmp_path):
    # PYTHONUSERBASE — чтобы подменённый HOME не спрятал пакеты, поставленные pip --user.
    env = dict(os.environ, HOME=str(tmp_path), PYTHONPATH=str(ROOT),
               PYTHONUSERBASE=site.getuserbase())
    r = subprocess.run([sys.executable, "-c", f"import {module}"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    leaked = [p.name for p in tmp_path.iterdir() if p.name.startswith("health_")]
    assert leaked == [], f"{module} при импорте создал {leaked}"
