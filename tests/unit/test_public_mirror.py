"""Зеркало публичной зоны: приватное и незакоммиченное не уезжают, удалённое удаляется (28.09)."""
import subprocess

import pytest

from scripts import public_mirror as pm

pytestmark = [pytest.mark.unit, pytest.mark.host_only]


def _git(d, *a):
    return subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True, text=True).stdout


def _repo(tmp_path):
    r = tmp_path / "work"
    (r / "private").mkdir(parents=True)
    (r / "plans").mkdir()
    (r / "publication_zones.yaml").write_text(
        "private:\n  - glob: \"private/*\"\n    why: t\n  - glob: \"plans/*\"\n    why: t\n"
        "  - glob: \"CLAUDE.md\"\n    why: t\n")
    (r / "a.py").write_text("print(1)\n")
    (r / "private" / "terms.yaml").write_text("secret: 1\n")
    (r / "plans" / "p.md").write_text("plan\n")
    (r / "CLAUDE.md").write_text("rules\n")
    _git(tmp_path, "init", "-q", str(r))
    _git(r, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    _git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c1")
    return r


def test_приватное_и_незакоммиченное_не_уезжают_удалённое_удаляется(tmp_path):
    root = _repo(tmp_path)
    dest = tmp_path / "mirror"
    _git(tmp_path, "init", "-q", str(dest))
    (root / "draft.py").write_text("not committed\n")          # незакоммиченное
    sha, n = pm.export("HEAD", dest, root)
    got = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*")
                 if p.is_file() and ".git" not in p.parts)
    assert got == ["a.py", "publication_zones.yaml"] and n == 2
    (dest / "stale.txt").write_text("from an old export\n")    # удалённое в работе — удаляется
    _git(root, "rm", "-q", "a.py")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c2")
    pm.export("HEAD", dest, root)
    assert not (dest / "a.py").exists() and not (dest / "stale.txt").exists()
    assert (dest / ".git").is_dir()


def test_пуш_ждёт_чтения_изменённых_публичных_файлов(tmp_path):
    root = _repo(tmp_path)
    ci = ["-c", "user.name=t", "-c", "user.email=t@t"]
    assert pm.unread_public("HEAD", root) == ["a.py", "publication_zones.yaml"]   # нет отметки
    c1 = _git(root, "rev-parse", "HEAD").strip()
    (root / pm.READ_MARK).write_text(f"# прочитано\n{c1}\n")
    assert len(pm.unread_public("HEAD", root)) == 2      # отметка не закоммичена — не считается
    _git(root, *ci, "add", "-A")
    _git(root, *ci, "commit", "-qm", "mark")
    assert pm.unread_public("HEAD", root) == []
    (root / "plans" / "p.md").write_text("plan 2\n")                  # приватное — читать не нужно
    (root / "a.py").write_text("print(2)  # замер на живых данных\n")
    (root / "b.py").write_text("new\n")
    _git(root, *ci, "add", "-A")
    _git(root, *ci, "commit", "-qm", "c2")
    assert pm.unread_public("HEAD", root) == ["a.py", "b.py"]


def test_машинная_строка_версии_не_требует_чтения(tmp_path):
    root = _repo(tmp_path)
    ci = ["-c", "user.name=t", "-c", "user.email=t@t"]
    (root / "MAP.md").write_text("# map\n**Версия:** 1.1 | **Дата:** 2040-01-01\ntext\n")
    _git(root, *ci, "add", "-A")
    _git(root, *ci, "commit", "-qm", "c2")
    (root / pm.READ_MARK).write_text(_git(root, "rev-parse", "HEAD"))
    _git(root, *ci, "add", "-A")
    _git(root, *ci, "commit", "-qm", "mark")
    (root / "MAP.md").write_text("# map\n**Версия:** 1.2 | **Дата:** 2040-01-02\ntext\n")
    _git(root, *ci, "commit", "-qam", "c3")
    assert pm.unread_public("HEAD", root) == []
    (root / "MAP.md").write_text("# map\n**Версия:** 1.3 | **Дата:** 2040-01-03\nзамер на живых данных\n")
    _git(root, *ci, "commit", "-qam", "c4")
    assert pm.unread_public("HEAD", root) == ["MAP.md"]


def test_незакоммиченная_отметка_не_открывает_пуш(tmp_path):
    """30.09: пуш прошёл по отметке, которая висела на диске незакоммиченной."""
    root = _repo(tmp_path)
    ci = ["-c", "user.name=t", "-c", "user.email=t@t"]
    (root / "a.py").write_text("print(2)\n")
    _git(root, *ci, "commit", "-qam", "c2")
    (root / pm.READ_MARK).write_text(_git(root, "rev-parse", "HEAD"))   # только на диске
    assert pm.unread_public("HEAD", root) == ["a.py", "publication_zones.yaml"]
