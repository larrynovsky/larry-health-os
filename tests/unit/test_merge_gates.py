"""Оракул: слияние НЕ обходит гейты, и это проверено поведением git.

ЧТО СТЕРЕЖЁТ. Все гейты репозитория висят на `pre-commit`: ponytail, дубль-гейт,
гейт одноразовости, квитанция замысла, ruff, прогон затронутых тестов. Каждая
запись реестра, описывающая гейт, говорит «вшит в pre-commit» — и это верно
ровно для `git commit`. Слияние git проводит через ДРУГОЙ хук,
`pre-merge-commit`, которого на MacBook не было ни в одном репозитории (замер
12.09). Пока сливать было нечего, дыра оставалась теоретической; с ветками на
нить она становится главной дорогой в `main`.

ЧТО ИМЕННО МЕРЯЕТСЯ. Не «есть ли файл», а какие хуки git ЗОВЁТ при каждом виде
слияния — потому что это свойство git, а не наше, и оно может измениться с
версией. Проба ставит хуки-отметчики в песочнице и читает, кого позвали.

ЗАЧЕМ ЗДЕСЬ ПОЗИТИВНЫЙ КОНТРОЛЬ НА `git`. Первое утверждение — «merge-коммит
зовёт pre-merge-commit». Если завтра git перестанет его звать, установка хука
останется на месте, а защита исчезнет — и файловая проверка этого не заметит.
Приём тот же, что в `test_data_ingestion_expected_red.py`: проверяем не только
вывод механизма, но и то, что механизм вообще способен сказать «нет».

ГРАНИЦА ЧЕСТНО. Тест судит, что хук ЗОВЁТСЯ и что установщик его ставит. Что
сам `pre-commit` правильно судит merge-коммит (например, `git diff --cached`
показывает слитое содержимое) — отдельное утверждение, здесь не проверяется.
"""
from __future__ import annotations

import os
import subprocess
import shutil
import tempfile
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.host_only,   # станок разработчика: git/хуки/Homebrew — в контейнере не судим
    pytest.mark.test_meta(status="implemented", confirmation="confirmed"),
]

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "scripts" / "install_hooks.sh"

_GIT_VARS = ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_COMMON_DIR",
             "GIT_PREFIX", "GIT_OBJECT_DIRECTORY")


def _env():
    e = dict(os.environ)
    for v in _GIT_VARS:
        e.pop(v, None)
    return e


def _git(repo: Path, *args: str):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, timeout=60, env=_env())


@pytest.fixture()
def песочница():
    """Репозиторий с хуками-отметчиками: каждый пишет своё имя в файл."""
    tmp = Path(tempfile.mkdtemp(prefix="merge_gates_"))
    repo = tmp / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    # Fail-closed: если git смотрит не сюда, коммиты уедут в чужую историю.
    # 12.09 такое уже случилось (a251b93) — предохранитель судит результат.
    real = _git(repo, "rev-parse", "--absolute-git-dir").stdout.strip()
    assert "merge_gates_" in real, f"песочница не изолирована: git смотрит в {real!r}"

    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    журнал = tmp / "calls.log"
    hooks = repo / ".git" / "hooks"
    for имя in ("pre-commit", "pre-merge-commit", "post-commit", "post-merge"):
        h = hooks / имя
        h.write_text(f'#!/bin/bash\necho "{имя}" >> "{журнал}"\nexit 0\n', encoding="utf-8")
        h.chmod(0o755)

    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    try:
        yield repo, журнал
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _звали(журнал: Path) -> set[str]:
    s = журнал.read_text(encoding="utf-8") if журнал.exists() else ""
    журнал.unlink(missing_ok=True)
    return set(s.split())


def test_merge_коммит_зовёт_pre_merge_commit(песочница):
    """Позитивный контроль на сам git: механизм способен сработать.

    Если это утверждение станет ложным (смена версии git, плагин), установленный
    хук останется на диске, а защита исчезнет — и проверка по файлам смолчит.
    """
    repo, журнал = песочница
    _git(repo, "checkout", "-q", "-b", "нить")
    (repo / "g.txt").write_text("работа нити\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "работа нити")
    _git(repo, "checkout", "-q", "-")
    (repo / "f.txt").write_text("base\nmain уехал\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "main вперёд")
    _звали(журнал)

    _git(repo, "merge", "-q", "--no-edit", "нить")
    звали = _звали(журнал)
    assert "pre-merge-commit" in звали, (
        "git больше НЕ зовёт pre-merge-commit при слиянии — установленный хук "
        f"перестал быть защитой, а выглядит установленным. Звали: {звали}")
    assert "pre-commit" not in звали, (
        "git позвал pre-commit при слиянии — поведение изменилось; тогда часть "
        f"этого модуля утверждает не то. Звали: {звали}")


def test_fast_forward_не_зовёт_ни_одного_гейта(песочница):
    """Вторая половина дыры, и худшая: самый частый случай — самый дырявый.

    Если main не двигался, пока нить работала (обычное дело ночью), git делает
    fast-forward и не зовёт НИ pre-commit, НИ pre-merge-commit. То есть даже
    установленный замок не спасает — нужен `--no-ff`.
    """
    repo, журнал = песочница
    _git(repo, "checkout", "-q", "-b", "нить2")
    (repo / "h.txt").write_text("работа\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "работа 2")
    _git(repo, "checkout", "-q", "-")
    _звали(журнал)

    _git(repo, "merge", "-q", "нить2")
    звали = _звали(журнал)
    assert "pre-merge-commit" not in звали and "pre-commit" not in звали, (
        f"поведение git изменилось: fast-forward теперь зовёт гейты ({звали}). "
        "Это хорошая новость, но требование --no-ff перестало быть обязательным "
        "— перечитай ритуал закрытия нити, он опирается на обратное.")


def test_no_ff_возвращает_гейт(песочница):
    """То, чем закрывается дыра из теста выше: --no-ff зовёт замок."""
    repo, журнал = песочница
    _git(repo, "checkout", "-q", "-b", "нить3")
    (repo / "i.txt").write_text("работа\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "работа 3")
    _git(repo, "checkout", "-q", "-")
    _звали(журнал)

    _git(repo, "merge", "-q", "--no-ff", "--no-edit", "нить3")
    звали = _звали(журнал)
    assert "pre-merge-commit" in звали, (
        f"--no-ff не позвал pre-merge-commit — дыра fast-forward не закрыта: {звали}")


def test_слияние_не_зовёт_post_commit(песочница):
    """Почему деплой после слияния нужен явным шагом.

    Деплой висит на post-commit. При слиянии git его НЕ зовёт — ни при
    fast-forward, ни при merge-коммите. Значит работа доедет до main и молча
    остановится: на Studio её не будет, и в deploy.log не появится ни строки.
    Утверждение здесь, чтобы смена поведения git не прошла незамеченной.
    """
    repo, журнал = песочница
    _git(repo, "checkout", "-q", "-b", "нить4")
    (repo / "j.txt").write_text("работа\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "работа 4")
    _git(repo, "checkout", "-q", "-")
    _звали(журнал)

    _git(repo, "merge", "-q", "--no-ff", "--no-edit", "нить4")
    звали = _звали(журнал)
    assert "post-commit" not in звали, (
        f"git стал звать post-commit при слиянии ({звали}) — тогда деплой после "
        "слияния происходит сам, и явный шаг стал лишним. Перечитай ритуал.")
    assert "post-merge" in звали, f"post-merge не позван при слиянии: {звали}"


def test_установщик_ставит_замок_слияния(tmp_path):
    """Ратчет на установщик: без него замок пришлось бы ставить руками.

    Проверяется РЕЗУЛЬТАТ установки в песочнице, а не текст скрипта.
    """
    repo = tmp_path / "main_copy"
    (repo / "scripts" / "git-hooks").mkdir(parents=True)
    shutil.copy(INSTALLER, repo / "scripts" / "install_hooks.sh")
    for имя in ("pre-commit", "post-commit-macbook", "pre_commit_check.py",
                "post-commit", "should_restart_bot.sh"):
        (repo / "scripts" / "git-hooks" / имя).write_text("#!/bin/bash\nexit 0\n",
                                                          encoding="utf-8")
    _git(tmp_path, "init", "-q", str(repo))
    real = _git(repo, "rev-parse", "--absolute-git-dir").stdout.strip()
    assert str(tmp_path) in real, f"песочница не изолирована: {real!r}"
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init", "--no-verify")

    r = subprocess.run(["bash", str(repo / "scripts" / "install_hooks.sh")],
                       capture_output=True, text=True, timeout=60, env=_env())
    assert r.returncode == 0, f"установщик упал: {r.stdout!r} {r.stderr!r}"

    замок = repo / ".git" / "hooks" / "pre-merge-commit"
    assert замок.exists(), (
        "установщик не поставил pre-merge-commit — слияние снова проходит мимо "
        "всех гейтов")
    assert замок.is_symlink(), f"{замок} не symlink — правки источника не доедут"
    assert замок.resolve() == (repo / "scripts" / "git-hooks" / "pre-commit").resolve(), (
        f"замок слияния указывает не на pre-commit: {замок.resolve()}")
