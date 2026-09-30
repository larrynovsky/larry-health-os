"""Оракул: записка соседней нити доходит МЕХАНИЗМОМ, а не вниманием читателя.

ЗАМЕР, РАДИ КОТОРОГО ЭТО ЕСТЬ (13.09.2026). `docs/handoff/` не назван в
read-first ни в §B этого свода, ни в ХУК #1 соседнего проекта — грепом ноль упоминаний.
То есть записка, оставленная одной сессией для другой, доставлялась привычкой.
Цена промаха в тот же день: две сессии написали ОДИН модуль на 167 строк под
одни шесть строк БД, потому что каждая не знала о второй. Инструментального
канала между задачами Cowork нет (`ListAgents` пуст у обеих сторон), общий
репозиторий — единственный.

ЧТО ПРОВЕРЯЕТСЯ — ПОВЕДЕНИЕ НАСТОЯЩЕГО ФАЙЛА, не наличие строки.
`scripts/git-hooks/handoff_notes_notice.sh` ИСПОЛНЯЕТСЯ в одноразовом
репозитории. Поэтому оракул краснеет и когда доставку удалили, и когда она
осталась, но перестала работать (неверный путь, сломанный фильтр) — упоминание
в комментарии такую мутацию пережило бы, а этот тест нет. Класс уже стрелял в
этом проекте дважды (О-14, M3).

ПОЧЕМУ ТЕЛО ТЕПЕРЬ ОТДЕЛЬНЫМ ФАЙЛОМ (13.09, второй заход). Раньше тело жило
блоком внутри `pre-commit`, а тест извлекал его по маркерам-комментариям. Это
работало, но блок был дословной копией в двух репозиториях: правка одной копии
молча оставляла вторую сломанной. Теперь дом один, зовут двое, а извлечение по
маркерам исчезло вместе с причиной — тест запускает файл как файл.

ГРАНИЦА ЧЕСТНО. Тест доказывает, что доставка ПЕЧАТАЕТ. Что напечатанное
прочитали — машине недоступно, и правило §21 про ответ в LATEST адресата
остаётся MANUAL. Здесь чинится ровно одна половина: раньше не было и печати.
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
ДОСТАВКА = REPO_ROOT / "scripts" / "git-hooks" / "handoff_notes_notice.sh"
ХУК = REPO_ROOT / "scripts" / "git-hooks" / "pre-commit"
ПОСТ = REPO_ROOT / "scripts" / "git-hooks" / "post-commit-macbook"


def _env(home: Path) -> dict[str, str]:
    e = dict(os.environ)
    for v in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_COMMON_DIR",
              "GIT_PREFIX", "GIT_OBJECT_DIRECTORY", "GIT_NAMESPACE",
              "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_QUARANTINE_PATH",
              "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE",
              "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE"):
        e.pop(v, None)
    e["HOME"] = str(home)
    return e


def _git(repo: Path, *args: str, home: Path, **kw):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, timeout=60, env=_env(home), **kw)


@pytest.fixture()
def стенд():
    tmp = Path(tempfile.mkdtemp(prefix="handoff_note_"))
    home = tmp / "home"
    home.mkdir()
    repo = tmp / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", home=home)
    _git(repo, "config", "user.email", "t@t", home=home)
    _git(repo, "config", "user.name", "t", home=home)
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A", home=home)
    _git(repo, "commit", "-qm", "base", "--no-verify", home=home)
    try:
        yield repo, home
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _прогнать(repo: Path, home: Path) -> str:
    assert ДОСТАВКА.is_file(), (
        f"нет файла доставки {ДОСТАВКА} — записки вернулись к вниманию читателя")
    r = subprocess.run(["bash", str(ДОСТАВКА)], cwd=str(repo), capture_output=True,
                       text=True, timeout=60, env=_env(home))
    assert r.returncode == 0, f"доставка не должна ронять коммит: {r.stderr}"
    return r.stdout


@pytest.mark.parametrize("нить", ["models-split", "чужая-нить"])
@pytest.mark.host_only
def test_свежая_записка_названа(стенд, нить):
    """Записка, положенная соседом, обязана быть НАЗВАНА при коммите.

    Два имени не для красоты. Настоящие нити зовутся латиницей, но git
    ЭКРАНИРУЕТ не-ASCII пути в `--name-only`, и первая редакция теряла
    кириллическое имя молча: строка переставала заканчиваться на LATEST.md, а
    отсутствие вывода неотличимо от отсутствия записок. Поймано этим тестом на
    первом прогоне; лечение — `core.quotepath=false` в самой доставке.
    """
    repo, home = стенд
    (repo / "docs" / "handoff" / нить).mkdir(parents=True)
    (repo / "docs" / "handoff" / нить / "LATEST.md").write_text(
        "# нить\n\n## Записка соседней сессии\nберу файлы A и B\n", encoding="utf-8")
    _git(repo, "add", "-A", home=home)
    _git(repo, "commit", "-qm", "записка", "--no-verify", home=home)

    out = _прогнать(repo, home)
    assert нить in out, (
        f"свежая записка не названа — доставка не работает:\n{out}")
    assert "LATEST" in out, f"не сказано, что именно читать:\n{out}"


@pytest.mark.host_only
def test_без_записок_молчит(стенд):
    """Пустой вывод при отсутствии записок: иначе строка станет фоном.

    Без этого утверждения доставка могла бы печатать заголовок всегда, и его
    перестали бы замечать — датчик, который говорит одно и то же, не датчик.
    """
    repo, home = стенд
    out = _прогнать(repo, home)
    assert out.strip() == "", f"при отсутствии записок доставка обязана молчать:\n{out}"


@pytest.mark.host_only
def test_старая_записка_не_поднимается(стенд):
    """Записка старше окна не всплывает — иначе список рос бы вечно."""
    repo, home = стенд
    (repo / "docs" / "handoff" / "древняя").mkdir(parents=True)
    (repo / "docs" / "handoff" / "древняя" / "LATEST.md").write_text(
        "старое\n", encoding="utf-8")
    _git(repo, "add", "-A", home=home)
    старая = "2026-01-01T12:00:00"
    e = _env(home)
    e["GIT_AUTHOR_DATE"] = старая
    e["GIT_COMMITTER_DATE"] = старая
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "давно", "--no-verify"],
                   capture_output=True, text=True, timeout=60, env=e)

    out = _прогнать(repo, home)
    assert "древняя" not in out, (
        f"записка девятимесячной давности попала в свежие — окно не работает:\n{out}")


@pytest.mark.host_only
def test_ловится_только_LATEST_а_не_любой_файл_нити(стенд):
    """Снимок нити — не записка. Иначе каждый the-end поднимал бы шум."""
    repo, home = стенд
    (repo / "docs" / "handoff" / "нить-со-снимком").mkdir(parents=True)
    (repo / "docs" / "handoff" / "нить-со-снимком" / "2026-09-13-abc1234.md").write_text(
        "снимок\n", encoding="utf-8")
    _git(repo, "add", "-A", home=home)
    _git(repo, "commit", "-qm", "снимок", "--no-verify", home=home)

    out = _прогнать(repo, home)
    assert "нить-со-снимком" not in out, (
        f"обычный снимок принят за записку — фильтр по LATEST.md не работает:\n{out}")


# ── вызов из хука: дом один, но позвать его обязан каждый ────────────────────

def _исполняемые(src: str) -> list[str]:
    return [ln.strip() for ln in src.splitlines() if not ln.lstrip().startswith("#")]


def test_доставку_зовёт_post_commit_а_не_pre_commit():
    """Вынос тела в отдельный файл создал НОВЫЙ тихий отказ: файл жив, а никто
    не зовёт — или зовут по пути, которого нет. Fail-open сделан намеренно
    (записка не должна ронять коммит), и именно поэтому промах пути был бы
    неотличим от «записок нет». Этот тест и есть тот датчик.

    ОБЕ ПОЛОВИНЫ ОБЯЗАТЕЛЬНЫ. Что post-commit зовёт — это доставка работает.
    Что pre-commit НЕ зовёт — это регрессия 14.09 не может вернуться тихо:
    там вызов означал бы отметку «прочитано» на попытке коммита, а не на
    состоявшемся.
    """
    пост = _исполняемые(ПОСТ.read_text(encoding="utf-8"))
    вызовы = [ln for ln in пост if "handoff_notes_notice.sh" in ln]
    assert вызовы, (
        "post-commit перестал звать доставку записок — канал между сессиями "
        "вернулся к вниманию читателя")
    assert not any("--commit" in ln for ln in вызовы), (
        "post-commit зовёт доставку с режимом `--commit`, которого больше нет: "
        "скрипт примет это за путь и промолчит")

    пре = _исполняемые(ХУК.read_text(encoding="utf-8"))
    assert not [ln for ln in пре if "handoff_notes_notice.sh" in ln], (
        "доставка вернулась в pre-commit: отменённый коммит снова будет гасить "
        "записку соседа навсегда")

    assert ДОСТАВКА.is_file() and os.access(ДОСТАВКА, os.X_OK), (
        f"хук зовёт {ДОСТАВКА}, но файла нет или он не исполняемый — доставка "
        "молчит ровно так же, как при отсутствии записок")


@pytest.mark.host_only
def test_отсутствие_файла_не_роняет_коммит(стенд, tmp_path):
    """Fail-open проверен исполнением, а не обещанием комментария."""
    repo, home = стенд
    вырезка = "\n".join(
        ln for ln in ПОСТ.read_text(encoding="utf-8").splitlines()
        if "handoff_notes_notice.sh" in ln and not ln.lstrip().startswith("#"))
    assert вырезка.strip(), (
        "в post-commit нет строки вызова доставки — тест прошёл бы вхолостую, "
        "не проверив ничего (этот класс уже стрелял здесь: О-14, M3)")
    скрипт = tmp_path / "call.sh"
    # SCRIPT_DIR в пустоту: доставки по этому пути нет.
    пусто = tmp_path / "пусто"; пусто.mkdir()
    скрипт.write_text(
        f'#!/bin/bash\nset -uo pipefail\nSCRIPT_DIR="{пусто}"\n' + вырезка + "\n",
        encoding="utf-8")
    r = subprocess.run(["bash", str(скрипт)], cwd=str(repo), capture_output=True,
                       text=True, timeout=60, env=_env(home))
    assert r.returncode == 0, (
        f"пропавшая доставка уронила бы коммит — это блок из-за чужой работы "
        f"(§13):\n{r.stderr}")


# ── пересборка 14.09 по внешнему ревью: все реплики + дельта ────────────────

@pytest.mark.host_only
def test_записка_на_ветке_соседнего_дерева_доходит(стенд):
    """F2 внешнего ревью: доставка была слепа РОВНО к тому случаю, ради которого
    заведена.

    `git log` без ревизий обходит HEAD, а в дереве нити HEAD — ветка нити.
    Значит записка, закоммиченная соседом на его ветке, не доходила ни до кого:
    чем строже соблюдают §21, тем меньше работала доставка. Две половины
    подсистемы гасили друг друга.
    """
    repo, home = стенд
    _git(repo, "worktree", "add", "-q", "-b", "thread/сосед",
         str(repo.parent / "wt-сосед"), home=home)
    сосед = repo.parent / "wt-сосед"
    (сосед / "docs" / "handoff" / "нить-соседа").mkdir(parents=True)
    (сосед / "docs" / "handoff" / "нить-соседа" / "LATEST.md").write_text(
        "# записка с ветки\n", encoding="utf-8")
    _git(сосед, "add", "-A", home=home)
    _git(сосед, "commit", "-qm", "записка соседа", "--no-verify", home=home)

    out = _прогнать(repo, home)
    assert "нить-соседа" in out, (
        f"записка с ветки соседнего дерева не доставлена — доставка читает "
        f"только свою реплику:\n{out}")


@pytest.mark.host_only
def test_прочитанное_не_повторяется_а_новое_печатается(стенд):
    """Дельта вместо состояния.

    Замер 13-14.09: список печатался 51 и 43 раза в сутки, а менялся 12 и 12 —
    три четверти печатей без единой новости. Датчик, который говорит одно и то
    же, перестают читать, и следующая настоящая записка тонет.
    """
    repo, home = стенд
    нить = repo / "docs" / "handoff" / "повтор"
    нить.mkdir(parents=True)
    (нить / "LATEST.md").write_text("# первая\n", encoding="utf-8")
    _git(repo, "add", "-A", home=home)
    _git(repo, "commit", "-qm", "записка", "--no-verify", home=home)

    первый = _прогнать(repo, home)
    assert "повтор" in первый, f"первая печать обязана состояться:\n{первый}"

    второй = _прогнать(repo, home)
    assert "повтор" not in второй, (
        f"уже показанная записка напечатана снова — это фон, а не датчик:\n{второй}")

    # Новая правка ТОЙ ЖЕ записки — снова новость.
    (нить / "LATEST.md").write_text("# первая\n\n# вторая\n", encoding="utf-8")
    _git(repo, "add", "-A", home=home)
    _git(repo, "commit", "-qm", "дописал", "--no-verify", home=home)
    третий = _прогнать(repo, home)
    assert "повтор" in третий, (
        f"дописанная записка не показана — ключ новизны не учитывает коммит:\n{третий}")


# ── отметка «прочитано» принадлежит СОСТОЯВШЕМУСЯ коммиту (F4, регрессия 14.09) ──
#
# 14.09 печать и отметка стояли в pre-commit, то есть на ПОПЫТКЕ коммита.
# Отменённый коммит гасил записку соседа навсегда. Первая починка развела печать
# и зачёт по двум хукам через файл `.pending` — и создала вторую дыру: тест
# `--no-verify` ниже показал, что этот хук пропускает pre-commit, но НЕ
# post-commit, поэтому `.pending` отменённой попытки засчитывался коммитом,
# который записку не печатал.
#
# Итог: одно событие, один хук. Здесь судится ИМЕННО это — живыми коммитами всех
# форм, потому что разница между формами и есть предмет.


def _стенд_с_хуками(tmp: Path, home: Path):
    """Одноразовый репозиторий с НАСТОЯЩИМ post-commit.

    pre-commit нарочно ПУСТОЙ, но существует и исполняем: так проверяется, что
    доставка не зависит от него, и что мутация «вернуть вызов в pre-commit»
    видна отдельным тестом, а не маскируется отсутствием файла.
    """
    repo = tmp / "repo"; repo.mkdir()
    _git(repo, "init", "-q", home=home)
    _git(repo, "config", "user.email", "t@t", home=home)
    _git(repo, "config", "user.name", "t", home=home)
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-commit").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    (hooks / "post-commit").write_text(
        f'#!/bin/bash\nexec "{ДОСТАВКА}"\n', encoding="utf-8")
    for h in ("pre-commit", "post-commit"):
        (hooks / h).chmod(0o755)
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A", home=home)
    _git(repo, "commit", "-qm", "base", "--no-verify", home=home)
    return repo


def _записка(repo: Path, home: Path, имя: str) -> None:
    """Записка СОСЕДА: кладём её коммитом БЕЗ хуков.

    Иначе post-commit отработал бы на этом же коммите и честно пометил записку
    прочитанной — так и ведёт себя настоящий автор в СВОЁМ дереве (у каждого
    рабочего дерева свой git-dir, а значит своя отметка). Здесь нужен сосед,
    поэтому хуки на этом коммите отключены целиком: `--no-verify` не годится,
    он гасит только pre-commit.
    """
    нить = repo / "docs" / "handoff" / имя
    нить.mkdir(parents=True)
    (нить / "LATEST.md").write_text(f"# записка {имя}\n", encoding="utf-8")
    _git(repo, "add", "-A", home=home)
    пусто = repo.parent / "без-хуков"
    пусто.mkdir(exist_ok=True)
    r = _git(repo, "-c", f"core.hooksPath={пусто}", "commit", "-qm",
             f"записка {имя}", home=home)
    assert r.returncode == 0, r.stderr


def _коммит(repo: Path, home: Path, файл: str, сообщение: str, *флаги: str) -> str:
    (repo / файл).write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A", home=home)
    r = _git(repo, "commit", "-m", сообщение, *флаги, home=home)
    assert r.returncode == 0, f"коммит не состоялся: {r.stderr}"
    return r.stdout + r.stderr


@pytest.mark.host_only
def test_отменённый_коммит_не_гасит_записку(tmp_path):
    """РЕГРЕССИЯ 14.09, найденная внешним ревью.

    Обычный `git commit` без -m, выход из редактора без сообщения — коммит
    отменён. Пока отметка писалась в pre-commit, записка соседа после этого не
    показывалась НИКОГДА. Теперь отменённая попытка до доставки не доходит:
    post-commit при отмене не зовётся вовсе.
    """
    home = tmp_path / "home"; home.mkdir()
    repo = _стенд_с_хуками(tmp_path, home)
    _записка(repo, home, "сосед")

    (repo / "своё.txt").write_text("работа\n", encoding="utf-8")
    _git(repo, "add", "-A", home=home)
    e = _env(home); e["GIT_EDITOR"] = "true"   # редактор закрыт без сообщения
    r = subprocess.run(["git", "-C", str(repo), "commit"], capture_output=True,
                       text=True, timeout=60, env=e)
    assert r.returncode != 0, "коммит с пустым сообщением обязан отмениться"

    вывод = _коммит(repo, home, "b.txt", "теперь по-настоящему")
    assert "сосед" in вывод, (
        "записка ПРОПАЛА: отменённая попытка засчитала её прочитанной")


@pytest.mark.host_only
def test_состоявшийся_коммит_гасит_записку_ровно_один_раз(tmp_path):
    """Обратная сторона: починка не должна вернуть вчерашний шум."""
    home = tmp_path / "home"; home.mkdir()
    repo = _стенд_с_хуками(tmp_path, home)
    _записка(repo, home, "сосед2")

    первый = _коммит(repo, home, "a.txt", "первый")
    assert "сосед2" in первый, "первая печать не состоялась"
    второй = _коммит(repo, home, "b.txt", "второй")
    assert "сосед2" not in второй, "записка напечатана повторно — дельта сломана"


@pytest.mark.host_only
def test_no_verify_не_глотает_записку(tmp_path):
    """ДЫРА ПЕРВОЙ ПОЧИНКИ, найденная этим оракулом 15.09.

    `--no-verify` пропускает pre-commit, но НЕ post-commit. Пока печать жила в
    pre-commit, а зачёт в post-commit, такой коммит засчитывал записку, ни разу
    её не показав. Здесь оба действия в одном хуке, поэтому `--no-verify`
    ПЕЧАТАЕТ — и только потому имеет право засчитать.
    """
    home = tmp_path / "home"; home.mkdir()
    repo = _стенд_с_хуками(tmp_path, home)
    _записка(repo, home, "сосед_nv")

    мимо = _коммит(repo, home, "a.txt", "мимо pre-commit", "--no-verify")
    assert "сосед_nv" in мимо, (
        "коммит с --no-verify не показал записку, хотя post-commit отработал: "
        "она засчитана прочитанной, не будучи показанной")
    следующий = _коммит(repo, home, "b.txt", "следующий")
    assert "сосед_nv" not in следующий, "после показа записка обязана затихнуть"


@pytest.mark.host_only
def test_amend_не_теряет_и_не_повторяет_записку(tmp_path):
    """`--amend` — тоже состоявшийся коммит, и post-commit на нём срабатывает.

    Форма в оракуле по риску Р-2 плана: разделение судилось только на обычном
    коммите.
    """
    home = tmp_path / "home"; home.mkdir()
    repo = _стенд_с_хуками(tmp_path, home)
    _записка(repo, home, "сосед_amend")

    первый = _коммит(repo, home, "a.txt", "первый")
    assert "сосед_amend" in первый

    правка = _git(repo, "commit", "--amend", "-m", "первый, точнее", home=home)
    assert правка.returncode == 0, правка.stderr
    assert "сосед_amend" not in (правка.stdout + правка.stderr), (
        "amend напечатал прочитанную записку заново — дельта не переживает amend")
    после = _коммит(repo, home, "b.txt", "второй")
    assert "сосед_amend" not in после, "amend сбросил отметку: записка вернулась шумом"


@pytest.mark.host_only
def test_merge_не_теряет_записку(tmp_path):
    """Слияние post-commit НЕ зовёт (замерено, git 2.54).

    Значит записка на слиянии не показывается — и, ровно поэтому, не
    засчитывается: доживает до следующего обычного коммита. Тест сторожит
    именно эту пару, а не merge вообще: если доставку когда-нибудь повесят на
    `post-merge`, показ без зачёта (или зачёт без показа) покраснеет здесь.
    """
    home = tmp_path / "home"; home.mkdir()
    repo = _стенд_с_хуками(tmp_path, home)
    _записка(repo, home, "сосед_merge")

    # Ветка в сторону — обстановка, а не предмет: коммитим без хуков, иначе
    # доставка отработала бы здесь и записка была бы честно показана до merge.
    пусто = repo.parent / "без-хуков"; пусто.mkdir(exist_ok=True)
    _git(repo, "checkout", "-qb", "вбок", home=home)
    (repo / "вбок.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A", home=home)
    _git(repo, "-c", f"core.hooksPath={пусто}", "commit", "-qm", "вбок", home=home)
    _git(repo, "checkout", "-q", "-", home=home)

    слияние = _git(repo, "merge", "--no-ff", "-m", "слияние", "вбок", home=home)
    assert слияние.returncode == 0, слияние.stderr

    следующий = _коммит(repo, home, "b.txt", "следующий")
    assert "сосед_merge" in следующий, (
        "слияние засчитало прочитанной записку, которую не печатало")


@pytest.mark.host_only
def test_отметка_пишется_подменой_файла_а_не_перезаписью(tmp_path):
    """Отметка пишется во временный файл и подменяется через `mv`.

    ЗАЧЕМ ЭТО НЕ КОСМЕТИКА. Прямой `> "$_SEEN"` СНАЧАЛА обрезает файл, и только
    потом пишет: обрыв посреди оставил бы часть записок «непрочитанными»
    навсегда — то есть вернул бы ровно тот шум, ради устранения которого дельта
    и сделана.

    ЧЕМ ЭТО СУДИТСЯ. Оборвать запись в тесте нечем, поэтому судим по РАЗЛИЧИМОМУ
    следствию того же выбора: `rename` требует прав на КАТАЛОГ, а `>` — на сам
    файл. Делаем файл отметки нечитаемым для записи при записываемом каталоге:
    подмена проходит, перезапись — нет. Мутация «вернуть прямой `>`» краснеет
    здесь, и это единственная форма, в которой у меня вообще есть оракул на этот
    выбор.

    ГРАНИЦА ЧЕСТНО: настоящий обрыв питания этим не проверен и проверен не
    будет. Тест доказывает СПОСОБ записи, а не устойчивость к обрыву.
    """
    home = tmp_path / "home"; home.mkdir()
    repo = _стенд_с_хуками(tmp_path, home)
    _записка(repo, home, "сосед_atomic")
    первый = _коммит(repo, home, "a.txt", "первый")
    assert "сосед_atomic" in первый

    отметка = repo / ".git" / "handoff_seen"
    assert отметка.is_file(), "отметка не записана — дельта не работает вовсе"
    строки = [s for s in отметка.read_text(encoding="utf-8").splitlines() if s]
    assert строки and all(len(s.split()) == 2 for s in строки), (
        f"отметка записана обрезанной: {строки!r}")
    assert not list((repo / ".git").glob("handoff_seen.tmp*")), (
        "временный файл отметки не убран — каталог git засоряется на каждом коммите")
    assert not (repo / ".git" / "handoff_seen.pending").exists(), (
        "остался .pending из двухфазной схемы, которой больше нет")

    # файл отметки запрещён к записи, каталог — нет
    отметка.chmod(0o444)
    _записка(repo, home, "сосед_atomic2")
    новый = _коммит(repo, home, "b.txt", "второй")
    assert "сосед_atomic2" in новый, "вторая записка не показана"
    assert "не могу записать" not in новый, (
        "отметка пишется прямой перезаписью: на защищённом от записи файле она "
        "отваливается, хотя подмена через mv прошла бы")

    третий = _коммит(repo, home, "c.txt", "третий")
    assert "сосед_atomic2" not in третий, (
        "отметка не обновилась — записка вернулась шумом")


@pytest.mark.host_only
def test_нечитаемый_для_записи_git_dir_называется_вслух(tmp_path):
    """F8 внешнего ревью: молча выключенная дельта возвращает вчерашний шум.

    Если каталог git недоступен для записи, отметку положить некуда. Тихий
    отказ здесь ХУЖЕ громкого: доставка продолжает печатать ВЕСЬ список на
    каждом коммите — ровно те 51 и 43 печати в сутки при 12 изменениях, ради
    устранения которых дельта и сделана, — и человек читает это как «сегодня
    много новостей», а не как «датчик сломан».

    Судим исполнением ДОСТАВКИ, а не коммита: сделать весь `.git` нечитаемым
    для записи нельзя — git не создаст `index.lock` и коммита просто не будет,
    то есть тест судил бы поломку git, а не поведение доставки. Поэтому здесь
    скрипт зовётся напрямую, ровно в том состоянии, в каком его застал бы
    post-commit: читать git-dir можно, писать — нет.
    """
    home = tmp_path / "home"; home.mkdir()
    repo = _стенд_с_хуками(tmp_path, home)
    _записка(repo, home, "сосед_f8")

    гитдир = repo / ".git"
    отметка = гитдир / "handoff_seen"
    # Базовый коммит стенда уже прошёл через post-commit (`--no-verify` его НЕ
    # пропускает), поэтому отметка тут есть. Предмет теста — что она НЕ
    # ОБНОВИЛАСЬ и об этом сказано, а не что её нет.
    было = отметка.read_text(encoding="utf-8") if отметка.exists() else None
    режим = гитдир.stat().st_mode
    гитдир.chmod(0o555)          # каталог только на чтение
    try:
        r = subprocess.run(["bash", str(ДОСТАВКА)], cwd=str(repo),
                           capture_output=True, text=True, timeout=60,
                           env=_env(home))
    finally:
        гитдир.chmod(режим)
    вывод = r.stdout + r.stderr

    assert r.returncode == 0, (
        f"доставка упала при нечитаемом для записи git-dir — она обязана быть "
        f"WARN, а не блоком (§13):\n{вывод}")
    assert "сосед_f8" in вывод, f"записка не напечатана вовсе:\n{вывод}"
    assert "не могу записать" in вывод, (
        f"отметку записать не удалось, и об этом промолчали — дельта выключена "
        f"молча, а человек увидит только растущий список:\n{вывод}")
    стало = отметка.read_text(encoding="utf-8") if отметка.exists() else None
    assert стало == было, (
        "обстановка не воспроизводится: отметка всё-таки обновилась, "
        "значит запись не отказала и тест судил не тот случай")
    assert "сосед_f8" not in (стало or ""), (
        "записка попала в отметку при неудачной записи — невозможно")
    assert not list(гитдир.glob("handoff_seen.tmp*")), (
        "временный файл остался после неудачной записи — мусор в git-dir")


# ── inbox: у LATEST остаётся ОДИН писатель (16.09) ───────────────────────────
#
# Замер 16.09 на 30-дневном окне: 21 закрытие нити, 5 с настоящим конфликтом, и
# в двух из пяти конфликтовал чужой LATEST.md — автор нити переписывал свой
# снимок, сосед в тот же файл клал записку (30 и 45 строк ручного разбора).
# Это write-write конфликт; он исчезает единственным владельцем файла, а не
# разрешателем. Записка соседа переехала в docs/handoff/<нить>/inbox/<кто>.md.
# Тесты ниже стерегут ДВА утверждения сразу: новый адрес доставляется, и ничто
# в нём не теряется молча.


def _записка_в_inbox(repo: Path, home: Path, нить: str, файл: str, текст: str) -> None:
    путь = repo / "docs" / "handoff" / нить / "inbox" / файл
    путь.parent.mkdir(parents=True, exist_ok=True)
    путь.write_text(текст, encoding="utf-8")
    _git(repo, "add", "-A", home=home)
    _git(repo, "commit", "-qm", f"записка в {нить}", "--no-verify", home=home)


@pytest.mark.host_only
def test_записка_из_inbox_названа_вместе_с_отправителем(стенд):
    """Мало сказать «в нить пришла записка» — надо сказать, КТО написал.

    Без имени отправителя адресат не знает, в чей inbox отвечать, и ответит
    единственным известным способом — в чужой LATEST, то есть ровно тем
    действием, против которого весь переезд.
    """
    repo, home = стенд
    _записка_в_inbox(repo, home, "нить-адресат", "agent-coordination.md",
                     "беру scripts/git-hooks/*, отдаю docs/how-to/thread_worktree.md\n")

    out = _прогнать(repo, home)
    assert "нить-адресат" in out, f"записка из inbox не доставлена вовсе:\n{out}"
    assert "agent-coordination" in out, (
        f"отправитель не назван — отвечать некуда:\n{out}")


@pytest.mark.host_only
def test_записка_в_inbox_не_теряется_из_за_имени_или_вложенности(стенд):
    """Фильтр не должен молча съедать записку с «неудобным» путём.

    Первая редакция фильтра требовала `inbox/<одно имя>.md`. Тогда записка,
    положенная в подкаталог или без расширения, не доставлялась НИКОМУ, а
    молчание канала неотличимо от «новостей нет» — тот же класс, что стоил
    13.09 модуля на 167 строк, написанного дважды.
    """
    repo, home = стенд
    for нить, файл in (("н1", "сосед с пробелом.md"),
                       ("н2", "вложенная/записка.md"),
                       ("н3", "без-расширения")):
        _записка_в_inbox(repo, home, нить, файл, "перечень файлов\n")

    out = _прогнать(repo, home)
    for нить in ("н1", "н2", "н3"):
        assert нить in out, (
            f"записка нити {нить} потеряна фильтром — канал тише, чем был:\n{out}")


@pytest.mark.host_only
def test_прочитанная_inbox_записка_не_повторяется_а_новая_печатается(стенд):
    """Дельта обязана работать и для нового адреса, иначе inbox станет фоном."""
    repo, home = стенд
    _записка_в_inbox(repo, home, "нить-адресат", "первый.md", "файлы A\n")
    первый = _прогнать(repo, home)
    assert "первый" in первый, f"первая записка не доставлена:\n{первый}"

    молчание = _прогнать(repo, home)
    assert молчание.strip() == "", (
        f"прочитанная записка печатается снова — дельта не работает:\n{молчание}")

    _записка_в_inbox(repo, home, "нить-адресат", "второй.md", "файлы B\n")
    второй = _прогнать(repo, home)
    assert "второй" in второй, f"вторая записка не доставлена:\n{второй}"
    assert "первый" not in второй, (
        f"вместе с новой поднялась прочитанная:\n{второй}")
