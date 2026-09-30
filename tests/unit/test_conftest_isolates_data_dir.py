"""conftest уводит данные тестов в tmp, если HEALTH_DATA_DIR не задан — и на основной машине тоже.

Приёмка урока установки свежим агентом 2026-09-24: у постороннего машина основная (её объявил
установщик), переменной нет — и тесты шли по его боевому ~/health. Проверяем настоящим
процессом: conftest исполняется при импорте, внутри текущего прогона его уже не переиграть.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_CODE = """
import socket, infra_config
socket.gethostname = lambda: infra_config.PRIMARY_HOST or "no-primary"   # как на основной машине
import runpy, os
runpy.run_path("tests/conftest.py")
print(os.environ.get("HEALTH_DATA_DIR", ""))
"""


def _data_dir_after_conftest(env_extra):
    env = {k: v for k, v in os.environ.items() if k not in ("HEALTH_DATA_DIR", "HEALTH_SECRETS_DIR")}
    env.update(env_extra)
    r = subprocess.run([sys.executable, "-c", _CODE], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    return r.stdout.strip().splitlines()[-1]


def test_без_переменной_данные_тестов_во_временном_каталоге():
    got = _data_dir_after_conftest({})
    assert got.startswith(tempfile.gettempdir()), f"данные тестов не уведены в tmp: {got!r}"


def test_явная_переменная_уважается(tmp_path):
    assert _data_dir_after_conftest({"HEALTH_DATA_DIR": str(tmp_path)}) == str(tmp_path)
