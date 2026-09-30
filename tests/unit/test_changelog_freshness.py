"""Журнал изменений пишется за каждую слитую нить (нить changelog-at-merge, 23.09).

ЧТО ДОКАЗЫВАЕТСЯ. (1) «Работа нити» — коммиты второго родителя слияния без записок и
артефактов документации; считается на НАСТОЯЩЕМ git мини-репозитория. (2) Строка
журнала собирается из их заголовков и несёт метку нити; нить без кода строки не даёт.
(3) Сторож звенит на слитой нити с кодом без строки и молчит, когда строка есть.

ЧЕГО НЕ ДОКАЗЫВАЕТСЯ. Что заголовки коммитов правдивы — строка пересказывает автора
коммитов, не код.
"""
import subprocess

import pytest

pytestmark = pytest.mark.unit

import git_facts


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                    *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "a.py").write_text("x = 1\n")
    _git(r, "add", "."); _git(r, "commit", "-q", "-m", "начало")
    _git(r, "checkout", "-q", "-b", "thread/нить-один")
    (r / "a.py").write_text("x = 2\n")
    _git(r, "commit", "-q", "-am", "fix(a): x стал 2")
    (r / "n.md").write_text("записка\n")
    _git(r, "add", "."); _git(r, "commit", "-q", "-m", "handoff(нить-один): записка")
    _git(r, "checkout", "-q", "main")
    _git(r, "merge", "-q", "--no-ff", "-m", "Merge branch 'thread/нить-один'", "thread/нить-один")
    _git(r, "checkout", "-q", "-b", "thread/только-доки")
    (r / "d.md").write_text("док\n")
    _git(r, "add", "."); _git(r, "commit", "-q", "-m", "docs: только документация")
    _git(r, "checkout", "-q", "main")
    _git(r, "merge", "-q", "--no-ff", "-m", "Merge branch 'thread/только-доки'", "thread/только-доки")
    monkeypatch.setattr(git_facts, "ROOT", r)
    return r


@pytest.mark.host_only
def test_работа_нити_без_записок_и_документации(repo):
    merges = git_facts.thread_merges()
    assert [s for _, s in merges] == ["нить-один", "только-доки"]
    assert git_facts.thread_code_subjects(merges[0][0]) == ["fix(a): x стал 2"]
    assert git_facts.thread_code_subjects(merges[1][0]) == []


def test_строка_журнала_и_версия():
    import doc_agent as d
    row = d.thread_changelog_row("нить-один", ["fix(a): x | y"], "2026-09-23", "15.29")
    assert row == "| 2026-09-23 | 15.29 | fix(a): x / y [нить нить-один] |"
    assert d.thread_changelog_row("x", [], "2026-09-23", "15.29") is None
    assert d.bump_version("15.28") == "15.29" and d.bump_version("15.99") == "15.100"


def _sensor(monkeypatch, changelog):
    import integrity_tests as it
    cap = []
    monkeypatch.setattr(it, "warn", lambda n, d="": cap.append((n, d)))
    return it.check_changelog_freshness(changelog=changelog), cap


@pytest.mark.host_only
def test_сторож_звенит_на_нити_без_строки(repo, monkeypatch, tmp_path):
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text("| 2026-09-22 | 15.28 | старое |\n", encoding="utf-8")
    res, cap = _sensor(monkeypatch, cl)
    assert len(cap) == 1 and "нить-один" in cap[0][1] and "только-доки" not in cap[0][1]
    assert res == {"thread_merges": 2, "missing": 1}


@pytest.mark.host_only
def test_сторож_молчит_когда_строка_есть(repo, monkeypatch, tmp_path):
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text("| 2026-09-23 | 15.29 | fix(a): x стал 2 [нить нить-один] |\n", encoding="utf-8")
    res, cap = _sensor(monkeypatch, cl)
    assert cap == [] and res["missing"] == 0


def test_без_git_не_судит(monkeypatch, tmp_path):
    monkeypatch.setattr(git_facts, "_git", lambda args: None)
    res, cap = _sensor(monkeypatch, tmp_path / "нет.md")
    assert cap == [] and "не судится" in res
