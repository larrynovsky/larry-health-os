"""Метка host_only (решение владельца 30.09, вариант Б): в контейнере тест станка пропускается С ПРИЧИНОЙ,
на хосте — бежит. Признак — HEALTH_RUNTIME образа, а не отсутствие git: иначе у разработчика без git
тесты станка исчезали бы молча. Судится настоящим pytest на настоящем помеченном файле."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
TARGET = "tests/unit/test_commit_exposure.py"      # помечен целиком (pytestmark), 1 тест


def _run(runtime):
    env = {k: v for k, v in os.environ.items() if k != "HEALTH_RUNTIME"}
    if runtime:
        env["HEALTH_RUNTIME"] = runtime
    return subprocess.run([sys.executable, "-m", "pytest", TARGET, "-q", "-rs", "-p", "no:cacheprovider"],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=120).stdout


def test_в_контейнере_пропуск_с_причиной_на_хосте_нет():
    inside = _run("container")
    assert "1 skipped" in inside and "host_only:" in inside, inside[-400:]
    outside = _run(None)
    assert "host_only:" not in outside, outside[-400:]      # на хосте метка не прячет тест
