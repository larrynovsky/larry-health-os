"""Дом извлечённых уроков (project_context/lessons.py, нить lessons-home 28.09.2026).

Четыре обещания, каждое со своим способом покраснеть:
1. страж формы краснеет на внесённых поломках (selftest движка — 34 поломки);
2. уроки health_scripts проходят форму (lessons.yaml в корне);
3. у уроков ОДИН дом: при lessons.yaml манифест не держит corpus — иначе два источника
   ложных путей разойдутся молча;
4. подача preflight не ослабла от переезда: запись с modules печатается по задетому модулю
   любого статуса (C-04 — observation), как печатался corpus.
"""
import json
import os

import pytest

from project_context import indexer, lessons

pytestmark = pytest.mark.unit

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.mark.host_only
def test_selftest_all_breakages_red():
    assert "поломок" in lessons.selftest()


def test_health_lessons_form_is_clean():
    assert lessons.lesson_problems(_REPO) == []


def test_single_home_manifest_has_no_corpus():
    with open(os.path.join(_REPO, "project_context.json"), encoding="utf-8") as f:
        assert "corpus" not in json.load(f), "второй дом ложных путей: перенеси в lessons.yaml"


def test_preflight_delivers_lessons_by_module_any_status():
    status = {r["id"]: r["status"] for r in lessons.load(_REPO)["lessons"]}
    assert status["C-04"] == "observation"
    ix = indexer.build(_REPO)
    assert "C-04" in indexer.preflight(ix, "memory")


def test_form_check_survives_copy_without_git(tmp_path):
    """Полный прогон идёт в копии без .git (C-66). 28.09 первый прогон этой нити покраснел
    82 записями «улика git:<sha> несуществующая» — не знаю ≠ нет. Уберите ветку
    git_ok из _evidence_ok — тест покраснеет."""
    import shutil
    shutil.copy(os.path.join(_REPO, "lessons.yaml"), tmp_path / "lessons.yaml")
    for r in lessons.load(tmp_path)["lessons"]:
        for ref in r["evidence"]:
            if not ref.startswith("git:"):
                (tmp_path / ref).parent.mkdir(parents=True, exist_ok=True)
                (tmp_path / ref).write_text("x")
    assert lessons.lesson_problems(tmp_path) == []


@pytest.mark.host_only
def test_evidence_commit_must_be_in_history_not_just_in_store(tmp_path):
    """31.10 (карточка 2026-10-31-4a31191b): хеш нити до ребейза лежит в хранилище (reflog,
    backup-ref) на одной машине и вычищен gc на другой — «объект есть» давал зелёный от машины
    (§20). Судим «коммит — предок HEAD»: побочная ветка с живым объектом — красная везде."""
    import subprocess

    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], check=True,
                              capture_output=True, text=True).stdout.strip()

    git("init", "-q", "-b", "main")
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid",
        "commit", "-q", "--allow-empty", "-m", "основа")
    base = git("rev-parse", "HEAD")
    git("checkout", "-q", "-b", "thread")
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid",
        "commit", "-q", "--allow-empty", "-m", "коммит нити до ребейза")
    side = git("rev-parse", "HEAD")
    git("checkout", "-q", "main")
    assert lessons._evidence_ok(tmp_path, f"git:{base[:7]}", True) is True
    assert lessons._evidence_ok(tmp_path, f"git:{side[:7]}", True) is False   # объект есть, в истории нет
