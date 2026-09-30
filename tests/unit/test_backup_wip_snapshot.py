"""Оракул: у состояния «на MacBook всё закоммичено» есть ПИСАТЕЛЬ.

ЗАМЕР, РАДИ КОТОРОГО ЭТО ЕСТЬ (13-14.09.2026). `backup.sh` снимал дерево только
когда оно грязное; на чистом писал в лог «no changes» и НЕ двигал
`refs/backups/wip`. Датчик `integrity_tests.check_macbook_uncommitted` судит
именно этот ref, поэтому уборка дерева — ровно то событие, которое датчик
погасить не может. Живьём: снимок замер 13.09 в 21:20, три файла закоммичены в
21:26, и ночной прогон 14.09 назвал их незакоммиченными. Погасила бы его только
новая грязь.

Второе следствие тяжелее первого. На ИСПРАВНОЙ машине, где всё закоммичено, ref
перестаёт свежеть, и датчик уходит в ветку «снимок устарел» — про которую он сам
говорит, что два чтения оттуда неразличимы: «ноутбук выключен» ИЛИ «джоба снимка
мертва». То есть здоровая чистая машина выглядела бы как умерший бэкап, и
настоящая смерть джобы утонула бы в этом же сигнале.

ЧТО ПРОВЕРЯЕТСЯ — НАСТОЯЩЕЕ РЕШЕНИЕ ИЗ НАСТОЯЩЕГО ФАЙЛА. Условие извлекается из
`backup.sh` по маркерам и ИСПОЛНЯЕТСЯ в одноразовом репозитории. Поэтому оракул
краснеет и когда правку откатили, и когда она осталась строкой, но перестала
работать — пересказ условия в тесте пережил бы обе мутации.

ГРАНИЦА ЧЕСТНО. Здесь судится ТОЛЬКО развилка «снимать или нет». Что снимок
доехал до Studio (`git push -f`), этим оракулом не доказывается: у него нет
второй машины. Доставку стережёт сам датчик — устаревший ref на Studio он
называет вслух.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.host_only]

ROOT = Path(__file__).resolve().parents[2]
СКРИПТ = ROOT / "backup.sh"
НАЧАЛО = "# ── РЕШЕНИЕ «СНИМАТЬ ЛИ»: начало блока"
КОНЕЦ = "# ── РЕШЕНИЕ «СНИМАТЬ ЛИ»: конец блока"


def _условие() -> str:
    src = СКРИПТ.read_text(encoding="utf-8")
    assert НАЧАЛО in src and КОНЕЦ in src, (
        "в backup.sh нет блока решения «снимать ли» — либо его убрали, либо "
        "переименовали маркеры; в обоих случаях развилка перестала быть судимой")
    тело = src.split(НАЧАЛО, 1)[1].split(КОНЕЦ, 1)[0]
    строки = [ln for ln in тело.split("\n", 1)[1].splitlines()
              if ln.strip().startswith("if ")]
    assert len(строки) == 1, f"ожидалась одна строка `if`, нашлось {len(строки)}"
    # `if …; then` → голое условие для проверки в подоболочке.
    return строки[0].strip().removeprefix("if ").rstrip().removesuffix("; then")


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


def _git(repo: Path, *args: str, home: Path):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, timeout=60, env=_env(home))


@pytest.fixture()
def стенд():
    tmp = Path(tempfile.mkdtemp(prefix="backup_wip_"))
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


def _снимать(repo: Path, home: Path, kind: str) -> bool:
    """Исполнить НАСТОЯЩЕЕ условие из backup.sh. True = снимок будет сделан."""
    скрипт = repo.parent / "decide.sh"
    скрипт.write_text(
        "#!/bin/bash\nset -uo pipefail\nKIND=\"$1\"\n"
        f"if {_условие()}; then exit 0; else exit 1; fi\n", encoding="utf-8")
    r = subprocess.run(["bash", str(скрипт), kind], cwd=str(repo),
                       capture_output=True, text=True, timeout=60, env=_env(home))
    assert r.returncode in (0, 1), f"условие не отработало: {r.stderr}"
    return r.returncode == 0


def test_wip_снимает_чистое_дерево(стенд):
    """Главное утверждение: у «чисто» есть писатель.

    Без этого датчик незакоммиченного не может отличить исправную прибранную
    машину от мёртвой джобы снимка — и обе выглядят одинаково тревожно.
    """
    repo, home = стенд
    assert _снимать(repo, home, "wip"), (
        "wip не снимает чистое дерево — состояние «всё закоммичено» снова "
        "некому записать, и ref замрёт на последней грязи")


def test_wip_снимает_и_грязное_дерево(стенд):
    """Правка не должна была отобрать исходное поведение."""
    repo, home = стенд
    (repo / "f.txt").write_text("изменено\n", encoding="utf-8")
    assert _снимать(repo, home, "wip")


def test_daily_на_чистом_дереве_не_снимает(стенд):
    """История не растёт снимками, равными HEAD.

    Граница правки названа явно: чинился ДАТЧИК, а не бэкап. Суточный ref —
    история, и запись, не несущая изменений, засоряет ротацию 14 снимков,
    вытесняя настоящие.
    """
    repo, home = стенд
    assert not _снимать(repo, home, "daily"), (
        "daily начал снимать чистое дерево — ротация 14 суточных снимков "
        "заполнится копиями HEAD и вытеснит настоящую историю")


@pytest.mark.parametrize("грязь", ["изменённый", "staged", "untracked"])
def test_daily_снимает_любую_грязь(стенд, грязь):
    """Три вида грязи, которые видит git, — все три обязаны поднимать снимок.

    Untracked здесь не для полноты: именно новый, ещё не добавленный файл —
    самая частая форма несохранённой работы, и именно он не виден `git diff`.
    """
    repo, home = стенд
    if грязь == "изменённый":
        (repo / "f.txt").write_text("изменено\n", encoding="utf-8")
    elif грязь == "staged":
        (repo / "f.txt").write_text("изменено\n", encoding="utf-8")
        _git(repo, "add", "-A", home=home)
    else:
        (repo / "новый.txt").write_text("работа\n", encoding="utf-8")
    assert _снимать(repo, home, "daily"), f"{грязь}: снимок не поднялся"


# ── отказ обязан быть слышен (внешнее ревью 14.09) ───────────────────────────
# Замер ревьюера: `cp .git/index` стоит вне цепочки `&&`, а `set -e` убивает
# скрипт раньше его СОБСТВЕННОЙ ветки «FAILED — manual recovery нужен». В логе
# оставалось «started» и больше ничего, а сверху ложился датчик, который про
# застывший ref говорит «ноутбук выключен ИЛИ джоба мертва» — отказ был
# замаскирован дважды. С 14.09 участок бежит 8 раз в сутки безусловно.

def _прогнать_скрипт(домик: Path, репо: Path, режим: str):
    """Настоящий backup.sh, но с подменённым $HOME и обезвреженным push."""
    src = СКРИПТ.read_text(encoding="utf-8")
    src = src.replace('cd $HOME/health_scripts', f'cd "{репо}"')
    src = src.replace('git push -f studio "$SNAP_REF"', 'true')
    копия = домик / "backup.sh"
    копия.write_text(src, encoding="utf-8")
    e = _env(домик)
    e["HOME"] = str(домик)
    (домик / "health_scripts" / "logs").mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["bash", str(копия), режим], capture_output=True,
                       text=True, timeout=60, env=e, cwd=str(репо))
    лог = домик / "health_scripts" / "logs" / "backup.log"
    return r, (лог.read_text(encoding="utf-8") if лог.exists() else "")


def test_обрыв_скрипта_попадает_в_лог(стенд, tmp_path):
    """Пустой репозиторий без HEAD: `cp .git/index` падает.

    Утверждение не «скрипт не падает», а «падение СЛЫШНО». Молчащий отказ здесь
    неотличим от выключенного ноутбука — это и делает его опасным.
    """
    _, home = стенд
    пусто = tmp_path / "пусто"
    пусто.mkdir()
    subprocess.run(["git", "-C", str(пусто), "init", "-q"], env=_env(home),
                   capture_output=True, timeout=60)
    (пусто / "работа.txt").write_text("не потеряй\n", encoding="utf-8")
    r, лог = _прогнать_скрипт(tmp_path, пусто, "--wip")
    assert r.returncode != 0, "ожидался отказ на репозитории без HEAD"
    assert "ОБОРВАН" in лог, (
        f"отказ не попал в лог — он неотличим от выключенного ноутбука:\n{лог}")


def test_опечатка_в_режиме_попадает_в_лог(стенд, tmp_path):
    """Опечатка в plist не должна давать молчащую джобу без следа."""
    repo, home = стенд
    r, лог = _прогнать_скрипт(tmp_path, repo, "--WIP")
    assert r.returncode == 2, f"ожидался usage, rc={r.returncode}"
    assert "неизвестный режим" in лог, f"режим-опечатка не назван в логе:\n{лог}"


def test_снимок_несёт_число_непокрытых_деревьев(стенд, tmp_path):
    """Факт «сколько реплик я не видел» обязан ехать В СНИМКЕ.

    Датчик незакоммиченного живёт на Studio и деревья нитей MacBook перечислить
    не может — если снимок это число не несёт, датчик снова начнёт молчать
    «всё хорошо» там, где честный ответ «про эти деревья не знаю».
    """
    repo, home = стенд
    _git(repo, "worktree", "add", "-q", "-b", "thread/t", str(tmp_path / "wt"), home=home)
    r, лог = _прогнать_скрипт(tmp_path, repo, "--wip")
    assert r.returncode == 0, f"снимок не сделан:\n{r.stderr}\n{лог}"
    msg = _git(repo, "log", "-1", "--format=%B", "refs/backups/wip", home=home).stdout
    assert "worktrees_uncovered=1" in msg, (
        f"снимок не назвал непокрытые деревья — датчик о них не узнает:\n{msg}")


# ── КОПИЯ ВЕТОК НИТЕЙ (16.09, BL-WIP-WORKTREES-1) ────────────────────────────
#
# Замер 16.09: post-commit пушит только main, backup.sh снимал только главную копию,
# значит закоммиченная работа нити жила на ОДНОМ диске — 992 строки двух нитей в
# момент замера. Тесты ниже исполняют НАСТОЯЩИЙ backup.sh против НАСТОЯЩЕГО
# удалённого репозитория (голый репо в tmp), поэтому судится доставка, а не текст
# скрипта: пересказ условия в тесте пережил бы и откат правки, и её поломку.


def _прогнать_с_удалённым(домик: Path, репо: Path, режим: str, remote: Path):
    """Настоящий backup.sh с настоящим удалённым репозиторием по имени studio."""
    src = СКРИПТ.read_text(encoding="utf-8")
    src = src.replace('cd $HOME/health_scripts', f'cd "{репо}"')
    копия = домик / "backup_remote.sh"
    копия.write_text(src, encoding="utf-8")
    e = _env(домик)
    e["HOME"] = str(домик)
    (домик / "health_scripts" / "logs").mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["bash", str(копия), режим], capture_output=True,
                       text=True, timeout=120, env=e, cwd=str(репо))
    лог = домик / "health_scripts" / "logs" / "backup.log"
    return r, (лог.read_text(encoding="utf-8") if лог.exists() else "")


def _голый(tmp_path: Path, репо: Path, home: Path) -> Path:
    remote = tmp_path / "studio.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)],
                   env=_env(home), capture_output=True, timeout=60)
    _git(репо, "remote", "add", "studio", str(remote), home=home)
    return remote


def _рефы(remote: Path, home: Path) -> str:
    return _git(remote, "for-each-ref", "--format=%(refname)", home=home).stdout


def test_ветка_нити_уезжает_копией_на_studio(стенд, tmp_path):
    """Главное утверждение волны: работа нити перестала быть одноэкземплярной.

    У ветки ОБЯЗАН быть свой коммит, и это не деталь стенда. Первая редакция теста
    заводила пустую ветку (`git branch` от main) — её tip достижим из main, поэтому
    чистка по достижимости честно удаляла копию в том же прогоне, и тест краснел на
    правильном поведении. Это и есть граница механизма: копия существует ровно для
    той работы, которой ещё нет в main.
    """
    repo, home = стенд
    remote = _голый(tmp_path, repo, home)
    _git(repo, "checkout", "-q", "-b", "thread/самая-нить", home=home)
    (repo / "нитевая.txt").write_text("работа нити\n", encoding="utf-8")
    _git(repo, "add", "-A", home=home)
    _git(repo, "commit", "-qm", "работа нити", "--no-verify", home=home)
    _git(repo, "checkout", "-q", "main", home=home)

    r, лог = _прогнать_с_удалённым(tmp_path, repo, "--wip", remote)
    assert r.returncode == 0, f"скрипт не отработал:\n{r.stderr}\n{лог}"

    рефы = _рефы(remote, home)
    assert "refs/backups/thread/самая-нить" in рефы, (
        f"копия ветки нити не доехала — работа осталась на одном диске:\n{рефы}\n{лог}")
    msg = _git(repo, "log", "-1", "--format=%B", "refs/backups/wip", home=home).stdout
    assert "threads=самая-нить:" in msg, (
        f"снимок не назвал ветки нитей — датчик на Studio о них не узнает:\n{msg}")


def test_без_нитей_список_явно_пуст(стенд, tmp_path):
    """`threads=нет` и ОТСУТСТВИЕ поля — разные вещи.

    Отсутствие поля означает старый backup.sh, то есть «не знаю»; явное «нет»
    означает «нитей не было». Датчик обязан различать их, а для этого писатель
    обязан сказать «нет» вслух.
    """
    repo, home = стенд
    remote = _голый(tmp_path, repo, home)
    r, лог = _прогнать_с_удалённым(tmp_path, repo, "--wip", remote)
    assert r.returncode == 0, f"скрипт не отработал:\n{r.stderr}\n{лог}"
    msg = _git(repo, "log", "-1", "--format=%B", "refs/backups/wip", home=home).stdout
    assert "threads=нет" in msg, f"пустой список не назван явно:\n{msg}"


def test_копия_слитой_нити_убирается_а_неслитой_остаётся(стенд, tmp_path):
    """Чистка по ДОСТИЖИМОСТИ (решение владельца 16.09), а не по отсутствию ветки.

    `git push --prune` удалял бы копию ветки, удалённой локально, — а ветка исчезает
    и при слиянии (норма), и при потере дерева (авария). Здесь удаляется только то,
    что доказуемо доехало до main; неслитое остаётся, даже если ветки уже нет.
    """
    repo, home = стенд
    remote = _голый(tmp_path, repo, home)

    for имя, файл in (("слитая", "a.txt"), ("живая", "b.txt")):
        _git(repo, "checkout", "-q", "-b", f"thread/{имя}", home=home)
        (repo / файл).write_text("работа\n", encoding="utf-8")
        _git(repo, "add", "-A", home=home)
        _git(repo, "commit", "-qm", f"работа {имя}", "--no-verify", home=home)
        _git(repo, "checkout", "-q", "main", home=home)

    r, лог = _прогнать_с_удалённым(tmp_path, repo, "--wip", remote)
    assert r.returncode == 0, f"первый прогон не отработал:\n{r.stderr}\n{лог}"
    рефы = _рефы(remote, home)
    assert "refs/backups/thread/слитая" in рефы and "refs/backups/thread/живая" in рефы, (
        f"копии не доехали:\n{рефы}\n{лог}")

    # Одну нить сливаем и удаляем локальную ветку — ровно то, что делает thread_finish.
    _git(repo, "merge", "--no-ff", "--no-edit", "-q", "thread/слитая", home=home)
    _git(repo, "branch", "-D", "thread/слитая", home=home)
    # Вторую ТОЖЕ удаляем локально, НЕ слив: имитация потерянного дерева.
    _git(repo, "branch", "-D", "thread/живая", home=home)

    r2, лог2 = _прогнать_с_удалённым(tmp_path, repo, "--wip", remote)
    assert r2.returncode == 0, f"второй прогон не отработал:\n{r2.stderr}\n{лог2}"
    рефы2 = _рефы(remote, home)
    assert "refs/backups/thread/слитая" not in рефы2, (
        f"копия слитой нити осталась — список копий перестаёт быть списком риска:\n{рефы2}")
    assert "refs/backups/thread/живая" in рефы2, (
        f"УДАЛЕНА КОПИЯ НЕСЛИТОЙ РАБОТЫ — автоматика убрала последний экземпляр:\n{рефы2}\n{лог2}")


def test_снимок_не_коммитит_в_main_и_не_пушит_main(стенд, tmp_path):
    """Решение владельца 24.07, и до него скрипт нарушал именно это.

    Раньше `backup.sh` делал `git commit --no-verify` в main и пушил его: ночью
    деплоился непроверенный WIP, обходились ВСЕ гейты. Утверждение проверяется
    поведением, а не грепом: после прогона main на месте, а на удалённом
    репозитории ветки main нет вовсе.
    """
    repo, home = стенд
    remote = _голый(tmp_path, repo, home)
    (repo / "черновик.txt").write_text("незакоммиченное\n", encoding="utf-8")
    было = _git(repo, "rev-parse", "main", home=home).stdout.strip()

    r, лог = _прогнать_с_удалённым(tmp_path, repo, "--wip", remote)
    assert r.returncode == 0, f"скрипт не отработал:\n{r.stderr}\n{лог}"

    стало = _git(repo, "rev-parse", "main", home=home).stdout.strip()
    assert стало == было, "снимок сдвинул main — непроверенный WIP уехал бы в деплой"
    рефы = _рефы(remote, home)
    assert "refs/heads/main" not in рефы, (
        f"main уехал на удалённый репозиторий из бэкапа, мимо гейтов:\n{рефы}")
    assert "refs/backups/wip" in рефы, f"сам снимок при этом не доехал:\n{рефы}\n{лог}"
    assert (repo / "черновик.txt").exists(), "скрипт тронул рабочее дерево"


# ── ЗАКРЫТИЕ С ПЕРЕСТАНОВКОЙ (21.09) ────────────────────────────────────────
#
# С 21.09 thread_finish переставляет ветку перед слиянием. Если main ушёл вперёд,
# старый tip копии не становится предком main НИКОГДА, и копия закрытой нити висела
# бы вечно — живой пример в первый же день: close-rebase-once, 8ef630c. Два случая
# различаются и лечатся по-разному; третий тест стережёт, что лечение не задело
# главное свойство — неслитая работа не трогается.


def _коммит_в(repo: Path, home: Path, файл: str, текст: str, сообщение: str) -> None:
    (repo / файл).write_text(текст, encoding="utf-8")
    _git(repo, "add", "-A", home=home)
    _git(repo, "commit", "-qm", сообщение, "--no-verify", home=home)


def _нить_с_копией(tmp_path, repo, home, remote, slug, файл, текст):
    _git(repo, "checkout", "-q", "-b", f"thread/{slug}", home=home)
    _коммит_в(repo, home, файл, текст, f"работа {slug}")
    _git(repo, "checkout", "-q", "main", home=home)
    r, лог = _прогнать_с_удалённым(tmp_path, repo, "--wip", remote)
    assert r.returncode == 0, f"копия не снята:\n{r.stderr}\n{лог}"
    assert f"refs/backups/thread/{slug}" in _рефы(remote, home)


def test_перестановка_без_правок_убирает_копию(стенд, tmp_path):
    """Содержание доехало, хотя tip другой: копию удаляет `git cherry`, а не предок."""
    repo, home = стенд
    remote = _голый(tmp_path, repo, home)
    _нить_с_копией(tmp_path, repo, home, remote, "чистая", "нить.txt", "работа\n")

    _коммит_в(repo, home, "сосед.txt", "сосед\n", "main ушёл вперёд")
    _git(repo, "rebase", "-q", "main", "thread/чистая", home=home)
    _git(repo, "checkout", "-q", "main", home=home)
    _git(repo, "merge", "--no-ff", "--no-edit", "-q", "thread/чистая", home=home)
    _git(repo, "branch", "-D", "thread/чистая", home=home)

    r, лог = _прогнать_с_удалённым(tmp_path, repo, "--wip", remote)
    assert r.returncode == 0, f"прогон не отработал:\n{r.stderr}\n{лог}"
    рефы = _рефы(remote, home)
    assert "refs/backups/thread/чистая" not in рефы, (
        f"копия переставленной нити осталась, хотя содержание уже в main:\n{рефы}\n{лог}")
    assert "thread-closed/чистая" not in рефы, "доказанно доехавшее не надо хранить"


def test_перестановка_с_правкой_переносит_копию_а_не_удаляет(стенд, tmp_path):
    """Содержание при закрытии переписано: доказать «доехало» нечем — данные остаются.

    Ровно случай close-rebase-once: `git cherry` видит коммит копии как «+», потому что
    при закрытии его содержимое изменили. Удалять нельзя (решение владельца 16.09:
    удаляется только доказанное), держать в списке риска — ложь: нить закрыта.
    """
    repo, home = стенд
    remote = _голый(tmp_path, repo, home)
    _нить_с_копией(tmp_path, repo, home, remote, "правленая", "общий.txt", "версия нити\n")

    # При закрытии содержание переписали (как правка конфликта при перестановке).
    _git(repo, "checkout", "-q", "thread/правленая", home=home)
    _git(repo, "reset", "-q", "--soft", "HEAD~1", home=home)
    (repo / "общий.txt").write_text("версия после разбора конфликта\n", encoding="utf-8")
    _git(repo, "add", "-A", home=home)
    _git(repo, "commit", "-qm", "работа правленая (после разбора)", "--no-verify", home=home)
    _git(repo, "checkout", "-q", "main", home=home)
    _git(repo, "merge", "--no-ff", "--no-edit", "-q", "thread/правленая", home=home)
    _git(repo, "branch", "-D", "thread/правленая", home=home)

    r, лог = _прогнать_с_удалённым(tmp_path, repo, "--wip", remote)
    assert r.returncode == 0, f"прогон не отработал:\n{r.stderr}\n{лог}"
    рефы = _рефы(remote, home)
    assert "refs/backups/thread/правленая" not in рефы, (
        f"копия закрытой нити осталась в списке риска:\n{рефы}\n{лог}")
    assert "refs/backups/thread-closed/правленая" in рефы, (
        f"ДАННЫЕ ПОТЕРЯНЫ: копию с непроверенным содержанием удалили, а не перенесли:\n{рефы}\n{лог}")


def test_неслитая_потерянная_ветка_не_переносится_и_не_удаляется(стенд, tmp_path):
    """⭐ Главное свойство не задето: слияния нити в main НЕТ — копия стоит на месте.

    Третий исход срабатывает только при двух условиях сразу: ветки нет И в main есть
    слияние этой нити. Потерянное дерево без слияния — ровно тот случай, ради которого
    копия и существует.
    """
    repo, home = стенд
    remote = _голый(tmp_path, repo, home)
    _нить_с_копией(tmp_path, repo, home, remote, "потерянная", "п.txt", "работа\n")
    _git(repo, "branch", "-D", "thread/потерянная", home=home)

    r, лог = _прогнать_с_удалённым(tmp_path, repo, "--wip", remote)
    assert r.returncode == 0, f"прогон не отработал:\n{r.stderr}\n{лог}"
    рефы = _рефы(remote, home)
    assert "refs/backups/thread/потерянная" in рефы, (
        f"КОПИЯ ПОТЕРЯННОЙ РАБОТЫ исчезла из списка риска:\n{рефы}\n{лог}")
    assert "thread-closed/потерянная" not in рефы, "не слитая нить не может считаться закрытой"
