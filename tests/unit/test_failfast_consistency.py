"""test_on_studio.sh: красные проверки согласованности останавливают прогон ДО полного pytest.

C-174 (07.10): нить canon-provenance узнала о красном consistency-тесте только в конце
13-минутного полного прогона. Блок fail-fast исполняется здесь по-настоящему: «Studio» —
локальная оболочка (подменный ssh), «pytest» — подменный интерпретатор, который пишет свои
аргументы в журнал и отвечает заданным кодом. Без блока или с потерянным кодом возврата
красный набор не останавливает прогон — тест краснеет."""
import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "test_on_studio.sh"


def _block() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"# --- failfast_consistency: begin.*?\n(.*?)# --- failfast_consistency: end ---",
                  text, re.S)
    assert m, "метки блока fail-fast пропали из test_on_studio.sh"
    return m.group(1)


def _run(tmp_path, partial: int, rc: int):
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "ssh").write_text('#!/bin/bash\nexec bash -c "${@: -1}"\n')
    (fake / "ssh").chmod(0o755)
    log = tmp_path / "py.log"
    py = fake / "py"
    py.write_text(f'#!/bin/bash\necho "$@" >> "{log}"\nexit {rc}\n')
    py.chmod(0o755)
    staging = tmp_path / "staging"
    staging.mkdir()
    sh = (f'set -euo pipefail\nSTUDIO=studio STAGING="{staging}" STAGING_SECRETS=s PY="{py}" '
          f'PARTIAL={partial}\n{_block()}\necho AFTER_BLOCK\n')
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"}
    r = subprocess.run(["bash", "-c", sh], env=env, capture_output=True, text=True, timeout=30)
    calls = log.read_text().splitlines() if log.exists() else []
    return r, calls


def test_красный_consistency_останавливает_до_полного(tmp_path):
    r, calls = _run(tmp_path, partial=0, rc=1)
    assert r.returncode != 0, "красный tests/consistency не остановил прогон"
    assert "AFTER_BLOCK" not in r.stdout, "после красного fail-fast прогон пошёл дальше"
    assert len(calls) == 1 and "tests/consistency" in calls[0]


def test_зелёный_consistency_пропускает_к_полному(tmp_path):
    r, calls = _run(tmp_path, partial=0, rc=0)
    assert r.returncode == 0 and "AFTER_BLOCK" in r.stdout
    assert len(calls) == 1 and "tests/consistency" in calls[0]


def test_частичный_прогон_не_гоняет_fail_fast(tmp_path):
    r, calls = _run(tmp_path, partial=1, rc=1)
    assert r.returncode == 0 and "AFTER_BLOCK" in r.stdout
    assert calls == [], "селектор — уже подмножество, fail-fast там лишний"
