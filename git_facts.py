"""ФАКТЫ о git для окружений, где git есть не всегда (2026-08-10).

ЗАЧЕМ. `scripts/test_on_studio.sh` синхронизирует дерево в `~/health_staging`
БЕЗ `.git` — и это правильно: репозиторий там означал бы, что из песочницы можно
коммитить и пушить, а она для того и заведена, чтобы туда нельзя было.

Цена этой правильности до сегодня: всё, что спрашивало git, в песочнице врало.
Замер 2026-08-10 — три следствия одного корня, до сих пор выглядевшие как три
разные поломки:

  · пять doc-тестов падали с `git ls-files` rc=128 — «❌ FAIL, почини ДО коммита»
    на чистом дереве, то есть обучение игнорировать собственный гейт;
  · снимок pass-set писал `head_sha: "unknown"` — штамп кода в артефакте терялся;
  · `probe_quarantine_exit` читала снимок, у которого нет ни sha, ни семьи A.

РЕШЕНИЕ: песочница получает ФАКТЫ о git, а не git. Синхронизация кладёт рядом
с деревом манифест — список отслеживаемых файлов и HEAD sha, снятые в НАСТОЯЩЕМ
репозитории. Читатели спрашивают git, а когда его нет — манифест.

ГРАНИЦА, названная вслух: манифест — снимок момента синхронизации. Он не знает
о правках, сделанных в песочнице ПОСЛЕ неё. Для прогона тестов этого достаточно
(дерево туда и приезжает целиком), но выдавать манифест за живой git нельзя —
поэтому `source()` говорит, откуда взят ответ, и вызывающий может это напечатать.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / ".git_facts.json"

# ── Артефакты, которые МАШИНЕРИЯ перевыпускает и стейджит (2026-09-07) ───────
# Не работа человека: doc_agent пишет их на post-commit, arch_guard — кэш графа на
# pre-commit; подбирает их следующий коммит. Поэтому в рабочем дереве MacBook они
# лежат застейдженными ПО ПОСТРОЕНИЮ после каждого коммита.
#
# Дом здесь, а не в doc_agent, по одной причине: список нужен ОБОИМ — писателю
# (`doc_agent` стейджит ровно эти имена) и читателю (`integrity_tests
# .check_macbook_uncommitted` их вычитает), а `doc_agent` датчику не по карману:
# он на импорте зовёт `secrets_dir()` и тянет реестр замысла. git_facts не тянет
# ни секретов, ни БД — его импортировать дёшево обоим.
#
# ЗАЧЕМ ФИЛЬТР ВООБЩЕ. Замер 2026-09-07 по 14 хранящимся снимкам `backup.sh`:
# без фильтра датчик сигналил бы 14 дней из 14 — чистый шум, обучающий пролистывать
# (§13). С фильтром 2 из 14, и оба попадания настоящие: 30.07 (пять файлов работы —
# план пробы, сайдкар, две доки, реестр замысла) и 04.08 (правка CLAUDE.md руками).
#
# ГРАНИЦА, названная вслух: фильтр по ИМЕНИ, а не по авторству. Правка CHANGELOG.md
# руками датчиком не увидится. Цена принята: по конвенции проекта CHANGELOG пишет
# doc_agent, а альтернатива (сверять содержимое с тем, что перевыпустила бы машинерия)
# стоит дороже, чем прячет.
#
# Что имена ЖИВЫЕ — кто-то в репозитории действительно их стейджит — стережёт
# tests/consistency/test_machine_staged_files.py; та же форма, что у
# `test_warn_classes_match_live_labels`: имя без живого писателя = мёртвый фильтр.
STAGED_BY_DOC_AGENT = ("CHANGELOG.md", "ARCH_SNAPSHOT.md", "ARCH_SNAPSHOT.en.md")   # post-commit, git add, ветка bp_changed; .en — шапка версии (arch-en-gen 29.09)
STAGED_BY_SECURITY = ("SECURITY.md",)                        # post-commit, git add, ветка sec_changed
# arch_guard кэш ПЕРЕЗАПИСЫВАЕТ, но не стейджит — в дереве он остаётся unstaged.
# Для фильтра разницы нет: не работа человека ни в том, ни в другом виде.
REGENERATED_BY_ARCH_GUARD = (".arch_graph.json", ".arch_graph.en.json")   # .en — кэш событий английской карты (arch-en-gen 29.09)
MACHINE_REGENERATED_FILES = (
    STAGED_BY_DOC_AGENT + STAGED_BY_SECURITY + REGENERATED_BY_ARCH_GUARD
)


def _git(args: list[str]) -> str | None:
    """Ответ настоящего git или None. Отсутствие git и ошибка git здесь
    НЕРАЗЛИЧИМЫ намеренно: и то и другое означает «спрашивать некого»."""
    try:
        # core.quotepath=false ОБЯЗАТЕЛЕН, а не косметика (внешнее ревью 14.09).
        # Без него git отдаёт не-ASCII пути в октальном экранировании и в кавычках:
        # «"\320\275\320\276..."» вместо «новый_модуль.py». Читатели сравнивают
        # имена ТОЧНО (вычитание MACHINE_REGENERATED_FILES), поэтому любой не-ASCII
        # артефакт из фильтра перестал бы фильтроваться молча, а человеку датчик
        # называл бы файл, которого он у себя не найдёт. Тот же класс уже был
        # починен в доставке записок и не был перенесён сюда — см. корпус C-39.
        r = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=ROOT,
                           capture_output=True,
                           text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


# ── Слитые нити (2026-09-23, нить changelog-at-merge) ──────────────────────────
# Один дом для двух читателей: писателя строки журнала (doc_agent --thread-merge) и
# сторожа журнала (integrity_tests.check_changelog_freshness). Разойдись они в том,
# что считать «работой нити», — сторож требовал бы строку, которую писатель не пишет.
THREAD_MERGE_SUBJECT = re.compile(r"^Merge branch 'thread/([^']+)'")
DOC_ONLY_SUBJECT = re.compile(r"^(handoff|docs)[(:]")
"""Коммиты без работы кода: записки нитей и артефакты документации."""


def thread_merges(rev: str = "HEAD") -> list[tuple[str, str]] | None:
    """(sha, slug) слияний нитей в первой линии истории, старые первыми. None — git нет."""
    out = _git(["log", "--first-parent", "--merges", "--reverse", "--format=%H%x09%s", rev])
    if out is None:
        return None
    merges = []
    for line in out.splitlines():
        sha, _, subject = line.partition("\t")
        m = THREAD_MERGE_SUBJECT.match(subject)
        if m:
            merges.append((sha, m.group(1)))
    return merges


def thread_code_subjects(merge_sha: str) -> list[str] | None:
    """Заголовки коммитов работы нити (второй родитель минус первый), без слияний, записок
    и артефактов документации, в порядке работы. None — git нет."""
    out = _git(["log", "--no-merges", "--reverse", "--format=%s",
                f"{merge_sha}^1..{merge_sha}^2"])
    if out is None:
        return None
    return [s.strip() for s in out.splitlines()
            if s.strip() and not DOC_ONLY_SUBJECT.match(s.strip())]


def _manifest() -> dict:
    # `open()`, а не `Path.read_text`: этот модуль зовётся из горячих путей, где тесты
    # подменяют `Path.read_text` для воспроизведения гонок. Лишнее чтение через тот же
    # метод сдвигало их счётчики — поймано `test_gate_state_machine` 2026-08-10.
    # Гигиена, а не исправление: сам тест починен ключом по файлу.
    try:
        with open(MANIFEST, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def source() -> str:
    """Откуда берутся ответы: 'git' | 'manifest' | 'none'. Для печати в отчётах —
    молча подменять живой git снимком нельзя, читатель обязан видеть разницу."""
    if _git(["rev-parse", "--git-dir"]) is not None:
        return "git"
    return "manifest" if MANIFEST.exists() else "none"


def head_sha() -> str:
    """Короткий HEAD sha. 'unknown' только если нет ни git, ни манифеста."""
    out = _git(["rev-parse", "--short", "HEAD"])
    if out and out.strip():
        return out.strip()
    return str(_manifest().get("head_sha") or "unknown")


def tracked(*globs: str) -> list[str]:
    """Отслеживаемые И ещё не добавленные файлы (`--cached --others`).

    `--others` не прихоть: новая страница почти всегда untracked ровно в тот
    момент, который сторожа и ловят. Фильтрация по globs при ответе из манифеста
    делается тем же `PurePath.match`, что понимает git-глоб `docs/**/*.md`.
    """
    args = ["ls-files", "--cached", "--others", "--exclude-standard", *globs]
    out = _git(args)
    if out is not None:
        return list(dict.fromkeys(out.splitlines()))
    files = _manifest().get("files") or []
    if not globs:
        return list(files)
    from fnmatch import fnmatch
    keep = []
    for rel in files:
        for g in globs:
            # git-глоб `*.py` без слэша совпадает на ЛЮБОЙ глубине, fnmatch — нет.
            pat = g if "/" in g else f"*{g}" if g.startswith("*") else f"*/{g}"
            if fnmatch(rel, g) or fnmatch(rel, pat) or fnmatch(rel, g.replace("**/", "*/")):
                keep.append(rel)
                break
    return list(dict.fromkeys(keep))


def write_manifest(dest: Path | None = None) -> dict:
    """Снять факты в НАСТОЯЩЕМ репозитории и записать рядом с деревом.

    Зовётся синхронизацией песочницы. Отказывается писать пустой манифест:
    пустой список отслеживаемых файлов неотличим от «git не ответил», и сторож,
    прочитавший такой манифест, доложил бы «нарушений нет» на пустом периметре.
    """
    files = tracked()
    sha = head_sha()
    if not files or sha == "unknown":
        raise RuntimeError(
            f"манифест не снят: files={len(files)}, sha={sha!r}. Пустой манифест "
            f"опаснее его отсутствия — сторожа стали бы зелёными от пустоты")
    data = {"head_sha": sha, "files": files}
    (dest or MANIFEST).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"files": len(files), "head_sha": sha}


if __name__ == "__main__":
    print(write_manifest())


# ── ДЕТЕКТОР ЗАХВАТА ЧУЖОГО ФАЙЛА (14.09, признак дал внешний тестировщик) ───
# ЧТО ЭТО ЗА ВОПРОС. Долг BL-THREAD-GATE-1 ждал «следующего зафиксированного
# случая захвата», и признака у него не было: в самом коммите носителя понятия
# «эта сессия» НЕТ — автор, коммиттер, трейлеры, notes, reflog и дерево
# одинаковы для своего файла и для захваченного (проверено исполнением).
#
# ПРИЗНАК СНАРУЖИ КОММИТА. Снимок `refs/backups/wip` с 14.09 перекатывается
# каждые 3 часа и существует даже на чистом дереве — то есть даёт машинную
# ленту «что было НЕзакоммичено в момент T». Захват выглядит так: файл приехал
# в коммит C, будучи незакоммиченным на последнем снимке перед C, И его
# содержимое в C ПОБАЙТНО то же, что в снимке — значит коммиттер его после
# снимка не трогал, он просто оказался в индексе.
#
# ГРАНИЦЫ, ЗАМЕРЕННЫЕ, А НЕ ОБЪЯВЛЕННЫЕ:
#   · окно 3 часа: захват между снимком и коммитом не виден (ложный пропуск);
#   · своя давняя незакоммиченная работа, закоммиченная без правок, выглядит
#     идентично (ложное срабатывание);
#   · деревья нитей снимок не покрывает вовсе; копии ВЕТОК нитей с 16.09 едут
#     отдельными refs (refs/backups/thread/*) и цепочки снимку не добавляют —
#     окно признака от этого не расширилось (BL-CAPTURE-WINDOW-1);
#   · всё это работает, только пока джоба снимка жива.
# Поэтому исход — ВОПРОС человеку, а не вердикт: «ты ли это коммитил?».
def captured_files(commit: str = "HEAD", snap_ref: str = "refs/backups/wip"):
    """Файлы в `commit`, похожие на захваченные. None = судить не на чем.

    Единственная публичная точка этого признака (D2). Возвращает список имён —
    не вердикт: отличить захват от «моя же старая работа» машине нечем.
    """
    snap = _git(["rev-parse", "--verify", f"{snap_ref}^{{commit}}"])
    if not snap:
        return None
    snap = snap.strip()
    if _git(["rev-parse", "--verify", f"{snap}^"]) is None:
        return None
    # Снимок обязан быть в причинном ПРОШЛОМ коммита: иначе сравнивать нечего.
    if subprocess.run(["git", "merge-base", "--is-ancestor", f"{snap}^", commit],
                      cwd=ROOT, capture_output=True).returncode != 0:
        return None
    было_грязным = set((_git(["diff", "--name-only", f"{snap}^", snap]) or "").split("\n"))
    в_коммите = set((_git(["show", "--name-only", "--format=", commit]) or "").split("\n"))
    подозрительные = []
    for f in sorted(x for x in (было_грязным & в_коммите) if x.strip()):
        if f in MACHINE_REGENERATED_FILES:
            continue  # машинные артефакты не «чужая работа» по построению
        a = _git(["rev-parse", f"{snap}:{f}"])
        b = _git(["rev-parse", f"{commit}:{f}"])
        if a and b and a.strip() == b.strip():
            подозрительные.append(f)
    return подозрительные


# ── Код MacBook доехал до Studio? (27.09, решение владельца: BL-AGENT-BRIDGE-1 п.9) ──
# Коммит через мост иногда не доживал до push: деплоя нет, и сигнала нет ни одного — не
# падение. Studio не может спросить ноутбук, но УЖЕ видит его HEAD: снимок `backup.sh --wip`
# (раз в 3 часа) — коммит, чей родитель и есть HEAD главной копии MacBook. Если этого
# родителя нет в истории развёрнутой ветки дольше grace — код застрял на ноутбуке.
def undeployed_head(snap_ref: str = "refs/backups/wip", target: str = "refs/heads/main",
                    now: float | None = None, grace_s: int = 3600) -> dict | None:
    """None — судить не на чем (нет снимка / нет родителя). Иначе {head, age_s, deployed}:
    head — HEAD MacBook на момент снимка, age_s — его возраст, deployed — есть ли он в target.
    grace: свежий коммит мог ещё ехать (снимок между коммитом и push) — не вина деплоя."""
    import time as _time
    head = _git(["rev-parse", "--verify", f"{snap_ref}^1^{{commit}}"])
    if not head:
        return None
    head = head.strip()
    ts = _git(["log", "-1", "--format=%ct", head])
    if not ts or not ts.strip().isdigit():
        return None
    age = int((now if now is not None else _time.time()) - int(ts.strip()))
    deployed = subprocess.run(["git", "merge-base", "--is-ancestor", head, target],
                              cwd=ROOT, capture_output=True).returncode == 0
    return {"head": head[:12], "age_s": age, "deployed": deployed or age < grace_s}
