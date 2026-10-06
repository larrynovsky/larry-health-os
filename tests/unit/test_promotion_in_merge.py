"""Суд подъёма до holds при слиянии нити — по её коммитам по порядку (BL-OWNER-CRITERION-MERGE-1).

Прежний суд «main → итог слияния» отбивал законный путь «критерий владельца отдельным коммитом,
потом подъём» в одной нити (уроки C-70, C-162). Здесь настоящий `git merge --no-commit` во временном
репозитории: суд по коммитам — только когда итог слияния равен вершине ветки.
"""
from __future__ import annotations

import subprocess

import pytest
import yaml

import intent_registry as ir

pytestmark = pytest.mark.unit


def _reg(status, by=None):
    inv = {"id": "i1", "status": status}
    if by:
        inv["closes_when"], inv["closes_when_by"] = "что-то стало правдой", by
    return [{"id": "s1", "invariants": [inv]}]


def _git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(cwd, reg, msg):
    (cwd / "subsystem_intent.yaml").write_text(yaml.safe_dump(reg, allow_unicode=True))
    _git(cwd, "add", "subsystem_intent.yaml")
    _git(cwd, "commit", "-q", "-m", msg)


@pytest.fixture
def repo(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "config", "user.email", "t@t")
    _git(main, "config", "user.name", "t")
    _commit(main, _reg("open", "claude"), "base")
    tree = tmp_path / "thread"
    _git(main, "worktree", "add", "-q", "-b", "thread/x", str(tree))
    return main, tree


def _merge(main):
    _git(main, "merge", "--no-commit", "--no-ff", "thread/x")


def test_criterion_then_flip_in_one_thread_passes(repo):
    main, tree = repo
    _commit(tree, _reg("open", "owner"), "критерий владельца")
    _commit(tree, _reg("holds"), "подъём")
    _merge(main)
    assert ir.promotions_in_merge(str(tree), main) == []
    # прежний суд целиком (main → итог) этот законный путь отбивал — ради этого и правка
    assert ir.unapproved_promotions(ir.registry_at("HEAD", main), ir.registry_at(":", main))


def test_flip_by_own_criterion_in_thread_is_flagged(repo):
    main, tree = repo
    _commit(tree, _reg("holds"), "подъём по своему критерию")
    _merge(main)
    v = ir.promotions_in_merge(str(tree), main)
    assert v and "s1::i1" in v[0] and "claude" in v[0]


def test_merge_result_unlike_branch_tip_is_not_judged_by_commits(repo):
    main, tree = repo
    _commit(tree, _reg("open", "owner"), "критерий владельца")
    _commit(tree, _reg("holds"), "подъём")
    _merge(main)
    (main / "subsystem_intent.yaml").write_text(yaml.safe_dump(_reg("open", "owner")))
    _git(main, "add", "subsystem_intent.yaml")       # итог слияния правлен мимо коммитов нити
    assert ir.promotions_in_merge(str(tree), main) is None


def test_pre_commit_judges_a_thread_merge_by_its_commits():
    from pathlib import Path
    src = (Path(ir.__file__).parent / "scripts" / "git-hooks" / "pre-commit").read_text(encoding="utf-8")
    assert 'os.environ.get("THREAD_MERGE_SOURCE")' in src and "ir.promotions_in_merge(src)" in src
