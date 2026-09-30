"""Оракул: Studio восстанавливает НАБОР хуков сам, при каждом приёме push.

РАДИ ЧЕГО. На Studio хуки — симлинки на трекаемые источники, поэтому по
СОДЕРЖИМОМУ они не устаревают. Устаревает набор: 12.09 появился новый хук
`pre-merge-commit`, и на Studio его никто не создал — слияние прошло бы мимо
замка, который на Studio держит запрет коммитов. Замок поставили руками, и
ручная установка не наследуется: следующий новый хук повторил бы историю
(долг BL-STUDIO-HOOKS-1).

ЧТО ИМЕННО ПРОВЕРЯЕТСЯ — поведение, а не присутствие строки. Прежний сторож
этого класса (`test_git_hook_symlink_on_studio`) отвечает на вопрос «лежит ли
симлинк», то есть замечает пропажу ПОСЛЕ факта. Здесь проверяется другое:
принимающий репозиторий, у которого хук удалили, ВОССТАНАВЛИВАЕТ его сам,
когда в него приходит push. Судим по состоянию каталога хуков после push.

ЛОВУШКА, РАДИ КОТОРОЙ ЭТОТ ТЕСТ ВООБЩЕ НУЖЕН. git зовёт хуки приёма с GIT_DIR
и соседями В ОКРУЖЕНИИ. Под ними `git -C <путь> rev-parse --git-common-dir`
отвечает про ПЕРЕМЕННУЮ, а не про путь, который спросили, — а установщик
спрашивает каталог хуков именно так. Без очистки окружения обёртка молча
ставила бы симлинки не туда. Это третий случай того же класса за двое суток,
поэтому он и вынесен в отдельный тест с позитивным контролем.

ГРАНИЦА ЧЕСТНО. Настоящий Studio здесь не участвует: песочница воспроизводит
его КОНФИГУРАЦИЮ (приём push в рабочее дерево через `receive.denyCurrentBranch
= updateInstead`), а не машину. Что симлинк `post-receive` заведён на самом
Studio — этот тест не доказывает и доказать не может; это разовый бутстрап,
и его стережёт ночной `test_git_hook_symlink_on_studio`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.test_meta(status="implemented", confirmation="confirmed"),
]

REPO_ROOT = Path(__file__).resolve().parents[2]
ОБЁРТКА = REPO_ROOT / "scripts" / "git-hooks" / "studio-post-receive"


def _env(home: Path) -> dict[str, str]:
    e = dict(os.environ)
    for v in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_COMMON_DIR",
              "GIT_PREFIX", "GIT_OBJECT_DIRECTORY", "GIT_NAMESPACE",
              "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_QUARANTINE_PATH"):
        e.pop(v, None)
    e["HOME"] = str(home)
    return e


def _git(where: Path, *args: str, home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(where), *args], capture_output=True,
                          text=True, timeout=60, env=_env(home))


@pytest.fixture()
def стенд():
    """Отправитель и приёмник; приёмник настроен как Studio."""
    tmp = Path(tempfile.mkdtemp(prefix="studio_recv_"))
    home = tmp / "home"
    home.mkdir()

    приёмник = tmp / "studio_copy"
    отправитель = tmp / "macbook_copy"

    for дерево in (приёмник, отправитель):
        (дерево / "scripts" / "git-hooks").mkdir(parents=True)
        # Установщик и его сырьё — настоящие, из репозитория: тест обязан
        # краснеть на правке ИХ, а не копии-заглушки.
        shutil.copy(REPO_ROOT / "scripts" / "install_hooks.sh", дерево / "scripts")
        for имя in ("pre-commit", "pre_commit_check.py", "post-commit",
                    "post-commit-macbook", "should_restart_bot.sh",
                    "studio-post-receive"):
            src = REPO_ROOT / "scripts" / "git-hooks" / имя
            if src.exists():
                shutil.copy(src, дерево / "scripts" / "git-hooks" / имя)
        for f in (дерево / "scripts").rglob("*"):
            if f.is_file():
                f.chmod(0o755)
        _git(дерево.parent, "init", "-q", str(дерево), home=home)
        _git(дерево, "config", "user.email", "t@t", home=home)
        _git(дерево, "config", "user.name", "t", home=home)
        _git(дерево, "add", "-A", home=home)
        # --no-verify: хуки песочницы — предмет теста, а не его условие.
        _git(дерево, "commit", "-qm", "base", "--no-verify", home=home)
        _git(дерево, "branch", "-M", "main", home=home)

    # Приёмник ведёт себя как Studio: push обновляет рабочее дерево.
    _git(приёмник, "config", "receive.denyCurrentBranch", "updateInstead", home=home)
    hooks = приёмник / ".git" / "hooks"
    (hooks / "post-receive").symlink_to(
        приёмник / "scripts" / "git-hooks" / "studio-post-receive")

    _git(отправитель, "remote", "add", "studio", str(приёмник), home=home)
    _git(отправитель, "fetch", "-q", "studio", home=home)
    _git(отправитель, "reset", "-q", "--hard", "studio/main", home=home)
    try:
        yield приёмник, отправитель, home
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _push(отправитель: Path, home: Path, метка: str):
    (отправитель / f"{метка}.txt").write_text(метка, encoding="utf-8")
    _git(отправитель, "add", "-A", home=home)
    _git(отправитель, "commit", "-qm", метка, "--no-verify", home=home)
    return _git(отправитель, "push", "-q", "studio", "main", home=home)


@pytest.mark.host_only
def test_приём_push_восстанавливает_пропавший_хук(стенд):
    """Главное утверждение: набор чинится САМ, без человека на Studio."""
    приёмник, отправитель, home = стенд
    hooks = приёмник / ".git" / "hooks"

    # Ровно тот случай, что был 12.09: новый хук в репозитории есть,
    # на принимающей машине его нет.
    (hooks / "pre-merge-commit").unlink(missing_ok=True)
    assert not (hooks / "pre-merge-commit").exists()

    r = _push(отправитель, home, "first")
    assert r.returncode == 0, f"push не прошёл: {r.stdout}{r.stderr}"

    assert (hooks / "pre-merge-commit").exists(), (
        "после приёма push замок слияния не восстановился — значит на Studio "
        "новый хук по-прежнему приходится ставить руками, а ручное не "
        "наследуется")
    assert (hooks / "pre-commit").exists(), "приём push потерял pre-commit"


@pytest.mark.host_only
def test_обёртка_ставит_хуки_в_СВОЙ_каталог_под_отравленным_окружением(стенд):
    """Ловушка, ради которой обёртка чистит окружение.

    Позитивный контроль в самом тесте: сначала убеждаемся, что отравление
    ДЕЙСТВУЕТ (без очистки git отвечает про переменную), и только потом
    судим обёртку. Без контроля зелёный ничего не значил бы — окружение могло
    просто не доехать.
    """
    приёмник, _, home = стенд
    чужой = приёмник.parent / "macbook_copy" / ".git"
    env = _env(home)
    env["GIT_DIR"] = str(чужой)

    # Позитивный контроль: отравление действительно перебивает путь.
    контроль = subprocess.run(
        ["git", "-C", str(приёмник), "rev-parse", "--absolute-git-dir"],
        capture_output=True, text=True, timeout=60, env=env)
    assert Path(контроль.stdout.strip()) == чужой.resolve(), (
        "отравление окружения не сработало — мерить нечего:\n"
        f"{контроль.stdout}{контроль.stderr}")

    (приёмник / ".git" / "hooks" / "pre-commit").unlink(missing_ok=True)
    r = subprocess.run(
        ["bash", str(приёмник / "scripts" / "git-hooks" / "studio-post-receive")],
        cwd=str(приёмник), capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, f"обёртка обязана быть fail-open: {r.stdout}{r.stderr}"

    assert (приёмник / ".git" / "hooks" / "pre-commit").exists(), (
        "хук не поставлен в каталог ПРИЁМНИКА под отравленным GIT_DIR — "
        "очистка окружения в обёртке не работает")
    assert not (чужой / "hooks" / "pre-commit").is_symlink() or \
        (чужой / "hooks" / "pre-commit").resolve().parent != (приёмник / "scripts" / "git-hooks"), (
        "симлинк уехал в ЧУЖОЙ репозиторий — ровно то, от чего защищает unset")


def test_обёртка_fail_open_и_когда_установщика_нет_и_когда_он_падает(tmp_path):
    """Fail-open: отказ приёма push дороже отсутствия хука.

    Проверяется НЕ через настоящий push, и это осознанно: удалить установщик в
    рабочем дереве приёмника — значит сделать дерево грязным, а `updateInstead`
    откажет ещё до всяких хуков («Working directory has unstaged changes»).
    Первая редакция теста так и упала — и упала бы ЗЕЛЕНО-выглядящей причиной,
    обвинив обёртку в чужой ошибке. Поэтому здесь обёртка запускается напрямую
    в дереве нужной формы: судится её собственный код возврата.

    Две формы отказа разные и обе реальны: установщика может не быть (старый
    коммит, неполный checkout) и он может упасть (exit 3 «не git-дерево»).
    """
    дерево = tmp_path / "repo"
    (дерево / "scripts" / "git-hooks").mkdir(parents=True)
    обёртка = дерево / "scripts" / "git-hooks" / "studio-post-receive"
    shutil.copy(ОБЁРТКА, обёртка)
    обёртка.chmod(0o755)

    # Форма 1: установщика нет вовсе.
    r = subprocess.run(["bash", str(обёртка)], cwd=str(дерево),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (
        f"обёртка уронила бы приём push при отсутствующем установщике: {r.stderr}")

    # Форма 2: установщик есть и падает.
    установщик = дерево / "scripts" / "install_hooks.sh"
    установщик.write_text("#!/bin/bash\nexit 3\n", encoding="utf-8")
    установщик.chmod(0o755)
    r = subprocess.run(["bash", str(обёртка)], cwd=str(дерево),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (
        f"обёртка уронила бы приём push при падении установщика: {r.stderr}")
