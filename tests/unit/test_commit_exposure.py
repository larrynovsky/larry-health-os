"""Штамп экспозиции к захвату чужого файла (BL-THREAD-GATE-1, вариант А, 27.09).

Коммит из главной копии при живом дереве нити несёт tree=main threads=1; коммит из дерева
нити — tree=<ветка>. Хук ставится в песочном репозитории, боевые хуки не трогаются.
"""
import shutil
import subprocess
from pathlib import Path
import pytest

pytestmark = pytest.mark.host_only   # станок разработчика: git/хуки/Homebrew — в контейнере не судим

HOOK = Path(__file__).resolve().parents[2] / "scripts" / "git-hooks" / "prepare-commit-msg"


def _git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True).stdout


def _last_trailer(cwd):
    return _git(cwd, "log", "-1", "--format=%(trailers:key=Commit-Exposure,valueonly)").strip()


def test_exposure_trailer_main_vs_thread(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t"); _git(repo, "config", "user.name", "t")
    _git(repo, "config", "core.hooksPath", str(tmp_path / "hooks"))
    (tmp_path / "hooks").mkdir()
    shutil.copy(HOOK, tmp_path / "hooks" / "prepare-commit-msg")
    (repo / "a").write_text("1")
    _git(repo, "add", "a"); _git(repo, "commit", "-qm", "первый")
    assert _last_trailer(repo) == "tree=main threads=0"
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "thread/x", str(wt))
    (repo / "a").write_text("2")
    _git(repo, "commit", "-qam", "из главной при живой нити")
    assert _last_trailer(repo) == "tree=main threads=1"
    (wt / "b").write_text("1")
    _git(wt, "add", "b"); _git(wt, "commit", "-qm", "из нити")
    assert _last_trailer(wt) == "tree=thread/x threads=1"
