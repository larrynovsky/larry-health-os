"""Гейт слияния засчитывает квитанцию дерева нити по СОСТАВУ, а не по HEAD.

ЗАЧЕМ (BL-PREFLIGHT-NEW-SUB-1). thread_finish гоняет гейты в главной копии, где HEAD — main,
а квитанции лежат в дереве нити и привязаны к её HEAD до ребейза. Итог 24–25.09: каждое
закрытие блокировалось на подсистеме, которую нить честно прочла, и обходилось пробным
слиянием руками. Зачёт по составу закрывает обход, но не дыру: сменившийся состав,
несвежая квитанция, отсутствие слияния — блок.

Тест на НАСТОЯЩЕМ git (временный репозиторий + worktree + merge --no-commit): хрупкая часть —
разбор `git worktree list --porcelain`, и подмена ответа git её бы не проверила.
"""
from __future__ import annotations

import os
import subprocess
import time

import pytest

from project_context import indexer, receipt

pytestmark = [pytest.mark.unit, pytest.mark.host_only]


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def merging(tmp_path, monkeypatch):
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "config", "user.email", "t@t")
    _git(main, "config", "user.name", "t")
    (main / "a.txt").write_text("a")
    _git(main, "add", "-A")
    _git(main, "commit", "-qm", "a")
    # Соседняя нить и заведена раньше, и по имени первая — в списке деревьев она до сливаемой, и зачёт
    # «первого попавшегося дерева» взял бы её квитанцию вместо сливаемой.
    _git(main, "worktree", "add", "-q", "-b", "thread/y", str(tmp_path / "aa_neighbour"))
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", "-b", "thread/x", str(wt))
    (wt / "b.txt").write_text("b")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "b")
    _git(main, "merge", "--no-ff", "--no-commit", "thread/x")
    monkeypatch.setattr(indexer, "composition_token", lambda ix, sub: "C1")
    monkeypatch.delenv("THREAD_MERGE_SOURCE", raising=False)  # тесты зовутся и из thread_finish
    d = indexer.receipts_dir(str(wt))
    os.makedirs(d, exist_ok=True)
    return str(main), str(wt), os.path.join(d, "sub")


def test_source_tree_found_by_merge_head(merging):
    main, wt, _ = merging
    assert os.path.realpath(receipt.merge_source_tree(main)) == os.path.realpath(wt)


def test_same_composition_is_credited(merging):
    main, _, rf = merging
    open(rf, "w").write("HEADTOKEN\nC1\n")
    assert receipt.merge_credit({}, main, "sub")


def test_changed_composition_blocks(merging):
    main, _, rf = merging
    open(rf, "w").write("HEADTOKEN\nC2\n")
    assert not receipt.merge_credit({}, main, "sub")


def test_stale_receipt_blocks(merging):
    main, _, rf = merging
    open(rf, "w").write("HEADTOKEN\nC1\n")
    old = time.time() - (receipt.TTL_H * 3600 + 60)
    os.utime(rf, (old, old))
    assert not receipt.merge_credit({}, main, "sub")


def test_old_single_line_receipt_blocks(merging):
    main, _, rf = merging
    open(rf, "w").write("HEADTOKEN\n")
    assert not receipt.merge_credit({}, main, "sub")


def test_no_merge_no_credit(merging):
    main, _, rf = merging
    open(rf, "w").write("HEADTOKEN\nC1\n")
    _git(main, "merge", "--abort")
    assert receipt.merge_source_tree(main) is None
    assert not receipt.merge_credit({}, main, "sub")


def test_thread_finish_names_the_tree_when_merge_head_is_absent(merging, monkeypatch):
    """Настоящий `git merge` зовёт pre-merge-commit ДО записи MERGE_HEAD — дерево называет
    thread_finish через окружение. Замер 25.09: без этого пробное слияние прошло, настоящее — нет."""
    main, wt, rf = merging
    open(rf, "w").write("HEADTOKEN\nC1\n")
    _git(main, "merge", "--abort")
    monkeypatch.setenv("THREAD_MERGE_SOURCE", wt)
    assert receipt.merge_credit({}, main, "sub")
    monkeypatch.setenv("THREAD_MERGE_SOURCE", main)  # сама главная копия — не источник
    assert not receipt.merge_credit({}, main, "sub")
