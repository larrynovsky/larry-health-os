"""Ночная проверка по данным человека судит ЕГО, а не владельца (28.09, нить tb-checks).

Бэкап и пороги должны принадлежать тому же тенанту, что и проверяемые данные.
Иначе исправный набор может получить ложный FAIL. В фикстуре используются
независимо придуманные размеры; оператору передаётся имя ошибки без значений.
"""
import os
import time

import pytest

pytestmark = pytest.mark.unit


def _touch(p, age=0):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    t = time.time() - age
    os.utime(p, (t, t))
    return p


def test_бэкапы_берутся_своего_тенанта(tmp_path):
    import integrity_tests as it
    owner = _touch(tmp_path / "health" / "backups" / "health_2026-09-28.db")
    mine_old = _touch(tmp_path / "health_t2" / "backups" / "health_t2_2026-09-27.db", age=86400)
    mine = _touch(tmp_path / "health_t2" / "backups" / "health_t2_2026-09-28.db")
    got = it._own_backups(tmp_path / "health_t2" / "data" / "health.db")
    assert got == [mine_old, mine] and owner not in got
    assert it._own_backups(tmp_path / "health" / "data" / "health.db") == [owner]


def test_без_своих_бэкапов_не_берёт_чужие(tmp_path):
    import integrity_tests as it
    _touch(tmp_path / "health" / "backups" / "health_2026-09-28.db")
    assert it._own_backups(tmp_path / "health_t2" / "data" / "health.db") == []


def test_ночная_проверка_человека_пишет_к_нему_и_зовёт_оператора(tmp_path):
    """Настоящий run_checks.sh --scheduled по данным не-владельца: вердикт в <DATA>/logs,
    общий logs/ владельца не тронут, оператору — имена проверок без значений данных."""
    import shutil
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    work, data = tmp_path / "repo", tmp_path / "health_t2"
    (work / "logs").mkdir(parents=True)
    shutil.copy(root / "run_checks.sh", work / "run_checks.sh")
    (work / "secrets_paths.py").write_text("def is_owner_data():\n    return False\n")
    (work / "notify.py").write_text(
        "def fault(msg, person_key=None):\n    open('fault.txt', 'w').write(msg)\n")
    (work / "oura_freshness_check.py").write_text("")
    (work / "memory_truthcheck.py").write_text("def apply_confirmations():\n    return {'bumped': 0}\n")
    report = '{"pass": 3, "fail": 1, "warn": 0, "failures": [["лаб-история усохла", "8 < 120"]]}'
    real = shutil.which("python3.11") or shutil.which("python3")
    stub = tmp_path / "py.sh"
    stub.write_text("#!/bin/bash\ncase \"$*\" in\n  *integrity_tests.py*--json*)\n"
                    f"    echo '{report}'; exit 1 ;;\nesac\nexec \"{real}\" \"$@\"\n")
    stub.chmod(0o755)
    env = dict(os.environ, HEALTH_PY=str(stub), HEALTH_DATA_DIR=str(data))
    subprocess.run(["bash", str(work / "run_checks.sh"), "--scheduled"], cwd=work, env=env,
                   capture_output=True, timeout=120)
    assert (data / "logs" / "integrity_latest.json").exists()
    assert not (work / "logs" / "integrity_latest.json").exists()
    msg = (work / "fault.txt").read_text()
    assert "tenant integrity failed" in msg and "120" not in msg
    assert not (work / "operator.txt").exists()
