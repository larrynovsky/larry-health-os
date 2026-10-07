#!/usr/bin/env python3
"""Зеркало публичной зоны в открытый репозиторий (решение владельца 28.09: «путь 2 — зеркало»).

Рабочий репозиторий владельца остаётся закрытым вместе с историей. Открытый получает только
публичную зону (всё, что не перечислено в publication_zones.yaml) — снимком ЗАКОММИЧЕННОГО
состояния, одним коммитом на выгрузку: «Export of <sha>». Незакоммиченное и приватное не
уезжают по построению: список файлов и список зон берутся из самого коммита.

    python3 scripts/public_mirror.py                 # собрать и закоммитить в клон зеркала
    python3 scripts/public_mirror.py --push          # то же и отправить на GitHub
    python3 scripts/public_mirror.py --release v0.1.0   # выпуск образа — ТОЛЬКО по команде владельца

Клон зеркала — отдельный каталог (по умолчанию ~/.public_mirror/<имя>), не рабочая копия.
Правки из открытого репозитория сюда не едут: их переносят в рабочий репозиторий вручную
(docs/how-to/publish_mirror.md), и следующая выгрузка их вернёт уже как свои.
"""
from __future__ import annotations

import argparse
import io
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "git@github.com:larrynovsky/larry-health-os.git"
REPO = "larrynovsky/larry-health-os"
# Что выпуск обязан нести (.github/workflows/release.yml): без любого из них урок не проходим.
RELEASE_ASSETS = {"compose.yaml", "health.env", "com.larry.health.colima.plist", "install.sh"}
_TAG = re.compile(r"^v\d+\.\d+\.\d+$")
DEST = Path.home() / ".public_mirror" / "larry-health-os"
# Отметка прочитанного (решение владельца 29.09: «после полного чтения»). Полное чтение экспорта
# 29.09 нашло десятки следов живых данных в прозе — стиль проекта «заземли замером» порождает их
# сам, поэтому чистый снимок не держится. Пуш требует, чтобы каждый публичный файл, изменённый
# после прочитанного коммита, был прочитан: список даёт --unread, отметку ставит --mark-read.
READ_MARK = "plans/publication_read.txt"
# Машинная строка версии/даты, которую doc_agent переписывает при каждом закрытии нити.
# Изменение только в ней читать нечего; без исключения отметка устаревала бы от самого слияния.
_MACHINE_LINE = re.compile(r"^\*\*(Версия|Version):\*\* [\d.]+ \| \*\*(Дата|Date):\*\* \d{4}-\d{2}-\d{2}$")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, text=True,
                          capture_output=True).stdout


def export(ref: str, dest: Path, root: Path = ROOT) -> tuple[str, int]:
    """Разложить публичную зону коммита ref в dest (кроме dest/.git). Возвращает (sha, файлов)."""
    sys.path.insert(0, str(root))
    import pii_census
    sha = _git(root, "rev-parse", ref).strip()
    files = pii_census.public_files_at(sha, root)
    for p in dest.iterdir():                          # зеркало — снимок: удалённое удаляется
        if p.name != ".git":
            shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
    raw = subprocess.run(["git", "-C", str(root), "archive", "--format=tar", sha, "--", *files],
                         check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
        tf.extractall(dest, filter="data")          # имена — из git, фильтр — пояс поверх
    return sha, len(files)


# Внешний вклад (PR) переносится в рабочий репозиторий руками, автор — строкой Co-authored-by.
# Выгрузка — один коммит «Export of», и без переноса этих строк автор терялся бы на GitHub (07.10:
# внешний вклад ушёл без имени автора). Берём только адреса GitHub noreply — они и так публичны;
# личный адрес в строке соавтора не уедет.
_NOREPLY = re.compile(r"^.+<[^<>@\s]+@users\.noreply\.github\.com>$")


def _coauthors(cwd: Path, ref: str) -> set[str]:
    out = _git(cwd, "log", "--format=%(trailers:key=Co-authored-by,valueonly)", ref)
    return {s.strip() for s in out.splitlines() if _NOREPLY.match(s.strip())}


def uncredited(sha: str, dest: Path, root: Path = ROOT) -> list[str]:
    """Соавторы из истории sha, которых ещё нет ни в одном коммите зеркала."""
    empty = subprocess.run(["git", "-C", str(dest), "rev-parse", "-q", "--verify", "HEAD"],
                           capture_output=True).returncode != 0          # свежий клон без коммитов
    have = set() if empty else _coauthors(dest, "HEAD")
    return sorted(_coauthors(root, sha) - have)


def unread_public(ref: str, root: Path = ROOT) -> list[str]:
    """Публичные файлы ref, добавленные или изменённые после отметки прочитанного.

    Нет отметки — непрочитано всё. Удалённые файлы читать не нужно, поэтому их в списке нет.
    Отметка берётся ИЗ САМОГО КОММИТА ref, не с диска (30.09): 29.09 коммит отметки не прошёл, а
    пуш прошёл по незакоммиченному файлу — отметка на диске пропустила бы и чужую правку.
    """
    sys.path.insert(0, str(root))
    import pii_census
    sha = _git(root, "rev-parse", ref).strip()
    public = pii_census.public_files_at(sha, root)
    shown = subprocess.run(["git", "-C", str(root), "show", f"{sha}:{READ_MARK}"],
                           capture_output=True, text=True)
    committed = shown.stdout if shown.returncode == 0 else ""
    disk = (root / READ_MARK).read_text() if (root / READ_MARK).exists() else ""
    if disk.strip() != committed.strip():
        print(f"⚠ отметка {READ_MARK} на диске не совпадает с закоммиченной в {sha[:12]} — "
              "судится закоммиченная; закоммитьте отметку", file=sys.stderr)
    lines = [x.strip() for x in committed.splitlines()]
    base = next((x for x in lines if x and not x.startswith("#")), "")
    if not base:
        return sorted(public)
    changed = set(_git(root, "diff", "--name-only", "--diff-filter=ACMRT", base, sha).split())
    return sorted(f for f in changed & set(public) if not _only_machine_lines(root, base, sha, f))


# Живой замер в прозе (C-140, C-144, C-151, pub-scrub-1006): дата СОБЫТИЯ здоровья или пара
# давления рядом со словом про тело. Словарь такое не видит — нужна форма. Признак «событие, а
# не нить»: дата старше коммита больше чем на две недели (метки нитей и решений — свежие).
# Замер 06.10 по 30 дням истории: все известные утечки (даты замеров давления, пары давления,
# «пик» давления, дата забора крови) пойманы; ~110 строк за месяц — шум, поэтому это подсветка для
# читающего перед выгрузкой, а не блок коммита. Чего не ловит, вслух: число без даты и без
# пары (126.5), месяц словом без числа, медицинский факт без даты.
_HEALTH = re.compile(r"давлени|пульс|симптом|глюкоз|сахар|холестер|температур|ЧСС|сатурац|mmHg|"
                     r"мм рт|blood pressure|systolic|diastolic|биопс|химио|лучев|диагноз|приступ|"
                     r"боль|тонометр|кров|моч[аи]|замер|анализ|бланк|терапи|лечени", re.I)
_DATE = re.compile(r"(?<![\d.])(0?[1-9]|[12]\d|3[01])\.(0?[1-9]|1[0-2])(?![\d.])")
_MONTHS = ("январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр",
           "октябр", "ноябр", "декабр")
_DATE_WORD = re.compile(r"\b(0?[1-9]|[12]\d|3[01])\s+(январ|феврал|марта|апрел|мая|июн|июл|"
                        r"август|сентябр|октябр|ноябр|декабр)", re.I)
_BP_PAIR = re.compile(r"\b(1\d\d|[89]\d)/([4-9]\d|1[0-2]\d)\b")
EVENT_AGE_DAYS = 14


def _old_date(line: str, ref_day) -> bool:
    import datetime as dt
    found = [(int(m.group(1)), int(m.group(2))) for m in _DATE.finditer(line)]
    for m in _DATE_WORD.finditer(line):
        word = m.group(2).lower()
        mo = next((i + 1 for i, w in enumerate(_MONTHS) if word.startswith(w)), None)
        if mo:
            found.append((int(m.group(1)), mo))
    for d, mo in found:
        try:
            day = dt.date(ref_day.year if mo <= ref_day.month else ref_day.year - 1, mo, d)
        except ValueError:
            continue
        if (ref_day - day).days > EVENT_AGE_DAYS:
            return True
    return False


def suspicious_lines(ref: str, root: Path = ROOT) -> list[str]:
    """Добавленные после отметки строки публичных файлов, похожие на живой замер — «путь:текст»."""
    import datetime as dt
    sha = _git(root, "rev-parse", ref).strip()
    day = dt.date.fromisoformat(_git(root, "show", "-s", "--format=%cs", sha).strip())
    base = _read_base(sha, root)
    files = unread_public(ref, root)
    out = []
    for f in files:
        args = ["diff", "-U0", base, sha, "--", f] if base else ["show", f"{sha}:{f}"]
        try:
            text = _git(root, *args)
        except subprocess.CalledProcessError:
            continue
        for l in text.splitlines():
            if base and (not l.startswith("+") or l.startswith("+++")):
                continue
            body = l[1:] if base else l
            if _HEALTH.search(body) and (_BP_PAIR.search(body) or _old_date(body, day)):
                out.append(f"{f}: {body.strip()[:200]}")
    return out


def _read_base(sha: str, root: Path) -> str:
    shown = subprocess.run(["git", "-C", str(root), "show", f"{sha}:{READ_MARK}"],
                           capture_output=True, text=True)
    lines = [x.strip() for x in (shown.stdout if shown.returncode == 0 else "").splitlines()]
    return next((x for x in lines if x and not x.startswith("#")), "")


def _only_machine_lines(root: Path, base: str, sha: str, path: str) -> bool:
    """Изменение файла сводится к машинной строке версии/даты."""
    diff = _git(root, "diff", "-U0", base, sha, "--", path).splitlines()
    body = [l[1:] for l in diff if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    return bool(body) and all(_MACHINE_LINE.match(l) for l in body)


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, text=True, capture_output=True).stdout


def _run_of(workflow: str, **flt: str) -> dict:
    """Последний прогон workflow в открытом репозитории (фильтр — commit= или branch=); {} — нет
    прогона или самого workflow (gh тогда падает — это тот же ответ «проверки не было»)."""
    import json
    extra = [x for k, v in flt.items() for x in (f"--{k}", v)]
    r = subprocess.run(["gh", "run", "list", "--repo", REPO, "--workflow", workflow, *extra, "--limit",
                        "1", "--json", "databaseId,conclusion"], text=True, capture_output=True)
    runs = json.loads(r.stdout) if r.returncode == 0 else []
    return runs[0] if runs else {}


def codeql_verdict(head: str, analyses: list[dict], alerts: list[dict]) -> str | None:
    """Причина отказать выпуску по CodeQL или None (решение владельца 01.10, G7: находки видны
    только на вкладке Security — без этого серьёзная уезжает к людям незамеченной).
    Отказ: CodeQL ещё не разбирал этот коммит (старые «0 открытых» ничего не говорят о новом
    коде) или есть открытые critical/high. Ложную находку закрывают на GitHub С ПРИЧИНОЙ — тогда
    она не открыта и не держит выпуск."""
    if not any(a.get("commit_sha") == head and "codeql" in (a.get("tool", {}).get("name", "").lower())
               for a in analyses):
        return f"CodeQL ещё не разобрал {head[:7]} — дождитесь прогона (вкладка Actions → CodeQL)"
    serious = sorted(a["number"] for a in alerts
                     if (a.get("rule", {}).get("security_severity_level") or "") in ("critical", "high"))
    if serious:
        return (f"открыты находки CodeQL critical/high: {', '.join('#' + str(n) for n in serious)}"
                f" — исправить или закрыть с причиной (github.com/{REPO}/security/code-scanning)")
    return None


def _codeql_state() -> tuple[list[dict], list[dict]]:
    import json
    base = f"repos/{REPO}/code-scanning"
    analyses = json.loads(_gh("api", f"{base}/analyses?ref=refs/heads/main&per_page=30"))
    # --paginate склеивает страницы как «[…][…]» — читаем по объекту на строку.
    lines = _gh("api", "--paginate", "--jq", ".[] | {number, rule: {security_severity_level: .rule.security_severity_level}}",
                f"{base}/alerts?state=open&ref=refs/heads/main&per_page=100").splitlines()
    return analyses, [json.loads(l) for l in lines if l.strip()]


def dependabot_verdict(alerts: list[dict]) -> str | None:
    """Причина отказать выпуску по уязвимым зависимостям или None (решение владельца 01.10:
    гейт G7 расширен с CodeQL на Dependabot — в тот день 5 алертов, 2 high, заметил глаз на
    выводе пуша, а не конвейер). Держат выпуск открытые critical/high; закрытые на GitHub
    с причиной — не держат. Граница: алерты Dependabot пересчитываются после пуша не мгновенно —
    свежесть к коммиту не сверяется (урок на том же коммите идёт ~10 минут, граф успевает)."""
    serious = sorted((a["number"], a.get("package", "?")) for a in alerts
                     if (a.get("severity") or "") in ("critical", "high"))
    if serious:
        return (f"открыты уязвимости зависимостей critical/high: "
                f"{', '.join(f'#{n} {p}' for n, p in serious)}"
                f" — обновить пакет (docs/how-to/dependency_updates.md) или закрыть с причиной"
                f" (github.com/{REPO}/security/dependabot)")
    return None


def _dependabot_state() -> list[dict]:
    import json
    lines = _gh("api", "--paginate", "--jq",
                ".[] | {number, severity: .security_advisory.severity, package: .dependency.package.name}",
                f"repos/{REPO}/dependabot/alerts?state=open&per_page=100").splitlines()
    return [json.loads(l) for l in lines if l.strip()]


def release(tag: str, dest: Path) -> int:
    """Выпуск (решение владельца 30.09: только по его команде). Тег ставится на то, что УЖЕ
    лежит на GitHub, и только если урок на этом коммите прошёл (tutorial.yml) — выпуск не
    обгоняет проверку урока. Затем ждёт release.yml и громко сверяет файлы выпуска."""
    if not _TAG.match(tag):
        print(f"⛔ тег {tag!r}: нужен вид v1.2.3", file=sys.stderr)
        return 1
    _git(dest, "fetch", "-q", "--tags", "origin")
    head, remote = _git(dest, "rev-parse", "HEAD").strip(), _git(dest, "rev-parse", "origin/main").strip()
    if head != remote:
        print(f"⛔ клон зеркала ({head[:7]}) не совпадает с GitHub ({remote[:7]}): сначала --push",
              file=sys.stderr)
        return 1
    if _git(dest, "tag", "-l", tag).strip():
        print(f"⛔ тег {tag} уже есть — выпуск не переписывается, возьмите следующий номер", file=sys.stderr)
        return 1
    import json
    verdict = _run_of("tutorial.yml", commit=head).get("conclusion", "")
    if verdict != "success":
        print(f"⛔ урок на {head[:7]} не зелёный ({verdict or 'прогона нет'}): выпуск не делается"
              f" — gh run list --repo {REPO} --workflow tutorial.yml", file=sys.stderr)
        return 1
    try:
        why = codeql_verdict(head, *_codeql_state())
    except (subprocess.CalledProcessError, ValueError) as e:
        why = f"не удалось прочитать CodeQL ({type(e).__name__}) — выпуск без проверки не делается"
    if not why:
        try:
            why = dependabot_verdict(_dependabot_state())
        except (subprocess.CalledProcessError, ValueError) as e:
            why = f"не удалось прочитать Dependabot ({type(e).__name__}) — выпуск без проверки не делается"
    if why:
        print(f"⛔ {why}", file=sys.stderr)
        return 1
    _git(dest, "-c", "core.hooksPath=/dev/null", "tag", "-a", tag, "-m", f"Release {tag}")
    _git(dest, "push", "-q", "origin", tag)
    print(f"тег {tag} отправлен; жду release.yml…", flush=True)
    import time
    for _ in range(30):
        run = str(_run_of("release.yml", branch=tag).get("databaseId", ""))
        if run:
            break
        time.sleep(10)
    if not run or subprocess.run(["gh", "run", "watch", run, "--repo", REPO, "--exit-status"]).returncode:
        print(f"⛔ release.yml для {tag} не прошёл — gh run view {run} --repo {REPO} --log-failed",
              file=sys.stderr)
        return 1
    assets = {a["name"] for a in json.loads(_gh("release", "view", tag, "--repo", REPO, "--json", "assets"))["assets"]}
    if assets != RELEASE_ASSETS:
        print(f"⛔ файлы выпуска {sorted(assets)} ≠ {sorted(RELEASE_ASSETS)}", file=sys.stderr)
        return 1
    print(f"✅ выпуск {tag}: образ ghcr.io/{REPO}:{tag}, файлы {', '.join(sorted(assets))}\n"
          "   Пакет GHCR публичным делает только владелец (docs/how-to/release.md).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ref", default="main")
    ap.add_argument("--dest", default=str(DEST))
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--unread", action="store_true", help="публичные файлы, не прочитанные с отметки")
    ap.add_argument("--mark-read", metavar="REF", help="записать: публичная зона REF прочитана целиком")
    ap.add_argument("--cleared", type=int, metavar="N",
                    help="с --mark-read: подозрительных строк просмотрено и признано безопасными (N)")
    ap.add_argument("--release", metavar="vX.Y.Z", help="выпуск образа: тег на GitHub, ждать release.yml")
    a = ap.parse_args(argv)
    if a.release:
        return release(a.release, Path(a.dest).expanduser())
    if a.mark_read:
        sus = suspicious_lines(a.mark_read, ROOT)
        if sus and a.cleared != len(sus):
            print("\n".join(sus))
            print(f"⛔ {len(sus)} строк похожи на живой замер (дата события или давление рядом со "
                  f"словом про тело). Уберите живое или, просмотрев каждую, повторите с "
                  f"--cleared {len(sus)}", file=sys.stderr)
            return 1
        sha = _git(ROOT, "rev-parse", a.mark_read).strip()
        (ROOT / READ_MARK).write_text(f"# публичная зона прочитана целиком по этот коммит\n{sha}\n")
        print(f"отметка: {sha[:12]} — закоммитьте {READ_MARK}")
        return 0
    if a.unread or a.push:
        left = unread_public(a.ref)
        if a.unread and left:
            sus = suspicious_lines(a.ref, ROOT)
            if sus:
                print(f"⚠ ПРОЧИТАТЬ ПЕРВЫМИ — {len(sus)} строк похожи на живой замер:")
                print("\n".join(sus) + "\n---")
        if a.unread or left:
            print("\n".join(left) or "всё прочитано")
        if left:
            print(f"⛔ {len(left)} публичных файлов не прочитаны после отметки ({READ_MARK});"
                  " прочитайте их, затем --mark-read", file=sys.stderr)
            return 1
        if a.unread:
            return 0
    dest = Path(a.dest).expanduser()
    if not (dest / ".git").exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", REMOTE, str(dest)], check=True)
    sha, n = export(a.ref, dest, ROOT)
    _git(dest, "add", "-A")
    if not _git(dest, "status", "--porcelain").strip():
        print(f"зеркало уже совпадает с {sha[:7]} — коммитить нечего")
        return 0
    # Хуки клона зеркала выключены (решение владельца 28.09, «б»): каждая строка экспорта уже
    # прошла гейты рабочего репозитория; квитанция ponytail на копию ничего не проверяет.
    msg = f"Export of {sha[:12]}"
    if who := uncredited(sha, dest, ROOT):
        msg += "\n\n" + "\n".join(f"Co-authored-by: {w}" for w in who)
    _git(dest, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", msg)
    print(f"✅ зеркало: {n} файлов из {sha[:7]} закоммичены в {dest}")
    if a.push:
        subprocess.run(["git", "-C", str(dest), "push", "-q", "origin", "HEAD:main"], check=True)
        print("✅ отправлено на GitHub")
    return 0


if __name__ == "__main__":
    sys.exit(main())
