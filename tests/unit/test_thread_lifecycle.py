"""Оракул: дерево нити изолирует индекс, а закрытие нити не теряет гейты.

РАДИ ЧЕГО ВСЁ. 12.09 общий индекс сработал четыре раза за день: дважды чужой
`git add` унёс готовые файлы в чужой коммит, один раз чужой незакоммиченный
файл попал в мой индекс и заблокировал мой коммит сообщением про чужой модуль,
один раз тест записал в общую историю. Своё дерево закрывает первые три;
четвёртый — не его класс (`.git` общий), и это сказано вслух в самом скрипте.

ЧТО ИМЕННО ПРОВЕРЯЕТСЯ — три величины, и ни одна не «скрипт отработал без
ошибки»:
  · изоляция: `git add -A` в одном дереве НЕ забирает файлы другого;
  · гейты при закрытии: слияние идёт так, что хук слияния ЗОВЁТСЯ (это и есть
    смысл `--no-ff`; fast-forward не зовёт ничего — замерено пробой);
  · деплой при закрытии: он вызывается ЯВНО, потому что git при слиянии
    post-commit не зовёт.

ГРАНИЦА ЧЕСТНО. Тесты работают на временном репозитории со своими хуками-
отметчиками. Что настоящий деплой доехал до Studio, они не доказывают — для
этого нужен настоящий Studio. Проверяется решение скрипта, а не сеть.
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
РЕПО = "health_scripts"
START = REPO_ROOT / "scripts" / "thread_start.sh"
FINISH = REPO_ROOT / "scripts" / "thread_finish.sh"


def _env(home: Path):
    e = dict(os.environ)
    for v in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_COMMON_DIR",
              "GIT_PREFIX", "GIT_OBJECT_DIRECTORY",
              # Метка замка закрытия: pre-commit коммита журнала гоняет эти тесты
              # ИЗНУТРИ закрытия, и метка, унаследованная песочницей, сделала бы
              # суд слепым (замерено: 1 красный при THREAD_FINISH_HOLDS_LOCK=1).
              "THREAD_FINISH_HOLDS_LOCK", "THREAD_FINISH_LOCK_WAIT"):
        e.pop(v, None)
    # Деревья нитей живут в $HOME/.worktrees — уводим HOME в песочницу, иначе
    # тест насорил бы в настоящем каталоге владельца.
    e["HOME"] = str(home)
    return e


def _git(repo: Path, *args: str, home: Path):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, timeout=60, env=_env(home))


def _построить(tmp: Path, *, отдельный_gitdir: bool):
    """Собрать репозиторий с хуками-отметчиками и подставным деплоем.

    ЗАЧЕМ ФЛАГ ФОРМЫ. git-каталог не обязан лежать ВНУТРИ рабочего дерева: у
    соседнего проекта `.git` — это файл «gitdir: ~/.gitdirs/neighbour». Все семь прежних
    оракулов строили только первую форму, а в ней «родитель .git» случайно
    совпадает с корнем дерева — поэтому ошибка в thread_finish была невидима,
    пока её не нашёл живой прогон 13.09. Форма — это тоже вход.
    """
    home = tmp / "home"
    home.mkdir()
    repo = tmp / "health_scripts"
    (repo / "scripts" / "git-hooks").mkdir(parents=True)
    for имя in ("thread_start.sh", "thread_finish.sh"):
        shutil.copy(REPO_ROOT / "scripts" / имя, repo / "scripts" / имя)
        (repo / "scripts" / имя).chmod(0o755)
    # Дом списка машинных артефактов — часть симулируемого репозитория.
    # Без него закрытие нити откажет с кодом 9 (fail-closed), и это правильно:
    # без списка машинный артефакт от чужой работы не отличить.
    shutil.copy(REPO_ROOT / "git_facts.py", repo / "git_facts.py")
    # Дом имени и пути замка главной копии (нить finish-lock). Без него закрытие
    # откажет кодом 15 — путь замка не угадывается.
    shutil.copy(REPO_ROOT / "scripts" / "git-hooks" / "finish_lock_guard.sh",
                repo / "scripts" / "git-hooks" / "finish_lock_guard.sh")
    (repo / "scripts" / "git-hooks" / "finish_lock_guard.sh").chmod(0o755)

    журнал = tmp / "calls.log"
    # Подставной деплой: настоящий пушит на Studio, здесь только отмечается.
    деплой = repo / "scripts" / "git-hooks" / "post-commit-macbook"
    деплой.write_text(f'#!/bin/bash\necho "deploy" >> "{журнал}"\nexit 0\n',
                      encoding="utf-8")
    деплой.chmod(0o755)

    # Подставной полный прогон. Настоящий рсинкает дерево на Studio и идёт 5.5
    # минуты; здесь он отмечается, записывает КАТАЛОГ, из которого запущен (это
    # и есть проверяемое свойство — судить дерево нити, а не главной копии), и
    # умеет по требованию краснеть и двигать main под ногами.
    прогон = repo / "scripts" / "test_on_studio.sh"
    прогон.write_text(
        '#!/bin/bash\n'
        f'echo "tests" >> "{журнал}"\n'
        f'pwd > "{журнал}.cwd"\n'
        # Сосед двигает main ФАЙЛОМ: по умолчанию кодом, по STAND_DRIFT_FILE — тем путём
        # (запиской docs/handoff/** — тот дрейф, что закрытие переживает без второго прогона).
        'if [[ -n "${STAND_DRIFT_REPO:-}" ]]; then '
        '_d="${STAND_DRIFT_FILE:-code_drift.py}"; mkdir -p "$STAND_DRIFT_REPO/$(dirname "$_d")"; '
        'echo "$RANDOM" >> "$STAND_DRIFT_REPO/$_d"; git -C "$STAND_DRIFT_REPO" add "$_d"; '
        'git -C "$STAND_DRIFT_REPO" commit -q --no-verify -m "сосед двинул main, пока шёл прогон"; fi\n'
        'exit ${STAND_RUN_RC:-0}\n',
        encoding="utf-8")
    прогон.chmod(0o755)
    # Подставной отбор и прогон ЗАТРОНУТЫХ тестов (дрейф записками, п. 7 шапки).
    (repo / "affected_tests.py").write_text(
        'import os\nprint(os.environ.get("STAND_AFFECTED_OUT", "tests/test_notes.py"))\n',
        encoding="utf-8")
    зат = repo / "scripts" / "run_affected_tests.sh"
    зат.write_text(f'#!/bin/bash\necho "affected" >> "{журнал}"\nexit ${{STAND_AFFECTED_RC:-0}}\n',
                   encoding="utf-8")
    зат.chmod(0o755)

    if отдельный_gitdir:
        вынесенный = tmp / "gitdirs" / "health_scripts"
        вынесенный.parent.mkdir(parents=True, exist_ok=True)
        _git(repo.parent, "init", "-q", "--separate-git-dir", str(вынесенный),
             str(repo), home=home)
    else:
        _git(repo.parent, "init", "-q", str(repo), home=home)
    реальный = _git(repo, "rev-parse", "--absolute-git-dir", home=home).stdout.strip()
    assert "thread_life_" in реальный, f"песочница не изолирована: {реальный!r}"
    _git(repo, "config", "user.email", "t@t", home=home)
    _git(repo, "config", "user.name", "t", home=home)

    hooks = Path(реальный) / "hooks"
    for имя in ("pre-commit", "post-commit"):
        h = hooks / имя
        h.write_text(f'#!/bin/bash\necho "{имя}" >> "{журнал}"\nexit 0\n',
                     encoding="utf-8")
        h.chmod(0o755)
    # Хук слияния умеет краснеть на N-м вызове (STAND_GATE_FAIL_ON=N): первый
    # вызов — пробный, ДО прогона; второй — настоящий, при слиянии. Счётчик в
    # отдельном файле, потому что _звали() чистит журнал.
    h = hooks / "pre-merge-commit"
    h.write_text(
        '#!/bin/bash\n'
        f'echo "pre-merge-commit" >> "{журнал}"\n'
        f'c=$(( $(cat "{журнал}.gate" 2>/dev/null || echo 0) + 1 )); '
        f'echo $c > "{журнал}.gate"\n'
        '[[ -n "${STAND_GATE_FAIL_ON:-}" && "$c" == "$STAND_GATE_FAIL_ON" ]] && exit 1\n'
        'exit 0\n', encoding="utf-8")
    h.chmod(0o755)

    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    # Авто-артефакты заводятся В ИСТОРИИ, а не появляются untracked.
    # ЗАЧЕМ ИМЕННО ТАК (найдено внешним ревью 14.09). Прежний стенд оставлял
    # CHANGELOG.md untracked — тогда `status --untracked-files=no` отдаёт ПУСТО,
    # весь блок `if [[ -n "$DIRTY" ]]` не исполняется, и тест зеленел по ветке
    # «untracked не блокирует», а не по той, которую называет. Три мутации,
    # включая «вырезать блок коммита артефактов целиком», проходили незамеченными.
    # В бою эти файлы отслеживаемые и грязные — стенд обязан быть таким же.
    for _а in ("CHANGELOG.md", "ARCH_SNAPSHOT.md", "SECURITY.md"):
        (repo / _а).write_text(f"# {_а}\n", encoding="utf-8")
    _git(repo, "add", "-A", home=home)
    _git(repo, "commit", "-qm", "base", home=home)
    _git(repo, "branch", "-M", "main", home=home)
    журнал.write_text("", encoding="utf-8")
    return repo, home, журнал


@pytest.fixture()
def песочница():
    """Обычная форма: каталог .git лежит внутри рабочего дерева."""
    tmp = Path(tempfile.mkdtemp(prefix="thread_life_"))
    try:
        yield _построить(tmp, отдельный_gitdir=False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture()
def песочница_вынесенный_gitdir():
    """Форма соседнего проекта: `.git` — файл-указатель на каталог в стороне."""
    tmp = Path(tempfile.mkdtemp(prefix="thread_life_"))
    try:
        yield _построить(tmp, отдельный_gitdir=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _звали(журнал: Path) -> list[str]:
    s = журнал.read_text(encoding="utf-8") if журнал.exists() else ""
    журнал.write_text("", encoding="utf-8")
    return s.split()


def _start(repo: Path, home: Path, slug: str):
    return subprocess.run(["bash", str(repo / "scripts" / "thread_start.sh"), slug],
                          cwd=str(repo), capture_output=True, text=True,
                          timeout=120, env=_env(home))


def _finish(repo: Path, home: Path, slug: str, **окружение: str):
    e = _env(home)
    e.update(окружение)
    return subprocess.run(["bash", str(repo / "scripts" / "thread_finish.sh"), slug],
                          cwd=str(repo), capture_output=True, text=True,
                          timeout=120, env=e)


def test_дерево_нити_не_видит_чужих_файлов(песочница):
    # Имена латиницей намеренно: slug идёт в имя ветки и в путь дерева
    # (реальные нити зовутся так же), а git КВОТИРУЕТ не-ASCII имена
    # файлов в выводе (\320\274...), и тест сравнивал бы не то.
    """ГЛАВНОЕ. Ради этого всё: `git add -A` в одном дереве не забирает чужое.

    Воспроизводит случай 12.09 буквально: в главной копии лежит незакоммиченный
    файл соседа, нить делает `add -A` у себя. До worktree сосед уехал бы в
    коммит нити.
    """
    repo, home, _ = песочница
    r = _start(repo, home, "isolation")
    assert r.returncode == 0, f"{r.stdout}{r.stderr}"
    tree = home / ".worktrees" / "health_scripts" / "isolation"
    assert tree.exists(), "дерево нити не создано"

    (repo / "neighbour_wip.py").write_text("# работа соседа\n", encoding="utf-8")
    (tree / "mine.py").write_text("# работа нити\n", encoding="utf-8")

    _git(tree, "add", "-A", home=home)
    застейджено = _git(tree, "diff", "--cached", "--name-only", home=home).stdout.split()

    assert "mine.py" in застейджено, f"нить не видит своего файла: {застейджено}"
    assert "neighbour_wip.py" not in застейджено, (
        "в индекс нити попал чужой незакоммиченный файл — общий индекс вернулся, "
        f"и `git add` снова уносит чужое: {застейджено}")


def test_закрытие_зовёт_хук_слияния(песочница):
    """`--no-ff` не для красоты: fast-forward не зовёт НИ ОДНОГО хука.

    Если закрытие вдруг станет fast-forward, merge-коммит пройдёт мимо всех
    гейтов, и это будет незаметно — коммит появится, тесты не покраснеют.
    """
    repo, home, журнал = песочница
    _start(repo, home, "gates")
    tree = home / ".worktrees" / "health_scripts" / "gates"
    (tree / "new_thing.py").write_text("x = 1\n", encoding="utf-8")
    _git(tree, "add", "-A", home=home)
    _git(tree, "commit", "-qm", "работа нити", home=home)
    _звали(журнал)

    r = _finish(repo, home, "gates")
    assert r.returncode == 0, f"закрытие не прошло:\n{r.stdout}{r.stderr}"
    звали = _звали(журнал)
    # С 21.09 хук зовётся ДВАЖДЫ: пробно до прогона и настоящим слиянием. Одного
    # «in» теперь мало — его удовлетворил бы один пробный вызов, и ff-слияние
    # мимо всех гейтов снова стало бы незаметным. Настоящее слияние — второй вызов
    # И merge-коммит (у него два родителя) в истории main.
    assert звали.count("pre-merge-commit") == 2, (
        "хук настоящего слияния не позван — значит слияние было fast-forward, и "
        f"merge прошёл мимо ВСЕХ гейтов (пробный вызов не в счёт). Звали: {звали}")
    родители = _git(repo, "log", "-1", "--format=%P", "main", home=home).stdout.split()
    assert len(родители) == 2, f"вершина main не merge-коммит: родителей {len(родители)}"


def test_закрытие_деплоит_явно(песочница):
    """git при слиянии post-commit не зовёт — деплой обязан быть явным шагом.

    Без него работа доезжает до main и молча останавливается: на Studio её нет,
    и в deploy.log нет ни строки. Это худший вид отказа — бесследный.
    """
    repo, home, журнал = песочница
    _start(repo, home, "deploy-step")
    tree = home / ".worktrees" / "health_scripts" / "deploy-step"
    (tree / "more.py").write_text("y = 2\n", encoding="utf-8")
    _git(tree, "add", "-A", home=home)
    _git(tree, "commit", "-qm", "работа", home=home)
    _звали(журнал)

    r = _finish(repo, home, "deploy-step")
    assert r.returncode == 0, f"{r.stdout}{r.stderr}"
    звали = _звали(журнал)
    assert "deploy" in звали, (
        "деплой не вызван — работа осталась на MacBook, и об этом никто не "
        f"узнает. Звали: {звали}")
    assert "post-commit" not in звали, (
        "git сам позвал post-commit при слиянии — поведение изменилось, явный "
        f"шаг стал лишним и теперь деплоит дважды. Звали: {звали}")


def test_незакоммиченное_в_нити_не_теряется(песочница):
    """Отказ, а не тихая потеря: слияние не заберёт незакоммиченное."""
    repo, home, _ = песочница
    _start(repo, home, "draft")
    tree = home / ".worktrees" / "health_scripts" / "draft"
    (tree / "wip.py").write_text("# не закоммичено\n", encoding="utf-8")

    r = _finish(repo, home, "draft")
    assert r.returncode == 4, (
        "закрытие прошло при незакоммиченной работе — она потерялась бы молча: "
        f"rc={r.returncode}\n{r.stdout}{r.stderr}")
    assert "незакоммиченное" in (r.stdout + r.stderr)


def test_повторный_старт_не_затирает_нить(песочница):
    """Позитивный контроль обратной стороны: скрипт, который всегда «успешно»
    создаёт нить, снёс бы чужую ветку с работой."""
    repo, home, _ = песочница
    assert _start(repo, home, "dup").returncode == 0
    повтор = _start(repo, home, "dup")
    assert повтор.returncode == 3, (
        f"повторный старт не отказал: rc={повтор.returncode}\n{повтор.stdout}{повтор.stderr}")

def test_артефакты_doc_agent_не_блокируют_закрытие(песочница):
    """Третий случай отказа, найденный ЖИВЫМ прогоном 13.09.

    Пять оракулов выше его не нашли: в песочнице главная копия всегда чистая.
    На настоящем репозитории git отказал слиянием ещё до гейтов — «Your local
    changes to the following files would be overwritten by merge: ARCH_SNAPSHOT.md
    CHANGELOG.md». Это не конфликт и не гейт, а незакоммиченное В ГЛАВНОЙ копии.

    И оно там почти всегда: doc_agent дописывает эти файлы в post-commit после
    КАЖДОГО коммита. Без разбора по происхождению закрытие нити падало бы почти
    всегда, а сообщение врало бы про конфликт.
    """
    repo, home, журнал = песочница
    _start(repo, home, "autodocs")
    tree = home / ".worktrees" / РЕПО / "autodocs"
    (tree / "work.py").write_text("z = 3\n", encoding="utf-8")
    # ЧТО ИМЕННО ЗДЕСЬ СУДИТСЯ (пересобрано 14.09 по внешнему ревью). Артефакт
    # ОТСЛЕЖИВАЕМЫЙ и грязный в главной копии — прежний стенд оставлял его
    # untracked, тогда `status --untracked-files=no` отдавал пусто, весь блок не
    # исполнялся, и тест зеленел по чужой ветке. Ветка нити артефакт НЕ трогает:
    # случай «обе стороны переписали CHANGELOG» — отдельный, это настоящий
    # content-конфликт, он заведён долгом BL-THREAD-ARTIFACT-MERGE-1, и мешать
    # его сюда значит судить сразу два разных отказа одним утверждением.
    _git(tree, "add", "-A", home=home)
    _git(tree, "commit", "-qm", "работа", home=home)

    # Ровно то, что оставляет post-commit в главной копии.
    (repo / "CHANGELOG.md").write_text(
        "# CHANGELOG.md\n\n## запись от doc_agent в главной\n", encoding="utf-8")
    _звали(журнал)

    r = _finish(repo, home, "autodocs")
    assert r.returncode == 0, (
        "закрытие упало из-за артефактов doc_agent — они есть после каждого "
        f"коммита, значит нить не закрыть почти никогда:\n{r.stdout}{r.stderr}")
    assert "deploy" in _звали(журнал), "деплой не дошёл"
    # Артефакт ДОЛЖЕН был уехать в историю, а не остаться грязным: именно это
    # проверяет, что `git add` отработал, а не вернул 128 в `|| true`.
    осталось = _git(repo, "status", "--porcelain", "--untracked-files=no",
                    home=home).stdout
    assert "CHANGELOG.md" not in осталось, (
        f"артефакт не закоммичен — ветка коммита артефактов не сработала:\n{осталось}")


def test_чужая_незакоммиченная_работа_блокирует_закрытие(песочница):
    """Обратная сторона: за соседа решать нельзя.

    Без этого утверждения починка выше выродилась бы в «коммить всё, что лежит»
    — и слияние затирало бы чужую незакоммиченную работу. Это ровно тот класс,
    от которого worktree и заводился.

    ПРАВКА 13.09: сосед изображается правкой ОТСЛЕЖИВАЕМОГО файла, а не новым.
    Прежняя редакция клала в главную копию untracked-файл — и была зелёной по
    неверной причине: срабатывала грубая проверка «любое `??` = чужое». Живой
    случай, ради которого тест писался, был именно таким, как здесь: git отказал
    словами «Your local changes to the following files would be overwritten by
    merge: ARCH_SNAPSHOT.md CHANGELOG.md», а это файлы в истории. Untracked
    слияние не трогает — про это отдельный тест ниже.
    """
    repo, home, _ = песочница
    _start(repo, home, "neighbour")
    tree = home / ".worktrees" / РЕПО / "neighbour"
    (tree / "mine2.py").write_text("a = 1\n", encoding="utf-8")
    _git(tree, "add", "-A", home=home)
    _git(tree, "commit", "-qm", "работа", home=home)

    # f.txt заведён фикстурой и лежит в истории — правка в нём и есть та чужая
    # работа, которую слияние действительно способно затереть.
    (repo / "f.txt").write_text("# работа соседа\n", encoding="utf-8")
    assert _git(repo, "status", "--porcelain", home=home).stdout.startswith(" M"), (
        "подготовка не удалась: чужая работа обязана быть правкой отслеживаемого файла")

    r = _finish(repo, home, "neighbour")
    assert r.returncode == 8, (
        "закрытие не отказало при чужой незакоммиченной работе — слияние её "
        f"затрёт: rc={r.returncode}\n{r.stdout}{r.stderr}")
    assert "f.txt" in (r.stdout + r.stderr), "не назван виновный файл"


def test_вынесенный_gitdir_не_ломает_закрытие(песочница_вынесенный_gitdir):
    """Форма репозитория, на которой закрытие нити молча падало.

    До 13.09 главная копия вычислялась как РОДИТЕЛЬ git-каталога. Здесь
    git-каталог лежит в <tmp>/gitdirs, его родитель — не репозиторий вовсе,
    и `git show-ref` оттуда отвечал «нет ветки thread/shape». Сообщение врало:
    ветка была, сломан был путь. На живом соседнем проекте это давало rc=3.

    Чем краснеет: верните MAIN_TREE="$(cd "$COMMON/.." && pwd)" в
    thread_finish.sh — finish вернёт 3, и `work` не появится в истории main.
    """
    repo, home, журнал = песочница_вынесенный_gitdir
    assert (repo / ".git").is_file(), (
        "песочница построена не той формы: .git обязан быть ФАЙЛОМ-указателем, "
        "иначе тест проверяет ту же форму, что и остальные семь")

    r = _start(repo, home, "shape")
    assert r.returncode == 0, f"{r.stdout}{r.stderr}"
    tree = home / ".worktrees" / "health_scripts" / "shape"
    assert tree.is_dir(), (
        f"дерево создано не там, где его будет искать закрытие: {r.stdout}{r.stderr}")

    (tree / "новый.txt").write_text("работа нити\n", encoding="utf-8")
    _git(tree, "add", "-A", home=home)
    _git(tree, "commit", "-qm", "work", home=home)
    _звали(журнал)

    f = _finish(repo, home, "shape")
    assert f.returncode == 0, f"закрытие упало: rc={f.returncode}\n{f.stdout}{f.stderr}"
    # Судим по ИСТОРИИ, а не по коду возврата: работа обязана оказаться в main.
    слитые = _git(repo, "log", "--oneline", "main", home=home).stdout
    assert "work" in слитые, f"работа нити не доехала до main:\n{слитые}"
    assert "deploy" in _звали(журнал), "деплой при закрытии не вызван"


def test_чужой_untracked_не_блокирует_закрытие(песочница):
    """Файл, о котором git не знает, слияние не трогает — значит и не блокирует.

    Замерено живьём 13.09: закрытие нити в соседнем проекте упало с кодом 8, назвав чужой
    работой ЧЕРНОВИК соседа в plans/ и csv в backups/. Ни того ни другого
    слияние бы не коснулось: перезаписать нечем то, чего нет в истории.
    Прежние оракулы этого не видели — в песочнице главная копия чистая.

    Граница, которую тест НЕ проверяет и которая осталась у git: если нить
    приносит файл С ТЕМ ЖЕ путём, что чужой untracked, git откажет сам, своим
    точным сообщением. Точная проверка там, где ей и место.

    Чем краснеет: верните `status --porcelain` без `--untracked-files=no` —
    закрытие вернёт 8 и работа не доедет до main.
    """
    repo, home, журнал = песочница

    r = _start(repo, home, "untr")
    assert r.returncode == 0, f"{r.stdout}{r.stderr}"
    tree = home / ".worktrees" / РЕПО / "untr"
    (tree / "работа.txt").write_text("нить\n", encoding="utf-8")
    _git(tree, "add", "-A", home=home)
    _git(tree, "commit", "-qm", "work-untr", home=home)

    # Сосед оставил В ГЛАВНОЙ копии черновик, которого нет в истории.
    (repo / "чужой_черновик.md").write_text("план соседа\n", encoding="utf-8")
    assert _git(repo, "status", "--porcelain", home=home).stdout.startswith("??"), (
        "подготовка не удалась: файл обязан быть именно untracked")

    f = _finish(repo, home, "untr")
    assert f.returncode == 0, (
        f"закрытие отбито чужим untracked (rc={f.returncode}) — слияние его не "
        f"трогает, блокировать нечего:\n{f.stdout}{f.stderr}")
    слитые = _git(repo, "log", "--oneline", "main", home=home).stdout
    assert "work-untr" in слитые, f"работа нити не доехала до main:\n{слитые}"
    assert (repo / "чужой_черновик.md").exists(), "чужой черновик пропал при слиянии"


def test_без_дома_списка_закрытие_отказывает_а_не_угадывает(песочница):
    """Fail-closed: нет `git_facts` — нет закрытия.

    Мутация «fail-closed → пустой список» пережила первую редакцию оракула,
    потому что в песочнице дом списка есть всегда. А цена ошибки тут прямая:
    с пустым списком КАЖДЫЙ авто-артефакт становится «чужой работой», закрытие
    отказывает с неверной причиной — либо, при обратном знаке, чужая работа
    уезжает в мой коммит.
    """
    repo, home, журнал = песочница
    _start(repo, home, "nolist")
    (repo / "git_facts.py").unlink()
    r = _finish(repo, home, "nolist")
    assert r.returncode == 9, (
        f"без списка машинных артефактов закрытие обязано отказать:\n{r.stdout}{r.stderr}")
    assert "список машинных артефактов" in (r.stdout + r.stderr), "причина не названа"


def test_имя_с_пробелом_и_кириллицей_читается_целиком(песочница):
    """Р5/F6 внешнего ревью: имена файлов читались построчно, а не по NUL.

    ЗАМЕР 15.09 (git 2.54), три формы одного и того же состояния:
      `status --porcelain`                  → ` M "\\321\\201..."` — октальные escape
      `-c core.quotepath=false` + porcelain → ` M "Claude outputs/файл с пробелом.md"`
      `status -z --porcelain`               → ` M Claude outputs/файл с пробелом.md\\0`

    То есть построчный разбор отдавал имя В КАВЫЧКАХ, а кириллицу — ещё и
    экранированной. Последствие не косметическое: имя в кавычках не совпадает
    ни с одним машинным артефактом, поэтому артефакт, чьё имя потребовало
    кавычек, уехал бы в «чужую работу» — и закрытие нити отказало бы на
    СОБСТВЕННОМ файле, сообщив человеку про чужую работу.

    Здесь это судится ИСПОЛНЕНИЕМ: чужой файл с пробелом и кириллицей в имени
    обязан быть назван целиком и читаемо, а не обрубком в escape-последовательностях.
    """
    repo, home, журнал = песочница
    _start(repo, home, "tricky-names")
    tree = home / ".worktrees" / РЕПО / "tricky-names"
    (tree / "work.py").write_text("z = 3\n", encoding="utf-8")
    _git(tree, "add", "-A", home=home)
    _git(tree, "commit", "-qm", "работа", home=home)

    # Чужая работа с трудным именем: пробелы И кириллица разом.
    чужой = repo / "Claude outputs" / "записка соседа.md"
    чужой.parent.mkdir(parents=True, exist_ok=True)
    чужой.write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "-A", home=home)
    _git(repo, "commit", "-qm", "чужой файл в историю", "--no-verify", home=home)
    чужой.write_text("v2 — незакоммиченная правка соседа\n", encoding="utf-8")

    r = _finish(repo, home, "tricky-names")
    вывод = r.stdout + r.stderr
    assert r.returncode == 8, (
        f"чужая незакоммиченная работа не заблокировала закрытие:\n{вывод}")
    assert "Claude outputs/записка соседа.md" in вывод, (
        f"имя чужого файла названо не целиком или в экранированном виде — "
        f"человек не поймёт, чью работу он спасает:\n{вывод}")
    assert "\\321" not in вывод and "\\320" not in вывод, (
        f"в сообщении октальные escape вместо кириллицы:\n{вывод}")


def test_артефакт_с_пробелом_в_имени_не_считается_чужим(песочница, monkeypatch):
    """Обратная сторона той же правки — и она важнее.

    Пока список артефактов резался по пробелам, артефакт с пробелом в имени
    распадался на куски, переставал узнаваться и попадал в ЧУЖОЕ. Закрытие
    нити отказывало бы на собственном файле — отказ в безопасную сторону, но
    по неверной причине, и человек искал бы несуществующего соседа.

    Живого ущерба нет: у всех четырёх нынешних артефактов имена без пробелов.
    Поэтому стенд добавляет пятый — иначе тест судил бы обстановку, в которой
    дефект не проявляется, то есть не судил бы ничего.
    """
    repo, home, журнал = песочница
    # Пятый артефакт с пробелом в имени — прямо в дом списка, как настоящий.
    gf = repo / "git_facts.py"
    исходник = gf.read_text(encoding="utf-8")
    assert "MACHINE_REGENERATED_FILES" in исходник, "стенд не несёт дом списка"
    gf.write_text(
        исходник + '\nMACHINE_REGENERATED_FILES = list(MACHINE_REGENERATED_FILES)'
                   ' + ["Claude outputs/сводка агента.md"]\n',
        encoding="utf-8")
    артефакт = repo / "Claude outputs" / "сводка агента.md"
    артефакт.parent.mkdir(parents=True, exist_ok=True)
    артефакт.write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "-A", home=home)
    _git(repo, "commit", "-qm", "артефакт в историю", "--no-verify", home=home)

    _start(repo, home, "spaced-artifact")
    tree = home / ".worktrees" / РЕПО / "spaced-artifact"
    (tree / "work.py").write_text("z = 4\n", encoding="utf-8")
    _git(tree, "add", "-A", home=home)
    _git(tree, "commit", "-qm", "работа", home=home)

    # Ровно то, что оставил бы post-commit: артефакт грязный в главной копии.
    артефакт.write_text("v2 — дописал post-commit\n", encoding="utf-8")

    r = _finish(repo, home, "spaced-artifact")
    вывод = r.stdout + r.stderr
    assert r.returncode == 0, (
        f"машинный артефакт с пробелом в имени принят за чужую работу — "
        f"закрытие отказало на собственном файле:\n{вывод}")
    осталось = _git(repo, "status", "--porcelain", "--untracked-files=no",
                    home=home).stdout
    assert "сводка агента" not in осталось, (
        f"артефакт с пробелом не застейджен — `git add` его не увидел:\n{осталось}")


# ── РЕБЕЙЗ, ЕДИНСТВЕННЫЙ ПРОГОН, ЯКОРЬ (правка 2026-09-15) ────────────────────
# ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ — четыре величины, и ни одна не «скрипт отработал»:
#   · ветка ПЕРЕСТАВЛЕНА на текущий main, а не слита с ним внутрь себя;
#   · полный прогон случился РОВНО ОДИН раз, ДО слияния, ИЗ ДЕРЕВА НИТИ;
#   · красный прогон останавливает закрытие, а названная причина его открывает
#     и уезжает в историю;
#   · якорь записки после закрытия указывает на merge-коммит, который РЕАЛЬНО
#     предок main (ребейз переписал внутренние хеши, и старый якорь бы врал).


def _коммит_в_главной(repo: Path, home: Path, текст: str) -> str:
    """Сосед двигает main, пока нить работает."""
    (repo / "чужое.txt").write_text(текст, encoding="utf-8")
    _git(repo, "add", "-A", home=home)
    _git(repo, "commit", "-qm", текст, home=home)
    return _git(repo, "rev-parse", "HEAD", home=home).stdout.strip()


def _коммит_в_нити(tree: Path, home: Path, имя: str, текст: str) -> None:
    (tree / имя).write_text(текст, encoding="utf-8")
    _git(tree, "add", "-A", home=home)
    _git(tree, "commit", "-qm", f"работа нити: {имя}", home=home)


def test_закрытие_ребейзит_ветку_на_текущий_main(песочница):
    """⭐ ГЛАВНОЕ В ПРАВКЕ. Коммит нити обязан лечь ПОВЕРХ нового main.

    До правки нить, чтобы догнать ушедший main, сливала его внутрь себя — и
    после каждого слияния требовала нового полного прогона, потому что слитое
    дерево это новый предмет. Замер 15.09: main сдвинулся трижды за сессию,
    полных прогонов вышло шесть, доказал последний.

    Оракул смотрит не на «скрипт не упал», а на ПРЕДКА: новый коммит main
    обязан стать предком коммита нити. Снимешь ребейз — коммит нити останется
    на старом основании, и здесь покраснеет.
    """
    repo, home, журнал = песочница
    _start(repo, home, "rebased")
    tree = home / ".worktrees" / РЕПО / "rebased"

    _коммит_в_нити(tree, home, "моё.txt", "работа нити\n")
    новый_main = _коммит_в_главной(repo, home, "сосед уехал вперёд")

    # До закрытия новый main НЕ предок ветки — иначе тест судил бы пустоту.
    до = _git(repo, "merge-base", "--is-ancestor", новый_main,
              "thread/rebased", home=home)
    assert до.returncode != 0, "стенд собран неверно: main уже предок ветки"

    вывод = _finish(repo, home, "rebased")
    assert вывод.returncode == 0, f"закрытие упало:\n{вывод.stdout}\n{вывод.stderr}"

    коммит_нити = _git(repo, "log", "--format=%H %s", "main", home=home).stdout
    sha = next(s.split()[0] for s in коммит_нити.splitlines()
               if "работа нити: моё.txt" in s)
    после = _git(repo, "merge-base", "--is-ancestor", новый_main, sha, home=home)
    assert после.returncode == 0, (
        "коммит нити НЕ переставлен на новый main — ребейза не было, и полный "
        "прогон судил дерево на старом основании")


def test_прогон_один_и_до_слияния_и_из_дерева_нити(песочница):
    """Порядок и есть содержание правки, поэтому он и проверяется.

    Прогон ПОСЛЕ слияния был бы вторым прогоном того же самого. Прогон ДО
    ребейза судил бы дерево, которого не будет. Прогон из ГЛАВНОЙ копии судил
    бы чужое дерево. Все три перестановки роняют этот тест.
    """
    repo, home, журнал = песочница
    _start(repo, home, "once")
    tree = home / ".worktrees" / РЕПО / "once"
    _коммит_в_нити(tree, home, "моё.txt", "работа\n")

    вывод = _finish(repo, home, "once")
    assert вывод.returncode == 0, f"закрытие упало:\n{вывод.stdout}\n{вывод.stderr}"

    звали = _звали(журнал)
    assert звали.count("tests") == 1, (
        f"полный прогон случился {звали.count('tests')} раз(а), а не один: {звали}")
    assert "pre-merge-commit" in звали, "хук слияния не звался — гейты мимо"
    assert звали.count("pre-merge-commit") == 2, (
        f"хук слияния звался {звали.count('pre-merge-commit')} раз, ждали два — "
        f"пробный до прогона и настоящий при слиянии: {звали}")
    assert звали.index("pre-merge-commit") < звали.index("tests"), (
        f"гейты слияния пробуются ПОСЛЕ прогона — их отказ сожжёт прогон: {звали}")
    последний_хук = len(звали) - 1 - звали[::-1].index("pre-merge-commit")
    assert звали.index("tests") < последний_хук, (
        f"прогон идёт ПОСЛЕ слияния — он судит уже слитое, а не то, что уедет: {звали}")
    assert звали.index("tests") < звали.index("deploy"), (
        f"прогон идёт после деплоя — судить уже уехавшее поздно: {звали}")

    откуда = Path(str(журнал) + ".cwd").read_text(encoding="utf-8").strip()
    assert Path(откуда).resolve() == tree.resolve(), (
        f"прогон запущен не из дерева нити, а из {откуда} — он рсинкает не тот "
        f"код, и зелёный относится не к той работе (§12)")


def test_красный_прогон_не_сливает_и_не_деплоит(песочница):
    """Отказ обязан быть ПОЛНЫМ: ни слияния, ни деплоя, дерево на месте.

    Половинчатый отказ («слил, но не задеплоил») хуже красного прогона: работа
    оказывается в main, а на Studio её нет, и расхождение видно только в логе.
    """
    repo, home, журнал = песочница
    _start(repo, home, "red")
    tree = home / ".worktrees" / РЕПО / "red"
    _коммит_в_нити(tree, home, "моё.txt", "работа\n")
    main_до = _git(repo, "rev-parse", "main", home=home).stdout.strip()

    вывод = _finish(repo, home, "red", STAND_RUN_RC="1")
    assert вывод.returncode == 12, (
        f"красный прогон не отбил закрытие (rc={вывод.returncode}):\n{вывод.stderr}")

    звали = _звали(журнал)
    assert звали.count("pre-merge-commit") == 1, (
        f"настоящее слияние всё же случилось (хук звался не только пробно): {звали}")
    assert "deploy" not in звали, f"деплой всё же случился: {звали}"
    assert _git(repo, "rev-parse", "main", home=home).stdout.strip() == main_до, \
        "main сдвинулся при красном прогоне"
    assert tree.exists(), "дерево нити убрано — чинить красный негде"


def test_названная_причина_открывает_красный_и_уезжает_в_историю(песочница):
    """Обход есть, но он оставляет КВИТАНЦИЮ, а не живёт в памяти закрывавшего.

    Почему обход вообще нужен: чужой красный — обычное состояние (15.09 два
    чужих прожили сутки). Глухой отказ сделал бы нить незакрываемой из-за
    соседа, и лечилось бы это `--no-verify`-культурой. Пустая причина не
    принимается — это проверяет тест выше, где переменной просто нет.
    """
    repo, home, журнал = песочница
    _start(repo, home, "accepted")
    tree = home / ".worktrees" / РЕПО / "accepted"
    _коммит_в_нити(tree, home, "моё.txt", "работа\n")

    причина = "чужой красный BL-ЧУЖОЙ-1, воспроизведён на чистом main"
    вывод = _finish(repo, home, "accepted", STAND_RUN_RC="1",
                    THREAD_FINISH_ACCEPT_RED=причина)
    assert вывод.returncode == 0, (
        f"названная причина не открыла закрытие:\n{вывод.stdout}\n{вывод.stderr}")

    история = _git(repo, "log", "--format=%B", "-n", "5", "main", home=home).stdout
    assert причина in история, (
        "причина обхода не уехала в историю — обход стал невидимым, то есть "
        f"тем самым молчанием, против которого он и оформлен:\n{история}")


def test_дрейф_main_во_время_прогона_отбивает_слияние(песочница):
    """Сосед двинул main за те минуты, что шёл прогон → сливать нельзя.

    Прогон судил дерево на старом основании; слить его значило бы получить
    зелёный не на той версии (§12) — ровно тот класс, ради которого правка и
    делалась. Своего цикла ретраев нет намеренно: он крутился бы именно в те
    часы, когда сосед активен. Повтор закрытия переставит и прогонит заново.
    """
    repo, home, журнал = песочница
    _start(repo, home, "drift")
    tree = home / ".worktrees" / РЕПО / "drift"
    _коммит_в_нити(tree, home, "моё.txt", "работа\n")

    вывод = _finish(repo, home, "drift", STAND_DRIFT_REPO=str(repo))
    assert вывод.returncode == 13, (
        f"дрейф main не отбил слияние (rc={вывод.returncode}):\n{вывод.stderr}")
    звали = _звали(журнал)
    assert звали.count("pre-merge-commit") == 1, (
        f"настоящее слияние случилось на дрейфе: {звали}")
    assert "deploy" not in звали, f"деплой случился на дрейфе: {звали}"
    assert tree.exists(), "дерево убрано — переделывать закрытие негде"


def test_дрейф_записками_сливается_без_второго_полного_прогона(песочница):
    """Сосед сдвинул main ТОЛЬКО запиской → ребейз, затронутые тесты, слияние.

    Замер 21.09: из четырёх дрейфов за день два были записками, и каждый сжигал
    пятиминутный прогон. Записки читают тесты, поэтому «не проверять» нельзя —
    проверяются затронутые, за секунды; полный прогон второй раз не идёт.
    """
    repo, home, журнал = песочница
    _start(repo, home, "notes")
    tree = home / ".worktrees" / РЕПО / "notes"
    _коммит_в_нити(tree, home, "моё.txt", "работа\n")

    вывод = _finish(repo, home, "notes", STAND_DRIFT_REPO=str(repo),
                    STAND_DRIFT_FILE="docs/handoff/сосед/LATEST.md")
    assert вывод.returncode == 0, f"дрейф записками отбил слияние (rc={вывод.returncode}):\n{вывод.stderr}"
    звали = _звали(журнал)
    assert звали.count("tests") == 1, f"полный прогон пошёл второй раз: {звали}"
    assert "affected" in звали, f"затронутые тесты по дрейфу не прогнаны: {звали}"
    assert "deploy" in звали
    лог = _git(repo, "log", "--format=%s", "main", home=home).stdout
    assert "Merge branch 'thread/notes'" in лог and "сосед двинул main" in лог


def test_дрейф_записками_с_красными_затронутыми_не_сливает(песочница):
    repo, home, журнал = песочница
    _start(repo, home, "notesred")
    tree = home / ".worktrees" / РЕПО / "notesred"
    _коммит_в_нити(tree, home, "моё.txt", "работа\n")

    вывод = _finish(repo, home, "notesred", STAND_DRIFT_REPO=str(repo),
                    STAND_DRIFT_FILE="docs/handoff/сосед/LATEST.md", STAND_AFFECTED_RC="1")
    assert вывод.returncode == 13, f"красные затронутые не остановили слияние (rc={вывод.returncode})"
    assert "deploy" not in _звали(журнал)


def test_дрейф_записками_без_отбора_тестов_не_сливает(песочница):
    """Отбор отказался («правка широкая») — судить нечем, отказ как прежде."""
    repo, home, журнал = песочница
    _start(repo, home, "notesnosel")
    tree = home / ".worktrees" / РЕПО / "notesnosel"
    _коммит_в_нити(tree, home, "моё.txt", "работа\n")

    вывод = _finish(repo, home, "notesnosel", STAND_DRIFT_REPO=str(repo),
                    STAND_DRIFT_FILE="docs/handoff/сосед/LATEST.md",
                    STAND_AFFECTED_OUT="affected_tests: правка широкая")
    assert вывод.returncode == 13
    assert "deploy" not in _звали(журнал)


def test_якорь_записки_указывает_на_merge_коммит(песочница):
    """Развилка владельца 15.09: ребейз ломает якорь, машина ставит новый.

    Внутренние хеши нити ребейз переписывает, поэтому якорь, написанный
    автором, перестаёт быть предком main — и записка начинает выглядеть
    недостоверной, а покраснеть нечему: `is-ancestor` не читает ни один тест.
    Скрипт ставит якорь на merge-коммит, и вот ЭТОТ предок main всегда.
    """
    repo, home, журнал = песочница
    _start(repo, home, "anchored")
    tree = home / ".worktrees" / РЕПО / "anchored"
    записка = tree / "docs" / "handoff" / "anchored" / "LATEST.md"
    записка.parent.mkdir(parents=True)
    записка.write_text("# LATEST — нить `anchored`\n\nТекст автора.\n",
                       encoding="utf-8")
    _git(tree, "add", "-A", home=home)
    _git(tree, "commit", "-qm", "записка нити", home=home)

    вывод = _finish(repo, home, "anchored")
    assert вывод.returncode == 0, f"закрытие упало:\n{вывод.stdout}\n{вывод.stderr}"

    текст = (repo / "docs" / "handoff" / "anchored" / "LATEST.md").read_text(
        encoding="utf-8")
    строки = [s for s in текст.splitlines() if s.startswith("> Якорь на main:")]
    assert len(строки) == 1, f"якорей в записке {len(строки)}, ожидался один:\n{текст}"
    sha = строки[0].split("`")[1]
    assert _git(repo, "merge-base", "--is-ancestor", sha, "main",
                home=home).returncode == 0, (
        f"якорь {sha} НЕ предок main — записка врёт ровно там, где обещает "
        f"достоверность")
    assert "Текст автора." in текст, "скрипт затёр прозу автора (§22)"


def test_повторный_якорь_заменяет_строку_а_не_плодит_вторую(песочница):
    """Вторая строка якоря разошлась бы с первой — две правды в одном файле.

    Случай живой: деплой отбился, нить закрывают повторно.
    """
    repo, home, журнал = песочница
    _start(repo, home, "reanchor")
    tree = home / ".worktrees" / РЕПО / "reanchor"
    записка = tree / "docs" / "handoff" / "reanchor" / "LATEST.md"
    записка.parent.mkdir(parents=True)
    записка.write_text(
        "# LATEST — нить `reanchor`\n\n"
        "> Якорь на main: `deadbee` · проверка: "
        "`git merge-base --is-ancestor deadbee HEAD`\n"
        "> (строку ставит scripts/thread_finish.sh при закрытии — "
        "хеши коммитов нити переписывает ребейз, живёт только merge-коммит)\n"
        "\nТекст автора.\n", encoding="utf-8")
    _git(tree, "add", "-A", home=home)
    _git(tree, "commit", "-qm", "записка со старым якорем", home=home)

    вывод = _finish(repo, home, "reanchor")
    assert вывод.returncode == 0, f"закрытие упало:\n{вывод.stdout}\n{вывод.stderr}"

    текст = (repo / "docs" / "handoff" / "reanchor" / "LATEST.md").read_text(
        encoding="utf-8")
    строки = [s for s in текст.splitlines() if s.startswith("> Якорь на main:")]
    assert len(строки) == 1, f"старый якорь не заменён, а дополнен:\n{текст}"
    assert "deadbee" not in текст, f"протухший якорь остался в записке:\n{текст}"


# ── ДЕШЁВОЕ ДО ДОРОГОГО (правка 2026-09-21) ────────────────────────────────────
# Первое живое закрытие новым скриптом 21.09 сожгло прогон на гейте квитанции:
# гейт отвечает за секунды, но звался ПОСЛЕ пятиминутного прогона. И оставил
# главную копию посреди слияния — «отмени руками» стояло советом в тексте.


def _в_середине_слияния(repo: Path, home: Path) -> bool:
    return _git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD",
                home=home).returncode == 0


def test_гейт_слияния_отбивает_ДО_прогона(песочница):
    """Отказ гейта обязан стоить секунды, а не прогон.

    Хук слияния краснеет на первом (пробном) вызове. Закрытие должно встать
    кодом 14, НЕ запуская прогон, и вернуть главную копию из пробного слияния.
    """
    repo, home, журнал = песочница
    _start(repo, home, "gatefirst")
    tree = home / ".worktrees" / РЕПО / "gatefirst"
    _коммит_в_нити(tree, home, "моё.txt", "работа\n")
    main_до = _git(repo, "rev-parse", "main", home=home).stdout.strip()

    вывод = _finish(repo, home, "gatefirst", STAND_GATE_FAIL_ON="1")
    assert вывод.returncode == 14, (
        f"гейт не отбил до прогона (rc={вывод.returncode}):\n{вывод.stderr}")
    звали = _звали(журнал)
    assert "tests" not in звали, (
        f"прогон всё же запущен при отбитом гейте — пять минут в топку: {звали}")
    assert "deploy" not in звали, f"деплой при отбитом гейте: {звали}"
    assert not _в_середине_слияния(repo, home), (
        "главная копия осталась посреди пробного слияния — общая для всех сессий")
    assert _git(repo, "rev-parse", "main", home=home).stdout.strip() == main_до, \
        "main сдвинулся при отбитом гейте"
    assert tree.exists(), "дерево нити убрано — чинить негде"


def test_отбитое_слияние_не_оставляет_главную_копию_посреди_слияния(песочница):
    """Пробный гейт прошёл, настоящий отбил — главная копия обязана вернуться.

    До 21.09 скрипт выходил с кодом 6 и советом «отмени (git merge --abort)»,
    а главная копия, общая для всех сессий, стояла посреди слияния, пока человек
    не прочтёт. 21.09 я так её и нашёл. Любой соседский коммит в это время унёс
    бы чужое слияние в свой.
    """
    repo, home, журнал = песочница
    _start(repo, home, "abortmerge")
    tree = home / ".worktrees" / РЕПО / "abortmerge"
    _коммит_в_нити(tree, home, "моё.txt", "работа\n")

    вывод = _finish(repo, home, "abortmerge", STAND_GATE_FAIL_ON="2")
    assert вывод.returncode == 6, (
        f"настоящий гейт не отбил слияние (rc={вывод.returncode}):\n{вывод.stderr}")
    assert not _в_середине_слияния(repo, home), (
        "главная копия осталась посреди слияния — отмена по-прежнему совет, а не шаг")
    assert "deploy" not in _звали(журнал), "деплой после отбитого слияния"
    assert tree.exists(), "дерево нити убрано — чинить негде"


def test_закрытие_пишет_строку_журнала_своим_коммитом(песочница):
    """23.09 (нить changelog-at-merge): журнал молчал 11 дней — хук на ветке нити выходит
    до doc_agent, слияние хук не зовёт. Закрытие зовёт `doc_agent --thread-merge <merge>`
    и коммитит журнал ОТДЕЛЬНЫМ коммитом с гейтами (не в якоре с --no-verify)."""
    repo, home, журнал = песочница
    (repo / "doc_agent.py").write_text(
        "import sys, subprocess\n"
        "a = sys.argv\n"
        "sha, slug = a[a.index('--thread-merge') + 1], a[a.index('--thread') + 1]\n"
        "open('CHANGELOG.md', 'a').write(f'| строка | [нить {slug}] {sha} |\\n')\n"
        "subprocess.run(['git', 'add', 'CHANGELOG.md'])\n", encoding="utf-8")
    _git(repo, "add", "doc_agent.py", home=home)
    _git(repo, "commit", "-qm", "стенд doc_agent", home=home)
    _start(repo, home, "logged")
    tree = home / ".worktrees" / РЕПО / "logged"
    (tree / "code.py").write_text("x = 1\n", encoding="utf-8")
    _git(tree, "add", "-A", home=home)
    _git(tree, "commit", "-qm", "feat: код нити", home=home)
    вывод = _finish(repo, home, "logged")
    assert вывод.returncode == 0, f"закрытие упало:\n{вывод.stdout}\n{вывод.stderr}"
    журнал_изменений = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "[нить logged]" in журнал_изменений
    темы = _git(repo, "log", "--format=%s", "-5", "main", home=home).stdout
    assert "docs(changelog): нить logged" in темы, темы
    assert not _git(repo, "status", "--porcelain", "CHANGELOG.md", home=home).stdout.strip(), \
        "строка журнала осталась незакоммиченной"


def test_отказ_строки_журнала_не_останавливает_закрытие(песочница):
    repo, home, журнал = песочница
    (repo / "doc_agent.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
    _git(repo, "add", "doc_agent.py", home=home)
    _git(repo, "commit", "-qm", "стенд doc_agent падает", home=home)
    _start(repo, home, "broken-log")
    tree = home / ".worktrees" / РЕПО / "broken-log"
    (tree / "code.py").write_text("x = 1\n", encoding="utf-8")
    _git(tree, "add", "-A", home=home)
    _git(tree, "commit", "-qm", "feat: код", home=home)
    вывод = _finish(repo, home, "broken-log")
    assert вывод.returncode == 0 and "строка журнала за нить broken-log не записана" in вывод.stderr


# ── ЗАМОК ГЛАВНОЙ КОПИИ (2026-09-23, нить finish-lock) ─────────────────────────
# Замер 23.09: `d15f91d` — коммит-якорь закрытия first-miss (он идёт с --no-verify)
# лёг, пока в главной копии шло слияние onboarding-launchd. MERGE_HEAD был чужой, и
# git молча сделал из якоря merge-коммит ЧУЖОЙ нити с сообщением «handoff(first-miss)».
# Код уцелел, но история соврала: работа onboarding-launchd вошла в main под чужим
# именем, и её якорь не проставился. Причина в устройстве: главная копия общая, а
# отрезки, где она меняется, ничем не разделены между процессами.


def _ждать(путь: Path, сек: float = 30.0) -> bool:
    import time
    конец = time.time() + сек
    while time.time() < конец:
        if путь.exists():
            return True
        time.sleep(0.1)
    return False


def _стенд_пауз(repo: Path, home: Path) -> None:
    """doc_agent и хук слияния, которые умеют остановиться по метке.

    Пауза в doc_agent — это окно МЕЖДУ слиянием нити и её якорем (ровно там стоял
    first-miss 23.09). Пауза в хуке на втором вызове — окно НАСТОЯЩЕГО слияния,
    когда MERGE_HEAD уже есть (там стоял onboarding-launchd).
    """
    (repo / "doc_agent.py").write_text(
        "import os, pathlib, subprocess, time\n"
        "m, w = os.environ.get('STAND_DOC_MARK'), os.environ.get('STAND_DOC_WAIT')\n"
        "if m: pathlib.Path(m).write_text('1')\n"
        "if w:\n"
        "    t = time.time() + float(os.environ.get('STAND_DOC_WAIT_SEC', '8'))\n"
        "    while time.time() < t and not pathlib.Path(w).exists(): time.sleep(0.1)\n"
        "b = os.environ.get('STAND_DOC_FOREIGN_MERGE')\n"
        "if b: subprocess.run(['git', 'merge', '--no-ff', '--no-commit', b],\n"
        "                     capture_output=True)\n", encoding="utf-8")
    _git(repo, "add", "doc_agent.py", home=home)
    _git(repo, "commit", "-qm", "стенд doc_agent с паузой", home=home)
    реальный = _git(repo, "rev-parse", "--absolute-git-dir", home=home).stdout.strip()
    h = Path(реальный) / "hooks" / "pre-merge-commit"
    h.write_text(
        '#!/bin/bash\n'
        'if [[ -n "${STAND_MERGE_MARK:-}" ]]; then\n'
        '  c=$(( $(cat "$STAND_MERGE_MARK.n" 2>/dev/null || echo 0) + 1 )); echo $c > "$STAND_MERGE_MARK.n"\n'
        '  if [[ "$c" == "${STAND_MERGE_PAUSE_ON:-2}" ]]; then touch "$STAND_MERGE_MARK"\n'
        '    for _ in $(seq 100); do [[ -f "$STAND_MERGE_WAIT" ]] && break; sleep 0.1; done; fi\n'
        'fi\nexit 0\n', encoding="utf-8")
    h.chmod(0o755)


def _нить_с_запиской(repo: Path, home: Path, slug: str) -> Path:
    _start(repo, home, slug)
    tree = home / ".worktrees" / РЕПО / slug
    записка = tree / "docs" / "handoff" / slug / "LATEST.md"
    записка.parent.mkdir(parents=True)
    записка.write_text(f"# LATEST — нить `{slug}`\n\nТекст.\n", encoding="utf-8")
    (tree / f"{slug}.txt").write_text("работа\n", encoding="utf-8")
    _git(tree, "add", "-A", home=home)
    _git(tree, "commit", "-qm", f"работа нити {slug}", home=home)
    return tree


def _finish_фоном(repo: Path, home: Path, slug: str, лог: Path, **окружение: str):
    e = _env(home)
    e.update(окружение)
    return subprocess.Popen(["bash", str(repo / "scripts" / "thread_finish.sh"), slug],
                            cwd=str(repo), stdout=лог.open("w"), stderr=subprocess.STDOUT,
                            env=e)


@pytest.mark.parametrize("окно", ["1", "2"], ids=["пробное_слияние", "настоящее_слияние"])
def test_якорь_соседа_не_уносит_чужое_слияние(песочница, окно):
    """⭐ Воспроизведение d15f91d двумя НАСТОЯЩИМИ закрытиями.

    B слил свою нить и стоит в doc_agent (перед якорем). A доходит до слияния и
    стоит в его хуке. Окон два: ПРОБНОЕ слияние (--no-commit, MERGE_HEAD на диске —
    так вышел d15f91d с двумя родителями) и НАСТОЯЩЕЕ (индекс уже собран, MERGE_HEAD
    на диске нет — коммит соседа забирает чужой индекс с ОДНИМ родителем). B продолжает и коммитит якорь.
    Без разделения главной копии якорь B становится merge-коммитом нити A.
    Оракул — форма истории, а не коды выхода: коммит «handoff(...)» обязан иметь
    одного родителя, а каждый merge-коммит — нести имя своей нити.
    """
    repo, home, журнал = песочница
    tmp = repo.parent
    _стенд_пауз(repo, home)
    _нить_с_запиской(repo, home, "second")
    _нить_с_запиской(repo, home, "first")
    b_merged, a_in_merge, b_done = tmp / "b_merged", tmp / "a_in_merge", tmp / "b_done"

    b = _finish_фоном(repo, home, "second", tmp / "b.log",
                      STAND_DOC_MARK=str(b_merged), STAND_DOC_WAIT=str(a_in_merge))
    assert _ждать(b_merged), "B не дошёл до doc_agent:\n" + (tmp / "b.log").read_text()
    a = _finish_фоном(repo, home, "first", tmp / "a.log",
                      STAND_MERGE_MARK=str(a_in_merge), STAND_MERGE_WAIT=str(b_done),
                      STAND_MERGE_PAUSE_ON=окно)
    rb = b.wait(timeout=90)
    b_done.write_text("1")
    ra = a.wait(timeout=90)
    граф = _git(repo, "log", "--graph", "--format=%h %s", "--name-only", "main", home=home).stdout
    логи = f"{граф}\nB rc={rb}:\n{(tmp / 'b.log').read_text()}\nA rc={ra}:\n{(tmp / 'a.log').read_text()}"

    история = _git(repo, "log", "--format=%H|%P|%s", "main", home=home).stdout.splitlines()
    for строка in история:
        sha, родители, тема = строка.split("|", 2)
        n = len(родители.split())
        if тема.startswith("handoff("):
            assert n == 1, f"якорь {sha[:7]} «{тема}» унёс чужое слияние:\n{логи}"
            тронул = set(_git(repo, "diff", "--name-only", f"{sha}^", sha,
                              home=home).stdout.split())
            assert all(t.startswith("docs/handoff/") for t in тронул), (
                f"якорь {sha[:7]} унёс чужой индекс: {sorted(тронул)}\n{логи}")
        if n > 1:
            assert тема.startswith("Merge branch 'thread/"), f"{sha[:7]} «{тема}»:\n{логи}"
    assert rb == 0 and ra == 0, логи
    for slug in ("first", "second"):
        assert (repo / f"{slug}.txt").exists(), f"работа нити {slug} не в main:\n{логи}"


def _путь_замка(repo: Path, home: Path) -> Path:
    r = subprocess.run(["bash", str(repo / "scripts" / "git-hooks" / "finish_lock_guard.sh"),
                        "--path"], cwd=str(repo), capture_output=True, text=True,
                       env=_env(home), timeout=30)
    return Path(r.stdout.strip())


def _держатель(замок: Path):
    """Чужой процесс держит замок, как держало бы его закрытие соседа."""
    import sys
    p = subprocess.Popen([sys.executable, "-c",
                          "import fcntl,sys,time; f=open(sys.argv[1],'a'); "
                          "fcntl.flock(f,fcntl.LOCK_EX); print('ok',flush=True); time.sleep(60)",
                          str(замок)], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "ok"
    return p


def test_занятый_замок_ждёт_и_отказывает_ничего_не_трогая(песочница):
    """Замок держит сосед — закрытие обязано ждать, а по таймауту уйти кодом 15,
    не тронув главную копию и не запустив прогон. Держатель умер — ядро сняло
    замок само, и следующее закрытие проходит (замок-флаг здесь остался бы висеть)."""
    repo, home, журнал = песочница
    _start(repo, home, "locked")
    tree = home / ".worktrees" / РЕПО / "locked"
    _коммит_в_нити(tree, home, "моё.txt", "работа\n")
    main_до = _git(repo, "rev-parse", "main", home=home).stdout.strip()
    замок = _путь_замка(repo, home)
    assert замок.parent == Path(_git(repo, "rev-parse", "--absolute-git-dir",
                                     home=home).stdout.strip()), \
        f"замок не в общем каталоге .git: {замок}"

    держатель = _держатель(замок)
    try:
        вывод = _finish(repo, home, "locked", THREAD_FINISH_LOCK_WAIT="2")
    finally:
        держатель.kill()
        держатель.wait()
    assert вывод.returncode == 15, f"rc={вывод.returncode}:\n{вывод.stderr}"
    assert "жду замок" in вывод.stderr
    звали = _звали(журнал)
    assert "tests" not in звали and "deploy" not in звали, звали
    assert _git(repo, "rev-parse", "main", home=home).stdout.strip() == main_до

    повтор = _finish(repo, home, "locked", THREAD_FINISH_LOCK_WAIT="2")
    assert повтор.returncode == 0, f"замок пережил держателя:\n{повтор.stderr}"


def test_якорь_не_коммитится_внутрь_чужого_слияния(песочница):
    """Вторая линия: кто-то начал слияние в главной копии руками, мимо замка.
    Якорь (он с --no-verify) обязан не коммититься, а чужое слияние — остаться
    нетронутым, как его оставили."""
    repo, home, журнал = песочница
    _стенд_пауз(repo, home)
    _git(repo, "checkout", "-qb", "чужая", home=home)
    (repo / "чужое.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A", home=home)
    _git(repo, "commit", "-qm", "чужая ветка", home=home)
    _git(repo, "checkout", "-q", "main", home=home)
    _нить_с_запиской(repo, home, "anch")

    вывод = _finish(repo, home, "anch", STAND_DOC_FOREIGN_MERGE="чужая")
    assert _в_середине_слияния(repo, home), "чужое слияние исчезло — его кто-то закончил"
    темы = _git(repo, "log", "--format=%P|%s", "main", home=home).stdout.splitlines()
    assert not any(t.split("|", 1)[1].startswith("handoff(anch)") for t in темы), (
        f"якорь закоммичен внутрь чужого слияния:\n{темы}\n{вывод.stderr}")
    assert "якорь не коммичу" in вывод.stderr, вывод.stderr


def _суд(repo: Path, home: Path, cwd: Path | None = None, **окружение: str):
    e = _env(home)
    e.update(окружение)
    return subprocess.run(["bash", str(repo / "scripts" / "git-hooks" / "finish_lock_guard.sh")],
                          cwd=str(cwd or repo), capture_output=True, text=True, env=e,
                          timeout=30)


def test_pre_commit_суд_отбивает_коммит_в_главной_пока_замок_чужой(песочница):
    repo, home, журнал = песочница
    _start(repo, home, "side")
    tree = home / ".worktrees" / РЕПО / "side"
    assert _суд(repo, home).returncode == 0, "свободный замок отбил коммит"
    из_нити = subprocess.run(["bash", str(repo / "scripts" / "git-hooks" / "finish_lock_guard.sh"),
                             "--path"], cwd=str(tree), capture_output=True, text=True,
                            env=_env(home), timeout=30).stdout.strip()
    assert Path(из_нити) == _путь_замка(repo, home), (
        f"из дерева нити замок другой ({из_нити}) — деревья запирали бы разные файлы")
    держатель = _держатель(_путь_замка(repo, home))
    try:
        занят = _суд(repo, home)
        свой = _суд(repo, home, THREAD_FINISH_HOLDS_LOCK="1")
        нить = _суд(repo, home, cwd=tree)
    finally:
        держатель.kill()
        держатель.wait()
    assert занят.returncode != 0 and "[замок закрытия]" in занят.stderr, занят.stderr
    assert свой.returncode == 0, "своё закрытие отбито собственным замком — самоблокировка"
    assert нить.returncode == 0, "коммит в дереве нити отбит — у него свой индекс"
    assert _суд(repo, home).returncode == 0, "замок пережил держателя"


def test_потомок_деплоя_не_уносит_замок(песочница):
    """Ключевой момент замка: дескриптор 9 наследуют дети. Деплой рестартит
    службы; потомок, переживший закрытие, держал бы замок, пока жив, — и все
    следующие закрытия ждали бы его. Стенд: деплой оставляет фоновый процесс."""
    repo, home, журнал = песочница
    деплой = repo / "scripts" / "git-hooks" / "post-commit-macbook"
    pid = repo.parent / "bg.pid"
    деплой.write_text(f'#!/bin/bash\nnohup sleep 20 >/dev/null 2>&1 &\necho $! > "{pid}"\nexit 0\n',
                      encoding="utf-8")
    _git(repo, "add", "-A", home=home)
    _git(repo, "commit", "-qm", "стенд: деплой с фоновым потомком", home=home)
    _start(repo, home, "daemon")
    _коммит_в_нити(home / ".worktrees" / РЕПО / "daemon", home, "моё.txt", "работа\n")
    вывод = _finish(repo, home, "daemon")
    assert вывод.returncode == 0, вывод.stderr
    суд = _суд(repo, home)
    if pid.exists():
        subprocess.run(["kill", pid.read_text().strip()], capture_output=True)
    assert суд.returncode == 0, "замок пережил закрытие — его унёс потомок деплоя"
