"""Характеризация project_context.staged на НАСТОЯЩЕМ git-репозитории.

Почему не моки: модуль существует ровно из-за расхождения индекса и рабочего дерева, а мок git'а
воспроизводил бы моё представление об этом расхождении, а не само расхождение. Именно так и был
получен REQUEST CHANGES — 21 контроль проверял чистый evaluator и ни разу настоящий путь.

Каждый контроль ниже был бы КРАСНЫМ до появления модуля: старая логика читала рабочее дерево.
"""
import os
import subprocess
import tempfile
import shutil

from project_context import staged
import pytest


def _repo():
    """Одноразовый git-репозиторий с одним закоммиченным модулем."""
    d = tempfile.mkdtemp(prefix="staged-test-")
    run = lambda *a: subprocess.run(a, cwd=d, check=True, capture_output=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    open(os.path.join(d, "victim.py"), "w").write("def pub():\n    return 1\n")
    run("git", "add", "victim.py")
    run("git", "commit", "-qm", "init", "--no-verify")
    return d, run


@pytest.mark.host_only
def test_snapshot_sees_only_staged_paths_as_added():
    d, run = _repo()
    try:
        open(os.path.join(d, "a.py"), "w").write("def f():\n    return 1\n")
        open(os.path.join(d, "b.py"), "w").write("def g():\n    return 2\n")
        run("git", "add", "a.py")                      # b.py остаётся untracked
        with staged.snapshot(d) as s:
            assert s.ok, s.why
            assert s.added == ("a.py",), s.added
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.host_only
def test_untracked_neighbour_is_invisible_in_snapshot():
    """DG-01 в чистом виде: незастейдженный файл не должен существовать для судящего."""
    d, run = _repo()
    try:
        open(os.path.join(d, "a.py"), "w").write("def f():\n    return 1\n")
        os.makedirs(os.path.join(d, "contracts"))
        open(os.path.join(d, "contracts/a.json"), "w").write("{}")
        run("git", "add", "a.py")                      # сайдкар НЕ застейджен
        with staged.snapshot(d) as s:
            assert os.path.exists(os.path.join(s.root, "a.py"))
            assert not os.path.exists(os.path.join(s.root, "contracts/a.json")), \
                "untracked сайдкар виден в снимке — это и есть DG-01"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.host_only
def test_snapshot_holds_staged_content_not_later_edits():
    """Правка в дереве ПОСЛЕ git add не должна попадать в предмет суждения."""
    d, run = _repo()
    try:
        open(os.path.join(d, "a.py"), "w").write("STAGED = 1\n")
        run("git", "add", "a.py")
        open(os.path.join(d, "a.py"), "w").write("STAGED = 999\n")   # только дерево
        with staged.snapshot(d) as s:
            assert open(os.path.join(s.root, "a.py")).read() == "STAGED = 1\n"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.host_only
def test_file_deleted_from_tree_but_present_in_index_stays_in_snapshot():
    """DG-14: чужой модуль, удалённый из дерева, остаётся частью будущего коммита."""
    d, run = _repo()
    try:
        open(os.path.join(d, "a.py"), "w").write("def f():\n    return 1\n")
        run("git", "add", "a.py")
        os.remove(os.path.join(d, "victim.py"))        # удаление НЕ застейджено
        with staged.snapshot(d) as s:
            assert os.path.exists(os.path.join(s.root, "victim.py")), \
                "снимок потерял модуль, который останется в коммите — это и есть DG-14"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.host_only
def test_no_added_files_means_no_materialisation():
    """Цена: коммит без новых файлов не платит за снимок."""
    d, _run = _repo()
    try:
        with staged.snapshot(d) as s:
            assert s.ok and s.added == () and s.root is None
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.host_only
def test_snapshot_resolves_repository_toplevel_from_subdirectory():
    """Ревью 2026-07-27 F-09: git находил индекс вверх по дереву, а пути резолвились от cwd —
    тот же нарушитель из подкаталога получал чистый вердикт."""
    d, run = _repo()
    try:
        open(os.path.join(d, "a.py"), "w").write("def f():\n    return 1\n")
        run("git", "add", "a.py")
        nested = os.path.join(d, "nested", "deep")
        os.makedirs(nested)
        with staged.snapshot(nested) as s:
            assert s.ok, s.why
            assert s.added == ("a.py",), s.added
            assert os.path.exists(os.path.join(s.root, "a.py"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.host_only
def test_temp_allocation_failure_is_a_result_not_an_exception(monkeypatch):
    """Ревью 2026-07-27 F-06: mkdtemp стоял ВЫШЕ try и бросал наружу вопреки контракту
    «никогда не бросает». Потребитель обязан получить решение, а не traceback."""
    d, run = _repo()
    try:
        open(os.path.join(d, "a.py"), "w").write("def f():\n    return 1\n")
        run("git", "add", "a.py")

        def _boom(*_a, **_k):
            raise OSError("synthetic no space")

        monkeypatch.setattr(tempfile, "mkdtemp", _boom)
        with staged.snapshot(d) as s:
            assert s.ok is False and "каталог снимка" in s.why, s.why
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_outside_git_returns_structural_failure_not_exception():
    d = tempfile.mkdtemp(prefix="staged-nogit-")
    try:
        with staged.snapshot(d) as s:
            assert s.ok is False and s.why, "отказ инструмента обязан быть структурным результатом"
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.host_only
def test_snapshot_directory_is_removed_on_exit():
    d, run = _repo()
    try:
        open(os.path.join(d, "a.py"), "w").write("def f():\n    return 1\n")
        run("git", "add", "a.py")
        with staged.snapshot(d) as s:
            kept = s.root
            assert os.path.isdir(kept)
        assert not os.path.exists(kept), "каталог снимка пережил суждение"
    finally:
        shutil.rmtree(d, ignore_errors=True)
