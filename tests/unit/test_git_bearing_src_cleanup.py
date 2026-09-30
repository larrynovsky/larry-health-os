"""Снимок `tests/conftest.py::git_bearing_src` не переживает процесс, который его создал.

Замер 2026-09-29: снимок не убирался никогда — каждый прогон pytest оставлял в $TMPDIR
Studio копию рабочего дерева (~150 МБ). С 26.09 по 29.09 их набралось 234 (~35 ГБ),
вместе с локальными снимками Time Machine это съело диск.
Оракул краснеет, если уборку при выходе процесса снять.
"""
import subprocess
import sys
from pathlib import Path
import pytest

pytestmark = pytest.mark.host_only   # станок разработчика: git/хуки/Homebrew — в контейнере не судим

REPO = Path(__file__).resolve().parents[2]


def test_snapshot_removed_when_process_exits(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("x")
    code = ("import sys; from tests.conftest import git_bearing_src; "
            "print('SNAP=' + git_bearing_src(sys.argv[1]))")
    out = subprocess.run([sys.executable, "-c", code, str(src)], cwd=REPO,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-2000:]
    snap = Path([l for l in out.stdout.splitlines() if l.startswith("SNAP=")][-1][5:])
    assert snap.name == "repo" and snap.parent.name.startswith("git-bearing-src-")
    assert not snap.parent.exists(), f"снимок пережил процесс: {snap.parent}"
