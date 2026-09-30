"""Оракул: установщик хуков не работает из дерева задачи (2026-09-11).

Что стережёт. Каталог `.git/hooks` ОБЩИЙ для всех деревьев, а install_hooks.sh
строит симлинки от REPO_ROOT. Запущенный из дерева задачи, он направил бы их в
это дерево — а оно живёт часы и удаляется вместе с задачей. После уборки хуки
сломаны у ВСЕХ деревьев разом, включая главное, и обнаружилось бы это первым же
коммитом: pre-commit не найден, гейты молча не сработали.

Второй механизм здесь же: каталог хуков спрашивается у git
(`rev-parse --git-common-dir`), а не собирается как REPO_ROOT + "/.git/hooks".
В linked worktree `.git` — файл-указатель, и сборка строкой даёт путь через
файл. Тот же класс стрелял в соседнем проекте 31.08 на первом пробном worktree.

Тест работает на СВОЁМ временном репозитории: настоящий трогать нельзя,
установщик переставляет симлинки.

Граница честно: проверяется install_hooks.sh. Аналогичная правка в
doc_agent._hooks_dir() этим тестом не покрыта — она вызывается только ручной
установкой хука и проверена вживую, но автоматического оракула не имеет.
"""
import shutil
import os
import subprocess
import tempfile
from pathlib import Path
import pytest

pytestmark = pytest.mark.host_only   # станок разработчика: git/хуки/Homebrew — в контейнере не судим

INSTALLER = Path(__file__).resolve().parents[2] / "scripts" / "install_hooks.sh"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=30,
                          env=_clean_env())


# ── Изоляция от git-окружения вызывающего (2026-09-12) ──────────────────────
# git запускает хуки с GIT_DIR / GIT_INDEX_FILE / GIT_WORK_TREE в окружении, и
# дочерний процесс их наследует. `git -C <tmp>` тогда молча работает НЕ с tmp, а
# с репозиторием, из которого пришёл хук. Последствие было ровно наоборот
# полезному: при ручном прогоне тест зеленел, а внутри pre-commit — краснел, то
# есть врал именно там, где на него смотрят (§20: зелёное, причинённое
# окружением). Та же строка `unset GIT_DIR GIT_WORK_TREE` стоит в post-receive
# соседний проект по той же причине.
_GIT_ENV_VARS = ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE",
                 "GIT_COMMON_DIR", "GIT_PREFIX", "GIT_OBJECT_DIRECTORY")


def _clean_env():
    env = dict(os.environ)
    for v in _GIT_ENV_VARS:
        env.pop(v, None)
    return env


def _assert_sandboxed(repo):
    """Fail-closed: тест не работает, если он не в песочнице.

    Чистки окружения мало как ЕДИНСТВЕННОЙ линии: она лечит известную причину
    утечки, а завтра причина будет другая. Этот предохранитель судит результат,
    а не причину — куда РЕАЛЬНО смотрит git после подготовки репозитория. Стоит
    до первого коммита, потому что цена ошибки здесь не «красный тест», а запись
    в чужую историю: 12.09 такая запись случилась — коммит `init` от `t <t@t>`
    удалил все отслеживаемые файлы health_scripts, уехал на Studio, и рестарт
    служб поднял боты на пустом дереве.
    """
    real = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--absolute-git-dir"],
        capture_output=True, text=True, timeout=30, env=_clean_env(),
    ).stdout.strip()
    assert "install_hooks_test_" in real, (
        f"песочница не изолирована: git смотрит в {real!r}, не во временный каталог")


def _make_repo():
    """Минимальный репозиторий со структурой, которую ждёт установщик.

    Файлы создаются для ОБЕИХ веток установщика — MacBook и Studio, — потому что
    ветку выбирает hostname машины, на которой идёт прогон. Первая редакция
    создавала только MacBook-набор: на MacBook тест зеленел, на стенде Studio
    падал с rc=2 «post-commit не найден». Зелёный был причинён окружением, а не
    тестом (§20), и поймал это первый же полный прогон на стенде 11.09.
    """
    tmp = Path(tempfile.mkdtemp(prefix="install_hooks_test_"))
    repo = tmp / "main_copy"
    (repo / "scripts" / "git-hooks").mkdir(parents=True)
    shutil.copy(INSTALLER, repo / "scripts" / "install_hooks.sh")
    for name in ("pre-commit", "post-commit-macbook", "pre_commit_check.py",
                 "post-commit", "should_restart_bot.sh"):
        (repo / "scripts" / "git-hooks" / name).write_text("#!/bin/bash\nexit 0\n")
    _git(repo.parent, "init", "-q", str(repo))
    _assert_sandboxed(repo)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init", "--no-verify")
    return tmp, repo


def _run_installer(cwd):
    return subprocess.run(["bash", str(Path(cwd) / "scripts" / "install_hooks.sh")],
                          capture_output=True, text=True, timeout=60,
                          env=_clean_env())


def test_из_дерева_задачи_отказ():
    """Главный инвариант: установщик не трогает общий каталог из дерева нити."""
    tmp, repo = _make_repo()
    try:
        wt = tmp / "thread_tree"
        _git(repo, "worktree", "add", "-q", "-b", "thread/x", str(wt))
        res = _run_installer(wt)
        assert res.returncode == 4, (
            f"установщик не отказал из дерева задачи: rc={res.returncode}, "
            f"stdout={res.stdout!r}, stderr={res.stderr!r}")
        assert "linked worktree" in res.stderr, f"отказ без объяснения: {res.stderr!r}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_отказ_не_переставил_уже_стоящие_хуки():
    """То, ради чего отказ и нужен: хуки не должны зависеть от временного дерева.

    ПОЧЕМУ ТЕСТ ПЕРЕПИСАН (ревью 12.09, F9). Прежняя редакция звала установщик
    из дерева задачи и проверяла цель симлинка ПОД УСЛОВИЕМ
    `if hook.is_symlink():`. Но установщик оттуда ОТКАЗЫВАЕТ — это утверждает
    тест строкой выше, — значит симлинка обычно нет, условие ложно, и тест
    проходил, не вычислив ни одного assert. Вакуумно-зелёный: он не покраснел
    бы ни от какой поломки, кроме той, при которой хук всё-таки появился.

    Здесь условия нет. Хуки сначала ставятся штатно из главной копии, поэтому
    симлинк существует ВСЕГДА, и утверждение вычисляется всегда. Проверяется
    то, что и заявлено: отказ из дерева задачи не тронул уже стоящие хуки.
    """
    tmp, repo = _make_repo()
    try:
        assert _run_installer(repo).returncode == 0, \
            "штатная установка сломана — дальше мерить нечего"
        hook = repo / ".git" / "hooks" / "pre-commit"
        assert hook.is_symlink(), (
            f"установщик поставил не симлинк ({hook}) — проверка цели стала бы "
            "бессмысленной, как в редакции до ревью F9")
        before = os.readlink(hook)
        assert "main_copy" in before, f"штатная установка указала мимо главной копии: {before}"

        wt = tmp / "thread_tree"
        _git(repo, "worktree", "add", "-q", "-b", "thread/y", str(wt))
        _run_installer(wt)

        after = os.readlink(hook)
        assert after == before, (
            f"отказ из дерева задачи всё-таки переставил хук: {before} -> {after}")
        assert "thread_tree" not in after, (
            f"хук указывает в дерево задачи — уборка дерева сломает его всем: {after}")

        # Позитивный контроль проверки, а не установщика: подстава, на которой
        # утверждение ОБЯЗАНО падать. Без него переименование каталога дерева
        # тихо обессмыслило бы подстроку "thread_tree".
        hook.unlink()
        hook.symlink_to(wt / "scripts" / "git-hooks" / "pre-commit")
        assert "thread_tree" in os.readlink(hook), (
            "проверка не узнаёт ссылку в дерево задачи даже когда она построена "
            "руками — значит зелёный выше ничего не значит")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_из_главной_копии_ставит():
    """Позитивный контроль: без него прошёл бы установщик, отказывающий всегда."""
    tmp, repo = _make_repo()
    try:
        res = _run_installer(repo)
        assert res.returncode == 0, (
            f"установщик сломан в штатном режиме: rc={res.returncode}, "
            f"stdout={res.stdout!r}, stderr={res.stderr!r}")
        hook = repo / ".git" / "hooks" / "pre-commit"
        assert hook.exists(), "хук не установлен из главной копии"
        assert "main_copy" in str(hook.resolve()), \
            f"хук указывает не в главную копию: {hook.resolve()}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_из_дерева_задачи_отказ()
    test_симлинки_не_указывают_в_дерево_задачи()
    test_из_главной_копии_ставит()
    print("OK: установщик хуков отказывает из дерева задачи и работает из главной копии")
