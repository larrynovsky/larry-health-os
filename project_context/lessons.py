"""Извлечённые уроки: один движок, свой lessons.yaml в корне каждого проекта.

ЗАЧЕМ ОДИН ДВИЖОК. До 28.09.2026 дом уроков был только в одном соседнем проекте (lessons.yaml +
scripts/lessons.py), в другом соседнем проекте его не было вовсе, а в health_scripts ту же роль играл
corpus в project_context.json — без причины, адреса и исхода. Ритуалы letitbe/the-end
ссылались на соседний проект по имени, и в двух проектах шаг «подай уроки» был пустым по
построению. Теперь формат и страж один, файл у каждого проекта свой, а ритуалы зовут
одну команду: `python3 -m project_context lessons ...` в корне проекта.

ЧТО ЗДЕСЬ, А ЧТО В ФАЙЛЕ ПРОЕКТА. Здесь — форма записи и три способа подачи. В файле
проекта — закрытый список видов адреса, словарь предметов, дома адресов (`homes`) и
то, какие статусы подаются на старте работы (`deliver_statuses`). Движок ничего не
знает о предмете проекта.

ПОДАЧА. Три пути, и они разные по смыслу:
- `for_subject` — старт работы по предмету: только статусы из deliver_statuses (по
  умолчанию adopted/failed — внедрённое и не сработавшее, как в соседнем проекте intent/13);
- `for_modules` — preflight по задетым модулям: ВСЕ уроки с полем modules, любого
  статуса. Так health_scripts подавал corpus, и подача не должна ослабеть от переезда;
- `groups` — сведение по (адрес, предмет), созревание по числу свидетельств.

ПРАВИЛО НЕ СРАБОТАЛО. У внедрённого урока — дата внедрения `adopted_on`; урок той же
группы, записанный позже неё, красит проверку. Обязательность даты — ключ проекта
`adoption_dates: required` (соседний проект его не ставит — паритет формы не меняется).

ГРАНИЦА. Страж проверяет ФОРМУ. Честна ли причина и меняет ли что-то названная
практика — суждение человека на выборке, машине недоступно.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

LESSONS = "lessons.yaml"
REQUIRED = ("id", "date", "thread", "event", "cost", "cause", "address", "subject",
            "outcome", "status", "evidence", "owner")
STATUSES = ("observation", "in_work", "adopted", "failed", "confirmed")
OUTCOME_KINDS = ("rule", "check", "none")
NEEDS_CHANGE = ("adopted", "failed", "confirmed")
DEFAULT_DELIVER = ("adopted", "failed")
# Причина, сведённая к исполнителю, ничего не даёт изменить (SRE: «человеческий фактор»).
BLAMEFUL = ("невнимател", "человеческий фактор", "модель ошиб", "забыл", "халатн")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Имя дома адреса — только файл в корне проекта. Путь из данных не строится: ref ищется
# КЛЮЧОМ в доме, а сам дом — плоское имя (WSTG-ATHZ-01; до 28.09 карта жила в коде
# соседнего проекта именно по этой причине — переезд в данные не должен её отменить).
HOME_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*\.ya?ml$")
RIPE = 3


def load(root) -> dict:
    return yaml.safe_load((Path(root) / LESSONS).read_text(encoding="utf-8")) or {}


def exists(root) -> bool:
    return (Path(root) / LESSONS).is_file()


def _homes(doc: dict) -> dict:
    return doc.get("homes") or {}


def _home_keys(root: Path, spec) -> set | None:
    """Ключи дома; None — дом не читается или объявлен неверно (отказ, а не пустота)."""
    if not isinstance(spec, dict) or not HOME_NAME_RE.match(str(spec.get("file", ""))):
        return None
    try:
        doc = yaml.safe_load((root / spec["file"]).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    entries = doc.get(spec.get("section")) if isinstance(doc, dict) else None
    return set(entries) if isinstance(entries, dict) else None


def _in_git(root: Path) -> bool:
    try:
        return subprocess.run(["git", "-C", str(root), "rev-parse", "--git-dir"],
                              capture_output=True).returncode == 0
    except OSError:
        return False


def _evidence_ok(root: Path, ref: str, git_ok: bool) -> bool | None:
    """Путь в репозитории либо `git:<sha>` — коммит, который несёт след.

    None — судить нечем: корень не git-репозиторий (полный прогон health_scripts идёт
    в копии без .git — урок C-66, повторённый этим же модулем 28.09 на первом прогоне).
    «Не знаю» не красится и не выдаётся за «есть»: форма sha проверяется всё равно."""
    ref = str(ref)
    if ref.startswith("git:"):
        sha = ref[4:]
        if not re.fullmatch(r"[0-9a-f]{7,40}", sha):
            return False
        if not git_ok:
            return None
        r = subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{sha}^{{commit}}"],
                           capture_output=True)
        return r.returncode == 0
    return (root / ref).exists()


def _stale_homes(root: Path, doc: dict) -> list[str]:
    """Принятая запись дома, которой коснулся урок позже подтверждения, — красная.
    Судит реестр уроков, а не календарь (соседний проект, план 2026-09-23, вопрос 3)."""
    out = []
    for kind, spec in _homes(doc).items():
        try:
            home = yaml.safe_load((root / spec["file"]).read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError, KeyError, TypeError):
            continue  # отсутствие дома ловит проверка вида адреса
        entries = (home.get(spec.get("section")) if isinstance(home, dict) else None) or {}
        for key, pr in entries.items():
            if not isinstance(pr, dict) or pr.get("status") != "accepted":
                continue
            since = str((pr.get("confirmed") or {}).get("date") or "")
            for raw in doc.get("lessons") or ():
                a = raw.get("address") or {}
                if a.get("kind") == kind and a.get("ref") == key and str(raw.get("date")) > since:
                    out.append(f"запись {key} ({spec['file']}) не пересмотрена после урока "
                               f"{raw.get('id')} ({raw.get('date')}; подтверждено: {since or 'никогда'})")
    return out


def lesson_problems(root) -> list[str]:
    root = Path(root)
    doc = load(root)
    kinds = set(doc.get("address_kinds") or ())
    vocab = set(doc.get("subjects") or {})
    out: list[str] = []
    if not kinds:
        out.append("address_kinds пуст — адрес урока некуда вести")
    homes = {}
    for kind, spec in _homes(doc).items():
        if kind not in kinds:
            out.append(f"дом объявлен для вида «{kind}», которого нет в address_kinds")
        homes[kind] = _home_keys(root, spec)
        if homes[kind] is None:
            out.append(f"вид адреса «{kind}»: дом {spec!r} не читается или назван не плоским "
                       f"именем *.yaml в корне — адрес некуда вести")
    for key in vocab:
        if not KEY_RE.match(str(key)):
            out.append(f"ключ предмета «{key}» — только латиница в нижнем регистре")
    seen: set = set()
    git_ok = _in_git(root)
    for raw in doc.get("lessons") or ():
        lid = raw.get("id", "<без id>")
        for field in REQUIRED:
            if not raw.get(field):
                out.append(f"{lid}: нет поля «{field}»")
        if lid in seen:
            out.append(f"{lid}: id не уникален")
        seen.add(lid)
        address = raw.get("address") or {}
        if address.get("kind") not in kinds:
            out.append(f"{lid}: адрес «{address.get('kind')}» не из закрытого списка {sorted(kinds)}")
        if not address.get("ref"):
            out.append(f"{lid}: у адреса нет ref — что именно менять")
        elif homes.get(address.get("kind")) is not None and address["ref"] not in homes[address["kind"]]:
            out.append(f"{lid}: ref «{address['ref']}» не заведён ключом в {_homes(doc)[address['kind']]['file']}")
        for key in raw.get("subject") or ():
            if key not in vocab:
                out.append(f"{lid}: ключ предмета «{key}» не объявлен в словаре subjects")
        mods = raw.get("modules")
        if mods is not None and not (isinstance(mods, list) and mods and all(isinstance(m, str) for m in mods)):
            out.append(f"{lid}: modules — непустой список имён модулей либо поле отсутствует")
        outcome = raw.get("outcome") or {}
        if outcome.get("kind") not in OUTCOME_KINDS:
            out.append(f"{lid}: исход «{outcome.get('kind')}» не из {list(OUTCOME_KINDS)}")
        if not outcome.get("text"):
            out.append(f"{lid}: исход без текста; «none» тоже объясняется — «потому что…»")
        if any(w in str(raw.get("cause") or "").lower() for w in BLAMEFUL):
            out.append(f"{lid}: причина сведена к исполнителю — назови, что менять в устройстве")
        status = raw.get("status")
        if status not in STATUSES:
            out.append(f"{lid}: статус «{status}» не из {list(STATUSES)}")
        if status in NEEDS_CHANGE and not raw.get("change"):
            out.append(f"{lid}: статус «{status}» без ссылки на изменение (поле change)")
        for ref in raw.get("evidence") or ():
            if _evidence_ok(root, ref, git_ok) is False:
                out.append(f"{lid}: evidence ведёт в несуществующее {ref}")
        if status == "adopted" and doc.get("adoption_dates") == "required" \
                and not DATE_RE.fullmatch(str(raw.get("adopted_on") or "")):
            out.append(f"{lid}: внедрён без даты (adopted_on: ГГГГ-ММ-ДД) — повтор после "
                       f"внедрения не увидеть")
    return out + _stale_homes(root, doc) + _repeats_after_adoption(doc)


def _repeats_after_adoption(doc: dict) -> list[str]:
    """Урок той же группы, записанный позже даты внедрения, — правило не сработало.

    Без этого failed ставится только по памяти исполнителя: статус есть, сигнала нет
    (решение 29.09, нить lessons-adopted-on). Тот же день — не повтор: уроки одной нити
    внедряются одной правкой."""
    out, seen = [], set()
    for (address, key), rows in _group(doc.get("lessons") or ()).items():
        for a in rows:
            since = str(a.get("adopted_on") or "")
            if a.get("status") != "adopted" or not since:
                continue
            for r in rows:
                if r is not a and str(r.get("date")) > since and (a.get("id"), r.get("id")) not in seen:
                    seen.add((a.get("id"), r.get("id")))
                    out.append(f"{a.get('id')}: правило не сработало — урок {r.get('id')} "
                               f"({r.get('date')}) в группе {address} · {key} записан после "
                               f"внедрения ({since}); поставь failed у внедрённых уроков группы")
    return out


def _group(lessons) -> dict[tuple[str, str], list[dict]]:
    found: dict = {}
    for raw in lessons:
        a = raw.get("address") or {}
        for key in raw.get("subject") or ():
            found.setdefault((f"{a.get('kind')}:{a.get('ref')}", key), []).append(raw)
    return found


def lesson_groups(root) -> dict[tuple[str, str], list[dict]]:
    """Свидетельства сводятся по (адрес, ключ предмета) — по данным, а не по похожести текста."""
    return _group(load(root).get("lessons") or ())


def for_subject(root, key: str) -> list[dict]:
    """Что подаётся исполнителю на старте работы по предмету."""
    doc = load(root)
    statuses = tuple(doc.get("deliver_statuses") or DEFAULT_DELIVER)
    return [r for r in doc.get("lessons") or ()
            if key in (r.get("subject") or ()) and r.get("status") in statuses]


def for_modules(root, modules) -> list[dict]:
    """Что подаётся preflight'ом по задетым модулям — любого статуса (бывший corpus)."""
    mods = set(modules)
    return [r for r in load(root).get("lessons") or () if set(r.get("modules") or ()) & mods]


def as_corpus(root) -> list[dict]:
    """Уроки с modules в форме прежнего corpus (id, modules, false_path) — для preflight."""
    return [{"id": r["id"], "modules": r["modules"], "false_path": r.get("event", "")}
            for r in load(root).get("lessons") or () if r.get("modules")]


def selftest() -> str:
    """Внесённые поломки обязаны покраснеть; синтетический корень — зелёный не может быть
    причинён настоящими файлами. Перенесено из соседнего проекта scripts/lessons.py (16 поломок)
    и дополнено тем, что появилось при обобщении движка (дома в данных, git-улика, подача)."""
    import tempfile
    good = {
        "address_kinds": ["ritual"],
        "subjects": {"stand": "стенд"},
        "lessons": [{
            "id": "L-1", "date": "2026-09-22", "thread": "t", "event": "e", "cost": "c",
            "cause": "в ритуале не записан порядок", "address": {"kind": "ritual", "ref": "the-end"},
            "subject": ["stand"], "outcome": {"kind": "rule", "text": "сначала стенд"},
            "status": "observation", "evidence": ["docs/x.md"], "owner": "владелец",
        }],
    }

    def make(tmp: Path, doc: dict) -> Path:
        subprocess.run(["git", "init", "-q", str(tmp)], capture_output=True)
        (tmp / "docs").mkdir(parents=True)
        (tmp / "docs/x.md").write_text("x\n", encoding="utf-8")
        (tmp / LESSONS).write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
        return tmp

    def write(root: Path, doc: dict) -> None:
        (root / LESSONS).write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")

    n = 0

    def red(mutate, expect: str, files: dict | None = None) -> None:
        nonlocal n
        with tempfile.TemporaryDirectory() as d:
            root = make(Path(d), yaml.safe_load(yaml.safe_dump(good)))
            for name, body in (files or {}).items():
                (root / name).write_text(body, encoding="utf-8")
            doc = load(root)
            mutate(doc)
            write(root, doc)
            got = lesson_problems(root)
            assert any(expect in p for p in got), f"не покраснело на «{expect}»: {got}"
            n += 1

    with tempfile.TemporaryDirectory() as d:
        root = make(Path(d), good)
        assert lesson_problems(root) == [], lesson_problems(root)
        assert list(lesson_groups(root)) == [("ritual:the-end", "stand")]
        assert for_subject(root, "stand") == []          # observation не подаётся по умолчанию
        doc = load(root); doc["deliver_statuses"] = ["observation"]; write(root, doc)
        assert [r["id"] for r in for_subject(root, "stand")] == ["L-1"]   # политика проекта
        assert for_modules(root, ["m"]) == [] and as_corpus(root) == []
        doc["lessons"][0]["modules"] = ["m", "k"]; write(root, doc)
        assert [r["id"] for r in for_modules(root, ["k"])] == ["L-1"]     # любой статус
        assert as_corpus(root) == [{"id": "L-1", "modules": ["m", "k"], "false_path": "e"}]

    red(lambda doc: doc["lessons"][0].pop("cost"), "нет поля «cost»")
    red(lambda doc: doc["lessons"][0]["address"].update(kind="прочее"), "не из закрытого списка")
    red(lambda doc: doc["lessons"][0]["address"].pop("ref"), "нет ref")
    red(lambda doc: doc["lessons"][0]["subject"].append("qa_stand"), "не объявлен в словаре")
    red(lambda doc: doc["lessons"][0]["outcome"].update(kind="запомним"), "не из")
    red(lambda doc: doc["lessons"][0]["outcome"].pop("text"), "без текста")
    red(lambda doc: doc["lessons"][0].update(cause="исполнитель был невнимателен"), "сведена к исполнителю")
    red(lambda doc: doc["lessons"][0].update(status="adopted"), "без ссылки на изменение")
    red(lambda doc: doc["lessons"][0].update(status="принято"), "статус")
    red(lambda doc: doc["lessons"][0].update(evidence=["docs/net.md"]), "несуществующее")
    red(lambda doc: doc["lessons"][0].update(evidence=["git:0000000"]), "несуществующее")
    red(lambda doc: doc["lessons"][0].update(evidence=["git:не-хеш"]), "несуществующее")
    red(lambda doc: doc["lessons"].append(dict(doc["lessons"][0])), "не уникален")
    red(lambda doc: doc["subjects"].update({"Стенд": "кириллица"}), "латиница")
    red(lambda doc: doc["lessons"][0].update(modules="m"), "modules")
    red(lambda doc: doc.update(address_kinds=[]), "address_kinds пуст")

    def adopted(doc, on=None):
        doc["lessons"][0].update(status="adopted", change="ritual:the-end, редакция")
        if on:
            doc["lessons"][0]["adopted_on"] = on
        return doc

    def repeated(day):
        def mutate(doc):
            adopted(doc, "2026-09-22")
            doc["lessons"].append(dict(doc["lessons"][0], id="L-2", date=day, status="observation"))
            doc["lessons"][1].pop("adopted_on")
        return mutate

    red(lambda doc: adopted(doc).update(adoption_dates="required"), "внедрён без даты")
    red(lambda doc: adopted(doc, "вчера").update(adoption_dates="required"), "внедрён без даты")
    red(repeated("2026-09-23"), "правило не сработало")
    with tempfile.TemporaryDirectory() as d:     # повтор в день внедрения — не повтор
        doc = yaml.safe_load(yaml.safe_dump(good))
        repeated("2026-09-22")(doc)
        doc["adoption_dates"] = "required"
        assert lesson_problems(make(Path(d), doc)) == [], lesson_problems(Path(d))

    home = {"products.yaml": "products:\n  venue: {title: бронь}\n"}
    spec = {"file": "products.yaml", "section": "products"}

    def addressed(ref, sp=spec):
        def mutate(doc):
            doc["address_kinds"].append("product_description")
            doc["homes"] = {"product_description": sp}
            doc["lessons"][0]["address"] = {"kind": "product_description", "ref": ref}
        return mutate

    red(lambda doc: (doc["address_kinds"].append("product_description"),
                     doc.update(homes={"product_description": spec})), "не читается")
    red(addressed("hotel"), "не заведён ключом", home)
    red(addressed("../runtime"), "не заведён ключом", home)
    red(addressed("venue", {"file": "../secrets.yaml", "section": "products"}), "не плоским", home)
    red(lambda doc: doc.update(homes={"job_card": spec}), "которого нет в address_kinds", home)
    with tempfile.TemporaryDirectory() as d:
        root = make(Path(d), yaml.safe_load(yaml.safe_dump(good)))
        (root / "products.yaml").write_text(home["products.yaml"], encoding="utf-8")
        doc = load(root)
        addressed("venue")(doc)
        write(root, doc)
        assert lesson_problems(root) == [], lesson_problems(root)
        doc["lessons"][0]["date"] = "2026-09-23"
        write(root, doc)
        accepted = "products:\n  venue: {title: бронь, status: accepted, confirmed: {date: '%s', by: владелец}}\n"
        (root / "products.yaml").write_text(accepted % "2026-09-01", encoding="utf-8")
        assert any("не пересмотрена после урока L-1" in p for p in lesson_problems(root)), lesson_problems(root)
        (root / "products.yaml").write_text(accepted % "2026-09-23", encoding="utf-8")
        assert lesson_problems(root) == [], lesson_problems(root)
        n += 1
    return f"lessons selftest: {n} поломок — {n} красных; чистый корень, дом адреса, свежее описание зелёные"


def main(argv, root=".") -> int:
    root = Path(root)
    if "--selftest" in argv:
        print(selftest())
        return 0
    if not exists(root):
        print(f"дома уроков нет: {root.resolve()}/{LESSONS} — заведи (формат: "
              f"health_scripts/docs/reference/lessons_format.md)")
        return 2
    if "--selftest" in argv:
        print(selftest())
        return 0
    if "--groups" in argv:
        for (address, key), rows in sorted(lesson_groups(root).items()):
            mark = "созрела" if len(rows) >= RIPE else f"копится ({len(rows)}/{RIPE})"
            print(f"{address} · {key}: {mark} — " + ", ".join(r["id"] for r in rows))
        return 0
    if "--subject" in argv:
        key = argv[argv.index("--subject") + 1]
        rows = for_subject(root, key)
        if not rows:
            print(f"по предмету «{key}» подавать нечего")
        for r in rows:
            print(f"{r['id']} ({r['status']}): {r['outcome']['text']} — оплачено: {r['cost']}")
        return 0
    if "--modules" in argv:
        mods = argv[argv.index("--modules") + 1].split(",")
        for r in for_modules(root, mods):
            print(f"{r['id']} ({r['status']}): {r['outcome']['text']}")
        return 0
    if "--subjects" in argv:
        for k, v in sorted((load(root).get("subjects") or {}).items()):
            print(f"{k}: {v}")
        return 0
    found = lesson_problems(root)
    for p in found:
        print("lessons:", p)
    return 1 if found else 0
