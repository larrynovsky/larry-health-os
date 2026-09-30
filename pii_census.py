"""pii_census — перепись личных данных в ПУБЛИЧНОЙ зоне репозитория и её ратчет.

ЗАЧЕМ. Решение владельца 2026-09-23: проект когда-нибудь откроется на GitHub — новым
чистым репозиторием (экспорт публичной зоны одним коммитом; история и журналы остаются
у владельца). Значит, дерево обязано быть публикуемым в любой момент, а не «вычищаемым
к дате»: без сторожа каждый коммит снова пачкает дерево. Замер 2026-09-23: находки в
330 публичных файлах против 59 записей аудита 2026-09-22 — список аудита был выборкой,
не переписью. Решение владельца того же дня: ловить ДО коммита, а не ночью.

ТРИ ДОМА, у каждого один писатель:
  publication_zones.yaml    — какие пути НЕ публикуются (журналы, данные владельца).
  private/pii_terms.yaml    — словарь: классы литералов и шаблонов. Сам словарь — PII,
                              поэтому живёт в приватной зоне и в публикацию не едет.
  private/pii_baseline.json — долг: число находок на публичный файл. Ходит только вниз.

ЧТО СУДИТ: публичный путь И содержимое файла, без учёта регистра, по подстроке.
ЧЕГО НЕ ЛОВИТ (вслух, чтобы зелёный не читали шире, чем он есть):
  * личное, которого нет в словаре — перепись знает только названное;
  * смысл, сказанный другими словами («после той самой операции»);
  * содержимое бинарных файлов (xlsx/pdf/png) — читается с потерями; их судит глаз;
  * класс provenance (2026-09-26) ловит ПОМЕТКУ автора «это настоящие данные <чьи/откуда>»,
    а не сами данные: утечка без пометки или с пометкой другими словами проходит.
Зелёный значит «известных литералов не прибавилось», а не «личных данных нет».

CLI:
  python3 pii_census.py --staged    pre-commit: exit 1, если у застейдженного публичного
                                    файла находок больше долга. Уменьшение долга пишется
                                    и до-стейджится само — ратчет вниз без ручного шага.
  python3 pii_census.py report      классы × число файлов по рабочему дереву.
  python3 pii_census.py rebaseline  записать текущие числа; ОТКАЗ, если что-то выросло.
"""
from __future__ import annotations

# INTENT: public_zone — замысел и границы обещания: subsystem_intent.yaml (project_intent public_zone)

import collections
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
ZONES = "publication_zones.yaml"
TERMS = "private/pii_terms.yaml"
BASELINE = "private/pii_baseline.json"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=True).stdout


def _zones(root: Path) -> list[str]:
    data = yaml.safe_load((root / ZONES).read_text(encoding="utf-8"))
    return [z["glob"] for z in data["private"]]


def _terms(root: Path) -> dict[str, re.Pattern]:
    """Класс → один скомпилированный шаблон. Нет словаря — отказ, а не пустой суд:
    сторож без словаря зеленел бы на любом коммите (класс «зелёный от окружения», §20)."""
    p = root / TERMS
    if not p.exists():
        raise SystemExit(f"pii_census: нет словаря {TERMS} — суд невозможен, коммит остановлен")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    out = {}
    for cls in sorted(set(data.get("literals", {})) | set(data.get("patterns", {}))):
        parts = [re.escape(t) for t in data.get("literals", {}).get(cls, [])]
        parts += data.get("patterns", {}).get(cls, [])
        if parts:   # пустой класс (шаблон установки) — пустой шаблон совпал бы с КАЖДОЙ позицией
            out[cls] = re.compile("|".join(parts), re.IGNORECASE)
    return out


def literals(classes: list[str] | None = None, root: Path = ROOT) -> list[str]:
    """Литералы словаря для РАНТАЙМ-сторожей (egress PubMed, лексикон дайджеста) — чтобы
    личные слова жили в одном доме, а не копией внутри каждого сторожа. В отличие от
    переписи здесь нет словаря → [] + WARNING, а не отказ: у публичной установки своих
    личных слов нет, а сторож с общими правилами продолжает работать."""
    p = root / TERMS
    if not p.exists():
        import logging
        logging.getLogger(__name__).warning("pii_census: словаря %s нет — личные слова не стерегутся", TERMS)
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")).get("literals", {})
    return [t for c, ts in data.items() if classes is None or c in classes for t in ts]


def pattern(classes: list[str] | None = None, root: Path = ROOT) -> re.Pattern | None:
    """Один шаблон по литералам И regex-шаблонам словаря выбранных классов; словаря нет → None."""
    lits = literals(classes, root)
    p = root / TERMS
    pats = []
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")).get("patterns", {})
        pats = [x for c, xs in data.items() if classes is None or c in classes for x in xs]
    parts = [re.escape(t) for t in lits] + pats
    return re.compile("|".join(parts), re.IGNORECASE) if parts else None


def probes(name: str, root: Path = ROOT) -> list[str]:
    """Образцы утечек для оракула класса словаря (probes.<name> в приватном словаре).
    Образцы — сами личные данные, поэтому их дом — приватный словарь, а не публичный тест:
    разрезанные склейкой строки перепись не видит, а человек читает как досье (26.09).
    Словаря нет → [] (тесты-оракулы помечены owner_data)."""
    p = root / TERMS
    if not p.exists():
        return []
    return list((yaml.safe_load(p.read_text(encoding="utf-8")).get("probes") or {}).get(name) or [])


def _is_private(path: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in globs)


def _scan(path: str, text: str, terms: dict[str, re.Pattern]) -> collections.Counter:
    # Двоичный файл (PNG, PDF…) текстом не судится: байты, декодированные с errors=ignore,
    # дают случайные «слова» (27.09: PNG-схема дала ложное [clinical]). Признак — NUL-байт.
    # Граница: личное, нарисованное на картинке, этот сторож не видит — только глаза.
    body = path + "\n" + ("" if "\0" in text else text)
    return collections.Counter({c: n for c, p in terms.items() if (n := len(p.findall(body)))})


def _tracked(root: Path) -> list[str]:
    """Отслеживаемые файлы. -z: иначе git экранирует не-ASCII пути (\\342…) и такие файлы
    выпадали из переписи молча (найдено 2026-09-23 пробой чистого клона)."""
    try:
        return [p for p in _git(root, "-c", "core.quotepath=off", "ls-files", "-z").split("\0") if p]
    except (subprocess.CalledProcessError, OSError):
        # Песочница test_on_studio — дерево БЕЗ .git; список отслеживаемых файлов
        # там лежит манифестом рядом (git_facts, 2026-08-10). OSError — образ контейнера:
        # там нет не только .git, но и самого git (замер 30.09 07:50: FileNotFoundError
        # из conftest уронил весь прогон INTERNALERROR'ом). Только для корня репо:
        # чужой каталог без git — это ошибка вызывающего, а не повод гадать.
        import git_facts
        if git_facts.ROOT != root.resolve() or git_facts.source() != "manifest":
            raise
        return git_facts.tracked()


def public_files(root: Path = ROOT) -> list[str]:
    """Отслеживаемые файлы публичной зоны — один дом ответа «что уедет в открытый
    репозиторий»: перепись, проба чистого клона, будущий экспорт."""
    globs = _zones(root)
    return [p for p in _tracked(root) if not _is_private(p, globs)]


def public_files_at(ref: str, root: Path = ROOT) -> list[str]:
    """То же, что public_files, но для коммита ref: и файлы, и список зон — из самого
    коммита, не из рабочего дерева. Читатель — scripts/public_mirror.py: зеркало собирается
    из закоммиченного, незакоммиченное в открытый репозиторий не уезжает."""
    data = yaml.safe_load(_git(root, "show", f"{ref}:{ZONES}"))
    globs = [z["glob"] for z in data["private"]]
    paths = _git(root, "-c", "core.quotepath=off", "ls-tree", "-r", "-z", "--name-only", ref)
    return [p for p in paths.split("\0") if p and not _is_private(p, globs)]


def is_public_export(root: Path = ROOT) -> bool:
    """Это публичная выгрузка (копия постороннего), а не рабочая копия владельца?

    Признак — из того же дома, что список зон: ни один ОТСЛЕЖИВАЕМЫЙ файл не лежит в
    приватной зоне. Отслеживаемый, а не «лежит на диске»: установщик у постороннего
    сам создаёт private/infra.yaml и private/pii_terms.yaml, но в git их не кладёт.
    Читатель — tests/conftest.py: тесты с меткой owner_data (правила и данные владельца)
    в выгрузке пропускаются, у владельца бегут и краснеют, если данных нет.
    Граница: форк, добавивший свои файлы в приватные зоны, перестаёт считаться выгрузкой."""
    globs = _zones(root)
    return not any(_is_private(p, globs) for p in _tracked(root))


def census(root: Path, paths: list[str] | None = None, read=None) -> dict[str, collections.Counter]:
    """Публичный путь → находки по классам. paths=None — все отслеживаемые файлы;
    read(path) -> str — откуда брать содержимое (рабочее дерево по умолчанию, индекс
    в pre-commit). Приватные пути в результат не попадают вовсе."""
    globs, terms = _zones(root), _terms(root)
    if paths is None:
        paths = _tracked(root)
    if read is None:
        def read(p):
            try:
                return (root / p).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return ""
    out = {}
    for p in paths:
        if _is_private(p, globs):
            continue
        hits = _scan(p, read(p), terms)
        if hits:
            out[p] = hits
    return out


# ── ПРОФИЛЬ ГЕНОТИПА ─────────────────────────────────────────────────────────
# Отдельный общедоступный генотип не обязательно идентифицирует человека,
# но сочетание генотипов может совпасть с частным профилем даже при пустой
# переписи литералов. Поэтому судится ПРОФИЛЬ — dict-литерал {rsid: генотип}
# в публичном .py — против генотипов тенантов. Генотипы тенантов лежат только
# в их БД; судья — ночная целостность на основной машине
# (integrity_tests.check_public_genotype_profile), а здесь — чистые функции без БД.
_RS_KEY = re.compile(r"^rs\d{3,}$")
_GT_VAL = re.compile(r"^[ACGT]/?[ACGT]$")


def genotype_profiles(src: str) -> list[tuple[int, dict[str, str]]]:
    """dict-литералы {rsid: генотип} с ≥2 записями → [(строка, {rsid: генотип в алфавитном
    порядке аллелей})]. Нераспарсиваемый исходник → пусто (его судит ruff, не этот гейт)."""
    import ast
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Dict):
            continue
        d = {}
        for k, v in zip(n.keys, n.values):
            if (isinstance(k, ast.Constant) and isinstance(k.value, str) and _RS_KEY.match(k.value)
                    and isinstance(v, ast.Constant) and isinstance(v.value, str) and _GT_VAL.match(v.value)):
                d[k.value] = "".join(sorted(v.value.replace("/", "")))
        if len(d) >= 2:
            out.append((n.lineno, d))
    return out


def profile_leaks(profiles: dict[str, list], tenants: dict[str, dict[str, str]],
                  min_hits: int) -> list[tuple[str, int, str, int]]:
    """(путь, строка, тенант, число совпадений) для профилей, где у тенанта ≥ min_hits
    РАЗЛИЧАЮЩИХ совпадений: генотип равен генотипу тенанта и НЕ равен генотипу хотя бы одного
    другого. Частый генотип, совпадающий у всех, человека не выдаёт и не считается.
    Граница: при одном тенанте различающих совпадений нет по построению — судья слеп."""
    out = []
    for path, plist in profiles.items():
        for line, d in plist:
            for name, g in tenants.items():
                hits = sum(1 for r, gt in d.items()
                           if g.get(r) == gt and not all(o.get(r) == gt for o in tenants.values()))
                if hits >= min_hits:
                    out.append((path, line, name, hits))
    return out


def judge(counts: dict[str, collections.Counter], baseline: dict[str, int],
          paths: list[str]) -> tuple[dict[str, tuple[int, int]], dict[str, int]]:
    """По каждому из paths: (выросло {путь: (долг, стало)}, уменьшилось {путь: стало})."""
    grew, shrank = {}, {}
    for p in paths:
        now, was = sum(counts.get(p, {}).values()), baseline.get(p, 0)
        if now > was:
            grew[p] = (was, now)
        elif now < was:
            shrank[p] = now
    return grew, shrank


def _where(root: Path, path: str, text: str, limit: int = 6) -> list[str]:
    """Где находки — по ВСЕМУ телу, как считает _scan: шаблон с \\s ловит и фразу,
    перенесённую на следующую строку (докстринги), и построчный поиск такую находку
    не показал бы — блок без адреса (2026-09-26, класс provenance, memory_salience.py)."""
    body, out = path + "\n" + text, []
    for cls, pat in _terms(root).items():
        for m in pat.finditer(body):
            i = body.count("\n", 0, m.start())
            where = "имя файла" if i == 0 else f"строка {i}"
            out.append((i, f"    {where}: [{cls}] «{' '.join(m.group(0).split())}»"))
    return [s for _, s in sorted(out)][:limit]


def _load_baseline(root: Path) -> dict[str, int]:
    p = root / BASELINE
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _write_baseline(root: Path, data: dict[str, int]) -> None:
    clean = {k: v for k, v in sorted(data.items()) if v > 0}
    (root / BASELINE).write_text(json.dumps(clean, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _show(root: Path, spec: str, missing: str | None = "") -> str | None:
    r = subprocess.run(["git", "-C", str(root), "show", spec], capture_output=True)
    return r.stdout.decode("utf-8", errors="ignore") if r.returncode == 0 else missing


def _module_files(node) -> list[str]:
    """Пути-кандидаты файлов, которые подключает import-узел AST (относительно корня)."""
    import ast
    names = []
    if isinstance(node, ast.Import):
        names = [a.name for a in node.names]
    elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
        names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
    out = []
    for m in names:
        base = m.replace(".", "/")
        out += [f"{base}.py", f"{base}/__init__.py"]
    return out


def private_imports(root: Path = ROOT, paths: list[str] | None = None,
                    read=None) -> dict[str, list[str]]:
    """Публичный .py → приватные файлы репозитория, которые он импортирует (27.09).

    В выгрузке приватной зоны нет: такой модуль или тест падает на импорте, а тест — ещё
    на сборке, роняя весь прогон. Правило «перенеси тест, зовущий plans/, в приватную зону»
    держалось на памяти и за три дня было забыто пять раз. Судится и на коммите (--staged),
    и полным деревом (tests/consistency/test_public_no_private_import.py)."""
    import ast
    globs = _zones(root)
    tracked = set(_tracked(root))
    if paths is None:
        paths = [p for p in tracked if p.endswith(".py") and not _is_private(p, globs)]
    if read is None:
        def read(p):
            return (root / p).read_text(encoding="utf-8", errors="ignore")
    found: dict[str, list[str]] = {}
    for p in paths:
        if not p.endswith(".py") or _is_private(p, globs):
            continue
        try:
            tree = ast.parse(read(p) or "")
        except SyntaxError:
            continue
        hits = sorted({f for node in ast.walk(tree) for f in _module_files(node)
                       if f in tracked and _is_private(f, globs)})
        if hits:
            found[p] = hits
    return found


def _staged(root: Path) -> int:
    """Судит ПРИРОСТ застейдженного файла против его же версии в HEAD под ТЕКУЩИМ словарём.
    Сравнение с HEAD, а не с долгом: устаревший (завышенный) долг иначе давал бы право
    вернуть убранное, а новое слово словаря — блок на давно лежащих вхождениях.
    Переименование сравнивается со старым путём — но только если старый путь был
    ПУБЛИЧНЫМ: перенос журнала из приватной зоны в публичную — это и есть утечка."""
    globs, terms = _zones(root), _terms(root)
    olds = {}
    for line in _git(root, "diff", "--cached", "--name-status", "-M", "--diff-filter=AMR").split("\n"):
        if not line:
            continue
        f = line.split("\t")
        olds[f[-1]] = f[1] if f[0].startswith("R") else f[-1]
    public = [p for p in olds if not _is_private(p, globs)]
    leaks = private_imports(root, [p for p in public if p.endswith(".py")],
                            read=lambda p: _show(root, f":{p}"))
    if leaks:
        print("⛔ pii_census: публичный модуль импортирует приватную зону — в открытой выгрузке "
              "он упадёт на импорте (тест — на сборке, уронив весь прогон).")
        for p, hits in leaks.items():
            print(f"  {p} → {', '.join(hits)}")
        print("Что сделать: тест разового скрипта из plans/ — в приватную зону (publication_zones.yaml,")
        print("  рядом с прочими tests/unit/*); проверки публичного кода — отдельным публичным тестом.")
        return 1
    baseline = _load_baseline(root)
    grew, shrank = {}, {}
    for p in public:
        now = sum(_scan(p, _show(root, f":{p}"), terms).values())
        old = olds[p]
        head = None if _is_private(old, globs) else _show(root, f"HEAD:{old}", missing=None)
        was = 0 if head is None else sum(_scan(old, head, terms).values())
        if now > was:
            grew[p] = (was, now)
        if now < baseline.get(p, 0):
            shrank[p] = now
    if grew:
        print("⛔ pii_census: в публичной зоне прибавились личные данные "
              "(решение владельца 2026-09-23: проект будет открыт на GitHub).")
        for p, (was, now) in grew.items():
            print(f"  {p}: было {was}, стало {now}")
            print("\n".join(_where(root, p, _show(root, f":{p}"))))
        print("Что сделать: обезличить (владелец/партнёр/врач/<город>), вынести в данные тенанта,")
        print("  либо — если файл по сути личный — внести его в приватную зону publication_zones.yaml с причиной.")
        print("[provenance] — это пометка автора «данные настоящие». Заменить ДАННЫЕ синтетикой и")
        print("  только потом пометку. Стереть пометку, оставив данные, — худший исход: пропадает")
        print("  единственный признак, по которому утечку видно (урок C-67).")
        return 1
    if shrank:
        baseline.update(shrank)
        _write_baseline(root, baseline)
        _git(root, "add", BASELINE)
        print(f"✓ pii_census: долг уменьшен в {len(shrank)} файл(ах), {BASELINE} до-стейджен")
    return 0


def main(argv: list[str]) -> int:
    root = ROOT
    if argv[:1] == ["--staged"]:
        return _staged(Path(_git(Path.cwd(), "rev-parse", "--show-toplevel").strip()))
    counts = census(root)
    if argv[:1] == ["rebaseline"]:
        old = _load_baseline(root)
        grew, _ = judge(counts, old, sorted(set(counts) | set(old)))
        # Рост законен ровно в одном случае: вхождения уже лежали в HEAD, а словарь их
        # раньше не видел — это новое зрение, не новая утечка. Проверяется машиной
        # (сравнение с HEAD под текущим словарём), а не флагом: флаг был бы обходом ратчета.
        terms = _terms(root)
        new_leak = {p: v for p, v in grew.items()
                    if (h := _show(root, f"HEAD:{p}", missing=None)) is None
                    or v[1] > sum(_scan(p, h, terms).values())}
        if new_leak:
            print("pii_census rebaseline: ОТКАЗ — долг только вниз; рост против HEAD:")
            for p, (was, now) in new_leak.items():
                print(f"  {p}: {was} → {now}")
            return 1
        if grew:
            print(f"pii_census rebaseline: словарь увидел старое — долг поднят в {len(grew)} файл(ах)")
        _write_baseline(root, {p: sum(c.values()) for p, c in counts.items()})
        print(f"pii_census: {BASELINE} записан — {len(counts)} файлов, {sum(sum(c.values()) for c in counts.values())} находок")
        return 0
    by_cls = collections.Counter()
    files = collections.Counter()
    for c in counts.values():
        by_cls.update(c)
        files.update(c.keys())
    for cls in sorted(by_cls):
        print(f"{cls:10s} находок {by_cls[cls]:5d}  файлов {files[cls]:4d}")
    print(f"итого: {sum(by_cls.values())} находок в {len(counts)} публичных файлах")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
