"""test_on_studio.sh: уборка стенда сначала гасит свой прогон, потом удаляет каталог.

05.10 управляющий процесс на MacBook погиб посреди прогона; ловушка EXIT удалила каталог
стенда, а pytest на Studio ещё ~10 минут гонял тесты в пустоте (6118 FileNotFoundError).
Здесь функция уборки исполняется по-настоящему: «Studio» — локальная оболочка (подменный ssh),
«прогон» — живой процесс sleep с pid в .run.pid. Старая уборка (только rm -rf) оставляет его
живым — этот тест краснеет."""
import os
import re
import signal
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "test_on_studio.sh"


def _cleanup_fn() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"# --- cleanup_staging: begin.*?\n(.*?)# --- cleanup_staging: end ---", text, re.S)
    assert m, "метки функции уборки пропали из test_on_studio.sh"
    return m.group(1)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # зомби (уже убит, не собран) — для нас мёртв
    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout
    return bool(st.strip()) and not st.strip().startswith("Z")


def test_уборка_гасит_прогон_до_удаления_каталога(tmp_path):
    root = tmp_path / "health_staging_runs"
    staging = root / "run.TESTXXXX"
    staging.mkdir(parents=True)
    run = subprocess.Popen(["sleep", "300"])
    try:
        (staging / ".run.pid").write_text(str(run.pid))
        fake = tmp_path / "bin"
        fake.mkdir()
        (fake / "ssh").write_text('#!/bin/bash\nexec bash -c "${@: -1}"\n')
        (fake / "ssh").chmod(0o755)
        sh = (f'{_cleanup_fn()}\nSTUDIO=studio STAGING_ROOT="{root}" STAGING="{staging}"\n'
              f'cleanup_staging\n')
        env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"}
        subprocess.run(["bash", "-c", sh], env=env, timeout=30, check=True)
        time.sleep(0.2)
        run.poll()
        assert not _alive(run.pid), "прогон пережил уборку — будет гонять тесты в удалённом каталоге"
        assert not staging.exists(), "каталог стенда не удалён"
    finally:
        if run.poll() is None:
            run.send_signal(signal.SIGKILL)


def test_удалённые_команды_оставляют_свой_pid():
    """Без pid в .run.pid уборке нечего гасить: обе долгие удалённые команды обязаны его писать
    и стартовать через exec (pid оболочки = pid команды)."""
    text = SCRIPT.read_text(encoding="utf-8")
    remote = [l for l in text.splitlines()
              if l.startswith('ssh "$STUDIO" "cd \'$STAGING\'') and ("pytest" in l or "integrity_code_guard" in l)]
    assert len(remote) == 2, remote
    for line in remote:
        assert r"echo \$\$ > .run.pid" in line and " exec $PY " in line, line
