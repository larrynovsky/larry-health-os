"""Оракул на КЛАСС: git-окружение вызывающего не доезжает до тестов.

ЧТО МЕРЯЕТСЯ. Утверждение фикстуры `_no_inherited_git_env` (tests/conftest.py):
окружение вызывающего — GIT_DIR и родня, которые git кладёт хукам, — до тестов
не доходит. Наблюдаемая величина ровно одна: что видит тест, запущенный под
отравленным окружением. Приманка `_git_env_probe.py` это и докладывает — свой
`os.environ` и то, КУДА разрешается `git rev-parse` из её процесса.

ПОЧЕМУ НЕ «ЧУЖОЙ РЕПОЗИТОРИЙ НЕ ИЗМЕНИЛСЯ» — две выброшенные редакции.

Первая мерила именно это и брала в подопытные `test_install_hooks_worktree.py`
и `test_post_commit_branch_guard.py` — оба про хуки, оба «на вид подходящие».
Мутация (снять тело фикстуры) осталась ЗЕЛЁНОЙ: у первого своя чистка
окружения, второй работает через подставной git.

Вторая сменила подопытного на файл, через который внешний тестировщик реально
пролез, — и мутация СНОВА осталась зелёной. Замер по всем двенадцати тестовым
файлам, трогающим git (12.09, /tmp/find_leakers.py), объяснил почему: под
отравленным GIT_DIR ни один из них не ПИШЕТ в чужой репозиторий. Они громко
падают. Коммит `a251b93` сделал разовый тест, которого в наборе давно нет;
величина «репозиторий изменился» осталась от него и меряет симптом, которого в
живом наборе не бывает. Оракул без симптома не краснеет никогда.

Третья попытка (равенство исходов чистого и отравленного прогонов) отпала по
другой причине: подопытный поднимал клон в ФИКСИРОВАННОМ `/tmp/dgverify`, и
параллельный pre-commit соседней нити ронял оракул сам по себе. Красный,
зависящий от того, коммитит ли в эту секунду сосед, — это VG-R5-10, краснота
носителя, за которую уже заплачено один раз.

ПОЗИТИВНЫЙ КОНТРОЛЬ ВНУТРИ. Приманка запускается ДВАЖДЫ под одним и тем же
отравленным окружением: с conftest и с `--noconftest`. Второй прогон обязан
УВИДЕТЬ утечку — иначе приманка слепа, и зелень первого прогона ничего не
значит. Приём взят у `test_data_ingestion_expected_red.py`
(`test_strict_xfail_actually_fails_on_pass`): проверять не только вывод
механизма, но и то, что механизм вообще способен сказать «нет».

ЧЕГО НЕ ДОКАЗЫВАЕТ. Что каждый существующий тест чист. Доказывает, что
фикстура доезжает до теста в `tests/` и снимает окружение. Это механизм, на
котором держится чистота остальных, а не перепись остальных.

ЦЕНА. ~2 с: два прогона одной функции. Предыдущая редакция стоила ~95 с.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.test_meta(status="implemented", confirmation="confirmed"),
]

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE = REPO_ROOT / "tests" / "unit" / "_git_env_probe.py"

_GIT_VARS = ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_COMMON_DIR",
             "GIT_PREFIX", "GIT_OBJECT_DIRECTORY", "GIT_NAMESPACE",
             "GIT_ALTERNATE_OBJECT_DIRECTORIES")


def _bare_env() -> dict[str, str]:
    env = dict(os.environ)
    for v in _GIT_VARS:
        env.pop(v, None)
    return env


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, timeout=60, env=_bare_env())


@pytest.fixture()
def stand_in_repo(tmp_path: Path) -> Path:
    """Подставной «боевой» репозиторий — то, чем отравляем окружение."""
    repo = tmp_path / "prod"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "owner@real")
    _git(repo, "config", "user.name", "owner")
    (repo / "important.py").write_text("REAL = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "real history: first")
    return repo


def _run_probe(stand_in: Path, report: Path, *, noconftest: bool) -> dict:
    env = _bare_env()
    env["GIT_DIR"] = str(stand_in / ".git")
    env["GIT_INDEX_FILE"] = str(stand_in / ".git" / "index")
    env["GIT_WORK_TREE"] = str(stand_in)
    env["GIT_PREFIX"] = ""
    env["PROBE_REPORT_PATH"] = str(report)

    cmd = [sys.executable, "-m", "pytest", "-c", str(REPO_ROOT / "pytest.ini"),
           str(PROBE), "-q", "-p", "no:cacheprovider"]
    if noconftest:
        cmd.append("--noconftest")

    r = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, capture_output=True,
                       text=True, timeout=300)
    assert report.exists(), (
        f"приманка не оставила отчёта (--noconftest={noconftest}) — прогон "
        f"сломался до неё, оракул ничего не измерил:\n{(r.stdout + r.stderr)[-2000:]}")
    data = json.loads(report.read_text(encoding="utf-8"))
    report.unlink()
    return data


ДОМА_СПИСКА = (
    # (файл, чем начинается блок, чем он заканчивается)
    # Конец режется по СЛЕДУЮЩЕЙ значащей строке, а не по пустой: иначе в хвост
    # попадают комментарии, и любое упоминание GIT_* в прозе делает тест
    # ложно-зелёным (уже ловили: хвост в 1368 лишних символов).
    ("tests/conftest.py", "def _no_inherited_git_env", "\n\n\n"),
    ("scripts/run_affected_tests.sh", "unset GIT_", '"$PYTHON"'),
    ("scripts/git-hooks/studio-post-receive", "unset GIT_", "REPO_ROOT="),
)


def test_списки_переменных_git_во_всех_домах_совпадают():
    """Списки одного множества обязаны сверяться механизмом, а не глазами.

    Третий раунд ревью нашёл, что два дома разъехались: фикстура снимала восемь
    переменных, `unset` в хуке — шесть (не было GIT_NAMESPACE и
    GIT_ALTERNATE_OBJECT_DIRECTORIES). Расхождение молчаливое: оба списка
    выглядят полными, пока не положишь рядом.

    13.09 сбылось предсказание, записанное прежней редакцией этого теста
    строкой «появится третий дом — он о нём не узнает»: третьим стал
    `studio-post-receive`, которому очистка нужна СИЛЬНЕЕ прочих (git зовёт
    хуки приёма с GIT_DIR в окружении, и без очистки установщик спрашивал бы
    каталог хуков у переменной, а не у пути). Поэтому дома теперь перечислены
    данными, а не зашиты в тело: добавить четвёртый — это строка в кортеже.

    Граница честно: тест сверяет ПЕРЕЧИСЛЕННЫЕ дома. Дом, не внесённый в
    кортеж, по-прежнему невидим — просто цена внесения теперь одна строка.
    """
    _NAME = re.compile(r"\bGIT_[A-Z_]+\b")

    списки: dict[str, set[str]] = {}
    for путь, начало, конец in ДОМА_СПИСКА:
        файл = REPO_ROOT / путь
        assert файл.exists(), (
            f"дом списка пропал: {путь}. Либо он переехал (тогда поправь "
            f"ДОМА_СПИСКА), либо очистка окружения там больше не делается — "
            f"второе хуже, чем красный тест")
        текст = файл.read_text(encoding="utf-8")
        assert текст.count(начало) >= 1, (
            f"в {путь} не нашёл начало блока {начало!r} — тест перестал что-либо "
            f"сверять именно в тот момент, когда это важнее всего")
        хвост = текст.split(начало, 1)[1].split(конец)[0]
        найдено = set(_NAME.findall(начало + хвост))
        assert найдено, f"в {путь} блок разобран, но переменных в нём нет"
        списки[путь] = найдено

    эталон_путь, эталон = next(iter(списки.items()))
    расхождения = {п: с for п, с in списки.items() if с != эталон}
    assert not расхождения, (
        "списки git-переменных разъехались — одна из линий защиты уже не полная, "
        "и узнать об этом можно было только положив их рядом\n"
        + f"  эталон ({эталон_путь}): {sorted(эталон)}\n"
        + "\n".join(
            f"  {п}: лишние {sorted(с - эталон)}, недостающие {sorted(эталон - с)}"
            for п, с in расхождения.items()))


@pytest.mark.host_only
def test_чужое_git_окружение_не_доезжает_до_тестов(stand_in_repo, tmp_path):
    assert PROBE.exists(), f"приманка пропала: {PROBE}"
    report = tmp_path / "probe.json"
    stand_in_git = str((stand_in_repo / ".git").resolve())

    # Позитивный контроль: без conftest утечка ОБЯЗАНА быть видна.
    blind = _run_probe(stand_in_repo, report, noconftest=True)
    assert blind["env"].get("GIT_DIR"), (
        "приманка не увидела GIT_DIR даже без conftest — она слепа, и зелёный "
        f"вердикт ниже ничего не значит:\n{blind}")
    assert Path(blind["git_dir"] or "/nonexistent").resolve() == Path(stand_in_git), (
        "без conftest `git rev-parse` не ушёл в подставной репозиторий — "
        f"отравление окружения не сработало, мерить нечего:\n{blind}")

    # Основное утверждение.
    guarded = _run_probe(stand_in_repo, report, noconftest=False)
    # GIT_EXEC_PATH — путь установки САМОГО git, а не признак чужого
    # репозитория; снять его значит сломать git. Прежняя редакция требовала
    # пустоты и потому была зелёной в обычном прогоне и КРАСНОЙ внутри
    # git-хука — то есть ровно там, ради чего писалась. Замерено 13.09 на
    # настоящем коммите: доезжали GIT_EXEC_PATH и личность вызывающего;
    # личность с тех пор снимается фикстурой, GIT_EXEC_PATH — законный гость.
    видно = {k: v for k, v in guarded["env"].items() if k != "GIT_EXEC_PATH"}
    assert видно == {}, (
        "до теста доехали git-переменные вызывающего — изоляция утекла\n"
        f"  {guarded['env']}")
    assert Path(guarded["git_dir"] or "/nonexistent").resolve() != Path(stand_in_git), (
        "`git` из теста разрешился в ЧУЖОЙ репозиторий — ровно сценарий "
        f"a251b93:\n  {guarded['git_dir']}")
