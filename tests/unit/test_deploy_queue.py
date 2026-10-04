"""Очередь деплоя контейнера: одно место ожидания, второй ждущий уходит молча (нить queue-and-plain, 03.10).

Замер 03.10: «контейнер владельца не обновился: очередь деплоя» — 10 тревог за сутки. Закрытие пяти
нитей подряд давало хвост ждущих, последние сдавались через 15 минут, а их коммит всё равно доезжал
с первым ждущим (он читает HEAD после захвата замка). Исполняется настоящий скрипт до шва
HEALTH_DEPLOY_QUEUE_ONLY — сборка и compose тестом не трогаются.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy_container.sh"


def _env(tmp_path):
    return dict(os.environ, HOME=str(tmp_path), HEALTH_DEPLOY_LOCK=str(tmp_path / "lock"),
                HEALTH_DEPLOY_QUEUE_ONLY="1", HEALTH_DEPLOY_WAIT_S="0.1", HEALTH_DEPLOY_WAIT_TRIES="300")


def _log(tmp_path) -> str:
    p = tmp_path / "Library" / "Logs" / "health-container-deploy.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def test_second_waiter_leaves_first_gets_the_lock(tmp_path):
    holder = subprocess.Popen(["sleep", "30"])
    try:
        (tmp_path / "lock").mkdir()
        (tmp_path / "lock" / "pid").write_text(str(holder.pid))
        first = subprocess.Popen(["bash", str(SCRIPT)], env=_env(tmp_path))
        for _ in range(100):
            if (tmp_path / "lock.next" / "pid").exists():
                break
            time.sleep(0.05)
        assert (tmp_path / "lock.next" / "pid").read_text().strip() == str(first.pid)

        second = subprocess.run(["bash", str(SCRIPT)], env=_env(tmp_path), timeout=10)
        assert second.returncode == 0
        assert "место в очереди занято" in _log(tmp_path)

        holder.kill()
        holder.wait()
        assert first.wait(timeout=20) == 0, "первый ждущий обязан получить замок"
        assert not (tmp_path / "lock").exists() and not (tmp_path / "lock.next").exists()
        assert "FAIL" not in _log(tmp_path)
    finally:
        if holder.poll() is None:
            holder.kill()


def test_free_lock_goes_straight_through(tmp_path):
    r = subprocess.run(["bash", str(SCRIPT)], env=_env(tmp_path), timeout=10)
    assert r.returncode == 0 and not (tmp_path / "lock").exists()
