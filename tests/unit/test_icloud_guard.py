"""Тесты не дотягиваются до настоящего iCloud (нить icloud-test-guard, 28.09, §20).

Замер 28.09: полный прогон на Studio повис на 49% — pytest читал документ человека в
iCloud/health/CR/, который iCloud выгрузил (dataless) и не отдавал. Путь брался так: в копии
канона лежит «CR/<файл>.pdf», модуль склеивал его с облаком из private/infra.yaml. Зелёный
такого теста был причинён окружением машины — класс §20.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import infra_config

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]


def _real_icloud() -> str:
    import pwd
    return os.path.join(pwd.getpwuid(os.getuid()).pw_dir, "Library", "Mobile Documents")


def test_run_cloud_is_not_real_icloud():
    assert not str(infra_config.cloud_dir("CR")).startswith(_real_icloud())
    assert not str(infra_config.HAE_APP_DIR).startswith(_real_icloud())


def test_child_process_cloud_is_not_real_icloud():
    """Дочерний процесс теста получает облако прогона через HEALTH_CLOUD_DIR."""
    out = subprocess.run([sys.executable, "-c", "import infra_config; print(infra_config.cloud_dir('CR'))"],
                         cwd=_ROOT, capture_output=True, text=True, timeout=60, env=dict(os.environ))
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() and not out.stdout.strip().startswith(_real_icloud())


def test_guard_blocks_real_icloud_open():
    p = os.path.join(_real_icloud(), "com~apple~CloudDocs", "health", "CR", "__nonexistent__.pdf")
    with pytest.raises(PermissionError, match="НАСТОЯЩЕГО iCloud"):
        open(p, "rb")
    with pytest.raises(PermissionError, match="НАСТОЯЩЕГО iCloud"):
        os.listdir(os.path.join(_real_icloud(), "com~apple~CloudDocs"))


def test_guard_ignores_fake_home_icloud(tmp_path, monkeypatch):
    """Тест, построивший «iCloud» во временном доме, стражем не задет."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    fake = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/health/data"
    fake.mkdir(parents=True)
    (fake / "x.txt").write_text("ok")
    assert (fake / "x.txt").read_text() == "ok"
