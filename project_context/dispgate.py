"""project_context.dispgate — гейт одноразовости на НОВЫХ .py (CLAUDE.md §15).

Периметр — ТОЛЬКО файлы со статусом A в стейдже. Предикат машинно-проверяем на всех новых файлах:
«переписан ли модуль» пришлось бы мерить процентом диффа. Легаси вне периметра осознанно
(его ведёт владелец отдельными сессиями), поэтому baseline-механизм не нужен вообще.
Исключение ровно одно, и оно про ОТСУТСТВИЕ ПРЕДМЕТА, а не про имя файла: `__init__.py` без
определений функций и классов. Прежний blanket-фильтр по ведущему `_` снят (DG-03): «периметр
тотален» было переоценкой — внутри него жила зона с другим путём кода, ровно та ловушка, о которой
предупреждает эвристика периметра (враг может быть уже внутри).

BLOCK (K1) сайдкар contracts/<модуль>.json есть, заявленная поверхность == фактической, блок вердикта
        заполнен, каждая зависимость в depends_on разрешается в существующий контракт либо честно
        помечена легаси с причиной. Отрицательный вердикт ПРОХОДИТ: блокирует отсутствие суждения,
        а не его знак.
BLOCK (K2) существует тест, который импортирует модуль и зовёт хотя бы одно его публичное имя —
        СВЯЗАННЫМ AST-вызовом (`<алиас модуля>.<имя>(…)` или прямо импортированное имя). Подстрока
        засчитывала комментарий, строку и одноимённый метод чужого объекта (DG-02).
        ЧЕСТНАЯ СИЛА K2: это СИНТАКСИЧЕСКОЕ свидетельство. Гейт не знает, соберёт ли pytest файл,
        достижима ли ветка, не затенено ли имя к моменту вызова и есть ли в тесте хоть один
        ассерт. Усиливать бесполезно: вызов без ассертов законен по любой проверке связанности,
        а это и есть главный реальный сценарий плохой характеризации (ревью 2026-07-27, F-03).
BLOCK (K3) модуль не лезет в чужие `_private` — ни `from M import _x`, ни `M._x`. Сиблинги внутри
        одного пакета чужими НЕ считаются: пакет — одна одноразовая единица.
WARN  (K4) публичная поверхность и число строк выше порогов политики. Никогда не блок: числа —
        ориентир, а блок по размеру воспроизводил бы ловушку «больше модулей = больше стыков».

ЧЕСТНАЯ ГРАНИЦА. K1-K4 — это CHECKS: ловят ОТСУТСТВИЕ носителя, не его качество. «Тест есть и зовёт
публичное имя» — присутствие, не поведение. Сам вердикт одноразовости — TEST с tacit-оракулом и
машинно не выводится ПРИНЦИПИАЛЬНО (Бах Ch.3 §6), поэтому подписывает его человек или агент, а гейт
проверяет лишь, что суждение вынесено и записано. Пункты D1 и D6 чек-листа не проверяются никак.

Включение гейта — только при зелёных позит+негат контролях (tests/unit/test_dispgate.py).
Замысел и почему так: docs/explanation/disposable_modules_refactor.md; план — plans/PLAN_disposability_gate_2026-07-26.md.
"""
import ast, datetime, os
# INTENT: disposability_gate — замысел и инварианты: subsystem_intent.yaml (тёплый слой: docs/explanation/disposability_gate.md)
from project_context import indexer, staged

VERDICTS = ("disposable", "not_disposable")
_REQUIRED = ("verdict", "rationale", "date", "oracle")


def _module_publics(tree):
    """Фактическая публичная поверхность: МОДУЛЬНЫЕ функции без `_`. Методы классов — интерфейс
    объекта, не поверхность модуля (та же граница, что у дубль-гейта)."""
    if tree is None:
        return set()
    return {n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")}


def _same_package(a, b):
    """Сиблинги одного пакета? Только если ОБА вложены и лежат в одном каталоге. Корневые модули
    пакетом не являются — иначе K3 обнулился бы для большей части репозитория."""
    return "/" in a and "/" in b and a.rsplit("/", 1)[0] == b.rsplit("/", 1)[0]


def _alias_map(tree, ix):
    """alias → ключ модуля проекта, для формы `M._x`. Однозначность обязательна: коллизию стемов
    не резолвим (консервативно, как indexer)."""
    out = {}
    bs = ix.get("by_stem", {})

    def _res(stem):
        if stem in ix["PROJ"]:
            return stem
        c = bs.get(stem, ())
        return next(iter(c)) if len(c) == 1 else None

    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                k = _res(a.name.split(".")[-1])
                if k:
                    out[(a.asname or a.name).split(".")[0]] = k
        elif isinstance(n, ast.ImportFrom) and n.module:
            for a in n.names:
                k = _res(a.name) or _res(f"{n.module}/{a.name}".replace(".", "/").split("/")[-1])
                cand = f"{n.module.replace('.', '/')}/{a.name}"
                if cand in ix["PROJ"]:
                    k = cand
                if k:
                    out[a.asname or a.name] = k
    return out


def _foreign_private(tree, key, ix):
    """Обращения к `_private` чужого модуля: `from M import _x` и `M._x`. Дандеры не считаем."""
    hits = []
    if tree is None:
        return hits
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            target = n.module.replace(".", "/")
            if target not in ix["PROJ"]:
                cands = ix.get("by_stem", {}).get(n.module.split(".")[-1], ())
                target = next(iter(cands)) if len(cands) == 1 else None
            if not target or target == key or _same_package(key, target):
                continue
            hits += [f"from {n.module} import {a.name}" for a in n.names
                     if a.name.startswith("_") and not a.name.endswith("__")]
    amap = _alias_map(tree, ix)
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            t = amap.get(n.value.id)
            if not t or t == key or _same_package(key, t):
                continue
            if n.attr.startswith("_") and not n.attr.endswith("__"):
                hits.append(f"{n.value.id}.{n.attr}")
    return sorted(set(hits))


def _verdict_problems(sc, policy):
    """Чего не хватает блоку вердикта. Отрицательный вердикт легален — нелегально его отсутствие."""
    d = (sc or {}).get("disposability")
    if not isinstance(d, dict):
        return ["блок disposability отсутствует или не объект"]
    bad = [f"нет поля {f}" for f in _REQUIRED if not str(d.get(f) or "").strip()]
    if d.get("verdict") and d["verdict"] not in VERDICTS:
        bad.append(f"verdict вне {VERDICTS}")
    if d.get("verdict") == "not_disposable" and not str(d.get("cause") or "").strip():
        bad.append("not_disposable без cause: без стабильного ключа причину нельзя посчитать по неделям")
    ids = [i["id"] for i in policy.get("items", [])]
    miss = [i for i in ids if not str((d.get("items") or {}).get(i) or "").strip()]
    if miss:
        bad.append("пункты без ответа: " + ",".join(miss))
    return bad


def _defines_anything(tree):
    """Есть ли на уровне модуля определения функций или классов.

    Единственное исключение из периметра «каждый новый .py» и потому нарочно узкое и машинное:
    `__init__.py` без определений — ре-экспортный шов пакета, у него нет поверхности, которую
    можно объявить, и нет поведения, которое можно характеризовать. Появилось определение —
    файл судится как обычный модуль, без поблажки за имя. Ср. DG-03: прежний фильтр выводил
    из периметра ЛЮБОЙ `__init__.py` и любой `_*.py`, то есть освобождал по имени, а не по
    отсутствию предмета суждения.

    Обход ВСЕГО дерева, не `tree.body`: норма говорит «без определений», а не «без определений на
    первом уровне», и `if True: def live_entry(): ...` освобождал файл от суждения
    (ревью 2026-07-27, F-04)."""
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
               for n in ast.walk(tree)) if tree else False


def _depends_problems(sc, sidecars):
    """Referential integrity `depends_on`: объявленная зависимость обязана либо иметь сайдкар,
    либо честно нести пометку легаси с причиной.

    DG-04 нашёлся на СОБСТВЕННОМ сайдкаре гейта: он ссылался на `contracts/project_context/
    indexer.json`, которого не существовало. Строка в `depends_on` — прокси существующего
    контракта, а не сам контракт: «переписать, имея только контракт и его тесты» неисполнимо,
    если соседний контракт — мираж.

    Пометка `legacy` намеренна и не является люком: она не освобождает от объявления зависимости,
    а требует НАЗВАТЬ, почему контракта нет. Без неё чужой технический долг блокировал бы честную
    работу — и правило начали бы обходить целиком, что хуже."""
    out = []
    dep = (sc or {}).get("depends_on")
    if dep is None:
        return ["нет поля depends_on: пустой список — законный ответ, отсутствие — нет"]
    if not isinstance(dep, list):
        return ["depends_on должен быть списком"]
    for i, d in enumerate(dep):
        # Тип, а не truthiness: `{"module": 7}` проходил проверку на непустоту и падал строкой
        # ниже на .strip(), а общий CLI-except делал падение разрешением (ревью 2026-07-27, F-05).
        if not isinstance(d, dict) or not isinstance(d.get("module"), str) or not d["module"].strip():
            out.append(f"depends_on[{i}]: поле module обязано быть непустой строкой")
            continue
        m = d["module"].strip()
        if m in sidecars:
            continue
        lg = d.get("legacy")
        if lg is not None and (not isinstance(lg, str) or not lg.strip()):
            out.append(f'depends_on[{i}]: пометка legacy обязана быть непустой строкой-ПРИЧИНОЙ; '
                       f'{lg!r} причиной не является')
            continue
        if isinstance(lg, str) and lg.strip():
            continue
        out.append(f'depends_on: «{m}» — контракта contracts/{m}.json не существует. Заведи его '
                   f'либо пометь "legacy": "<почему контракта пока нет>"')
    return out


_UNSET_PERIM = object()
_PERIMETER_CACHE = {}

# Как называть человеку проектный файл в сообщениях об отказе. Не путь: строгий читатель
# судит одноразовый снимок индекса, и путь вёл в `/tmp/pc-index-*`, удалённый к моменту,
# когда человек читает сообщение (раунд 2, F-R2-05).
_PROJ_FILE = "project_context.json (судится версия из индекса)"


def perimeter_policy(root="."):
    """Два требования — два периметра (решение владельца 2026-07-29, ред. 2).

    K1 (контракт и вердикт) спрашивается с ЛЮБОГО нового Python-исходника: проба, оснастка,
    миграция — всё, что человек написал и положил в репозиторий, обязано объяснить себя.
    K2 (характеризационный тест) спрашивается только с того, что система исполняет в штатной
    работе. До этой правки оба делили один периметр, и файл, которому не нужен тест, уходил
    из-под суда ЦЕЛИКОМ, вместе с обязанностью объясниться.

    Данные — в `project_context/perimeter.json` (§9: домов у списка не два). Здесь только
    подъём и применение. Файл не читается → возвращаем None, и вызывающий обязан трактовать
    это как отказ инструмента суждения, а не как разрешение (симметрия с `disposability.json`).

    ПРОЕКТНЫЙ СЛОЙ (2026-09-12). Движок домен-агностичен везде, кроме этого файла: карта
    каталогов и имя канона написаны под health_scripts. Второму проекту это стоило гейта —
    в соседнем проекте миграции и оснастки живут в `scripts/`, каталога с таким именем в движковой
    карте нет вовсе, и каждый новый файл там требовал бы характеризационный тест, то есть
    заглушку ради зелёного (замер 12.09: 11 ложных блоков из 36 новых файлов за месяц).

    Поэтому `<root>/project_context.json::perimeter` может ДОБАВИТЬ свои каталоги и назвать
    свой канон — тем же приёмом, каким проект уже переопределяет числа политики
    одноразовости (`indexer.disposability_policy`).

    Границы проектного слоя, и они не косметические:
      - классы остаются ЗАКРЫТЫМ списком движка. Каталог с классом не из списка НЕ
        освобождается вовсе — fail-closed: неизвестная причина строже, чем никакой;
      - движковые каталоги проект перекрыть не может: `judgement_harness` у `tests/`
        останется, чем бы проект его ни назвал. Иначе освобождение стало бы переносимым;
      - ратчет границы (`test_dispgate_perimeter`) сверяет ДВИЖКОВЫЕ каталоги с
        `indexer.SKIP` и этим слоем не затрагивается: проектные каталоги в SKIP не попадают.
    """
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "perimeter.json")
    key = (p, os.path.abspath(root))
    if key not in _PERIMETER_CACHE:
        try:
            import json
            with open(p, encoding="utf-8") as fh:
                doc = json.load(fh)
            classes = doc["_criterion"]["classes"]
            dirs = {k: v["class"] for k, v in doc["dirs"].items()}
            canon = dict(doc.get("_canon_access") or {})

            proj = _project_perimeter(root)
            never = set(canon.get("never_overrides") or ())
            for name, rec in (proj.get("dirs") or {}).items():
                klass = (rec or {}).get("class", "")
                # ТРЕТЬЯ граница слоя (раунд 2, F-R2-06). Классы из `never_overrides` — те, чьё
                # освобождение не снимает даже касание канона. Проект, назначив такой класс
                # СВОЕМУ каталогу, получал освобождение сильнее любого движкового: файл не
                # читался вовсе, и канон до него не дотягивался. Прежний claim перечислял два
                # «не может» и молчал про это — тот же класс занижения, из которого вырос F-01:
                # запись, которая преуменьшает возможности проекта, выключает поиск.
                if klass in never:
                    raise indexer.ProjectManifestUnusable(
                        f"{_PROJ_FILE}: perimeter.dirs[{name!r}].class = {klass!r} — этот класс "
                        "проект своим каталогам назначать не может: его освобождение не снимается "
                        "касанием канона, то есть было бы сильнее любого движкового")
                if name in dirs or klass not in classes:
                    continue          # движковое не перекрывается; неизвестный класс не освобождает
                dirs[name] = klass
            pc = proj.get("canon") or {}
            for fld in ("modules", "callables"):
                extra = pc.get(fld)
                if isinstance(extra, list) and extra:
                    canon[fld] = sorted(set(canon.get(fld) or ()) | set(map(str, extra)))

            # Имена, пришедшие ОТ ПРОЕКТА, отдельно от слитых: их разрешимость проверяет
            # `check` против индекса снимка (F-R2-07). Движковые имена здесь не судятся —
            # движок может стоять пакетом в проекте, где `health_db` законно отсутствует.
            _PERIMETER_CACHE[key] = {"classes": classes, "dirs": dirs, "canon": canon,
                                     "project_canon_modules": [str(x) for x in
                                                               (pc.get("modules") or ())]}
        except indexer.ProjectManifestUnusable:
            # НЕ гасим в None и НЕ кэшируем. None здесь значит «движковая таблица нечитаема» —
            # блок всему без разбора, включая тесты, и без единого слова о причине. Негодный
            # ПРОЕКТНЫЙ файл — другое событие: причина известна и называема, и общий except
            # CLI превращает её в блок с текстом (`internal_error_blocks`). Свалить два разных
            # отказа в один код возврата значило бы отнять у человека имя файла и опечатки.
            raise
        except Exception:
            _PERIMETER_CACHE[key] = None
    return _PERIMETER_CACHE[key]


def _project_perimeter(root) -> dict:
    """`<root>/project_context.json::perimeter` или пустой словарь; негодный вход — исключение.

    Файл читаем ЧУЖИМ домом (`indexer.project_manifest`): свой try/except был бы четвёртой
    редакцией одной и той же «безопасной стороны» (§9). Отсутствие файла и отсутствие ключа
    `perimeter` — законная пустота: проект без своей карты судится движковой, как и раньше.

    НЕГОДНЫЙ ВХОД — НЕ ПУСТОТА (внешнее ревью 12.09.2026, F-01). Первая редакция считала, что
    «нет политики» всегда строже, чем «есть политика», и потому глотала битый json. Неверно:
    проектный слой не только ДОБАВЛЯЕТ освобождения, он ещё и УСИЛИВАЕТ строгость, называя
    канон проекта. Потеря канона при сохранившемся движковом освобождении каталога снимала K2
    с миграции, импортирующей канон, — воспроизведено до настоящего успешного `git commit`.
    Поэтому негодный файл и негодная ФОРМА (перечислены ниже) поднимают исключение, которое
    CLI превращает в блок с названной причиной — тот самый путь `internal_error_blocks`.

    Типы проверяются здесь, а не в ридере: ридер знает про файл, форму периметра знает его
    потребитель. Второго JSON-ридера при этом не заводится."""
    per = indexer.project_manifest(root, strict=True, label=_PROJ_FILE).get("perimeter")
    if per is None:
        return {}
    if not isinstance(per, dict):
        raise indexer.ProjectManifestUnusable(
            f"{_PROJ_FILE}: perimeter: {type(per).__name__}, а не объект — "
            "политику периметра не прочесть")
    dirs = per.get("dirs")
    if dirs is not None:
        if not isinstance(dirs, dict):
            raise indexer.ProjectManifestUnusable(
                f"{_PROJ_FILE}: perimeter.dirs: {type(dirs).__name__}, а не объект")
        # ЗНАЧЕНИЯ, а не только сам словарь (раунд 2, F-R2-03). `dirs` проверялся, его записи —
        # нет: строка вместо объекта улетала в общий `except` двумя кадрами выше, тот отдавал
        # None, и человек получал блок с именем ДВИЖКОВОГО perimeter.json — целого файла из
        # чужого репозитория. Суждение не слабело (None блокирует всё), но чинить звали не туда.
        for name, rec in dirs.items():
            if not isinstance(rec, dict):
                raise indexer.ProjectManifestUnusable(
                    f"{_PROJ_FILE}: perimeter.dirs[{name!r}]: {type(rec).__name__}, а не объект — "
                    "у записи каталога нет класса, судить по ней нечем")
    canon = per.get("canon")
    if canon is not None:
        if not isinstance(canon, dict):
            raise indexer.ProjectManifestUnusable(
                f"{_PROJ_FILE}: perimeter.canon: {type(canon).__name__}, а не объект")
        for fld in ("modules", "callables"):
            val = canon.get(fld)
            if val is None:
                continue
            if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
                raise indexer.ProjectManifestUnusable(
                    f"{_PROJ_FILE}: perimeter.canon.{fld}: не список строк — "
                    "имена канона не прочесть")
        # ПУСТАЯ секция канона — тоже опечатка, а не решение (F-R2-07). `{"canon": {}}` и
        # `{"canon": {"modules": []}}` проходили проверку типов и молча оставляли проект без
        # признака касания канона: движковые имена (`health_db`) в чужом репозитории не значат
        # ничего. Отличить «имена стёрли» от «канона нет» машина может ровно одним способом —
        # по наличию самой секции: её написали намеренно, значит намеревались что-то назвать.
        if not (canon.get("modules") or canon.get("callables")):
            raise indexer.ProjectManifestUnusable(
                f"{_PROJ_FILE}: perimeter.canon: секция есть, но не названо ни одного имени — "
                "признак касания канона у проекта мёртв. Канона правда нет — убери секцию")
    return per


def unresolved_canon_names(ix, names):
    """Имена проектного канона, которым в индексе не соответствует НИ ОДИН модуль.

    ЗАЧЕМ (раунд 2, F-R2-07). Весь гейт соседний проект висит на одной строке `"models"`. Опечатка в
    ней — `"modelz"`, `[]`, `{}` — формально годна по типам и молча снимает строгость: канон
    перестаёт опознаваться, миграция, которая его импортирует, проходит без характеризации.
    Для ЧИСЕЛ политики движок такую опечатку уже ловит (`disposability_policy` отдаёт
    `unknown` — «опечатка в проектном файле иначе тихо не сработала бы»); для ИМЁН аналога
    не было, хотя имя резолвится против готового индекса тем же приёмом, что импорты.

    ГРАНИЦА. Резолв — по индексу СНИМКА: модуль, которого в репозитории нет вовсе, здесь
    неотличим от опечатки, и это верно — оба означают, что имя канона ничего не защищает."""
    known = set(ix.get("PROJ") or ()) | set((ix.get("by_stem") or {}).keys())
    return sorted({str(n) for n in (names or ())} - known)


def class_for(relpath, pol):
    """Класс освобождения по ВНЕШНЕМУ совпадению компонента пути, либо None.

    Внешнему, а не любому: `tests/snapshots/x.py` выпал из-за `tests`, и судить его как
    `snapshots` было бы приписыванием чужой причины (поймано ратчетом границы 2026-07-29)."""
    if not pol:
        return None
    for part in relpath.split("/")[:-1]:
        cls = pol["dirs"].get(part)
        if cls:
            return cls
    return None


def touches_canon(tree, pol):
    """Способен ли модуль дотянуться до боевой базы — машинный признак (ред. 3, 2026-07-29).

    ЗАЧЕМ. Строгость привязана к ВЛИЯНИЮ, а не к раскладке каталогов: файл, умеющий писать в
    `health.db`, опасен одинаково, где бы он ни лежал, и «это же черновик» канон не защищает.
    Замер 2026-07-29: из семи файлов в `plans/` канона касаются три — то есть признак
    РАЗЛИЧАЕТ, а не красит папку целиком.

    ШИРОКО НАМЕРЕННО. `read_only=True` тоже считается касанием: отличить чтение от записи по
    импорту нельзя, а ошибаться здесь безопаснее в сторону строгости. Имена модулей и вызовов —
    данные (`perimeter.json::_canon_access`), не литералы в коде (§9).

    ЧЕГО НЕ ЛОВИТ. Динамический импорт (`importlib`, `__import__("health_db")`) и запуск через
    subprocess. Названо вслух в данных как `known_hole`: закрыть значило бы исполнять судимый
    код прямо на pre-commit."""
    if tree is None or not pol:
        return False
    cfg = pol.get("canon") or {}
    mods, calls = set(cfg.get("modules") or ()), set(cfg.get("callables") or ())
    if not mods and not calls:
        return False
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            if any(a.name.split(".")[0] in mods for a in n.names):
                return True
        elif isinstance(n, ast.ImportFrom):
            if (n.module or "").split(".")[0] in mods:
                return True
            if any(a.name in calls for a in n.names):
                return True
        elif isinstance(n, ast.Call):
            f = n.func
            name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
            if name in calls:
                return True
    return False


def demands_for(relpath, pol, tree=None):
    """Что спрашивается с этого файла: (k1, k2). Неизвестный путь и класс → спрашивается всё.

    Освобождение по каталогу действует, ПОКА файл не касается канона. Коснулся — освобождение
    снимается, но не у всех классов: `judgement_harness` (тесты) не перекрывается никогда, иначе
    123 тестовых файла, импортирующих `health_db`, потребовали бы сайдкар — та же рекурсия, от
    которой класс и заведён. Список перекрываемых — данные (`_canon_access`), не суждение кода."""
    if not pol:
        return True, True
    cls = class_for(relpath, pol)
    if cls is None:
        return True, True
    rec = pol["classes"].get(cls) or {}
    k1, k2 = bool(rec.get("k1", True)), bool(rec.get("k2", True))
    if k1 and k2:
        return k1, k2
    overridable = set((pol.get("canon") or {}).get("overrides_exemption_for") or ())
    if cls in overridable and touches_canon(tree, pol):
        return True, True
    return k1, k2


KINDS = ("module", "probe")
_VERDICT_RED = "ПОКРАСНЕЛ"
_VERDICT_GREEN = "НЕ покраснел"


def _probe_evidence_problems(sc):
    """K2' — чем ПРОБА заменяет характеризационный тест (решение владельца 2026-07-29, Р-6).

    ЗАЧЕМ ОТДЕЛЬНЫЙ РЕЖИМ. Проба доказывает свойство ЖИВОЙ системы, а не поведение функции.
    Требовать с неё тест, который импортирует её и зовёт публичное имя, — значит требовать
    заглушку: тест ради зелёного, ровно та имитация, против которой построен K2. Но и отпускать
    пробу без носителя доказательства нельзя — тогда «доказано» держится на честном слове.
    Поэтому у пробы другой носитель: ИСПОЛНЕННЫЙ негативный контроль. Проба обязана предъявить
    мутации, каждая из которых ломает ровно одно звено, и все обязаны покраснеть.

    ЧЕСТНАЯ СИЛА. Гейт проверяет, что квитанция ПОЛНА и внутренне непротиворечива: оснастка
    названа, дата разбирается как дата, мутаций хотя бы одна, у каждой тег/утверждение/вердикт,
    теги уникальны, ни одного «НЕ покраснел». Гейт НЕ может проверить, что прогон был: квитанцию
    можно написать руками. Это тот же класс, что `legacy`-пометка в K1 и «K2 доказывает синтаксис,
    не исполнение» — названо вслух в §15, а не спрятано. Дата НЕ сравнивается с сегодняшним днём
    сознательно: у гейта нет своего времени, а тянуть в него `_time_inject` значило бы дать
    pre-commit-инструменту зависимость от контракта единого времени ради проверки свежести,
    которая по существу принадлежит реестру замысла (там у доказательства есть срок годности).

    ЗАЧЕМ ЭТО НЕ ЛЮК. Режим включается полем `kind: "probe"` в сайдкаре, то есть заявляется
    автором. Уйти сюда от K2 можно, но невыгодно: исполненный негативный контроль — работа
    БОЛЬШАЯ, чем тест-заглушка. Возможность всё же есть и она названа как известный обход."""
    nc = (sc or {}).get("negative_control")
    if not isinstance(nc, dict):
        return ["kind=probe, но нет блока negative_control — проба без исполненного негативного "
                "контроля не носитель доказательства, а заявление о нём"]
    out = []
    if not str(nc.get("harness") or "").strip():
        out.append("negative_control.harness пуст — не названа оснастка, которой проверяли")
    ran = str(nc.get("ran_at") or "").strip()
    if not ran:
        out.append("negative_control.ran_at пуст — неизвестно, когда контроль исполнялся")
    else:
        try:
            datetime.date.fromisoformat(ran)
        except ValueError:
            out.append(f"negative_control.ran_at={ran!r} не разбирается как дата (ГГГГ-ММ-ДД)")
    muts = nc.get("mutations")
    if not isinstance(muts, list) or not muts:
        out.append("negative_control.mutations пуст — контроль без единой мутации ничего не ломал")
        return out
    seen = set()
    for i, m in enumerate(muts):
        if not isinstance(m, dict):
            out.append(f"mutations[{i}] не объект")
            continue
        tag = str(m.get("tag") or "").strip()
        if not tag:
            out.append(f"mutations[{i}]: нет тега — мутацию не на что сослаться")
        elif tag in seen:
            out.append(f"mutations[{i}]: тег {tag!r} повторяется — две разные мутации под одним "
                       f"именем неразличимы в квитанции")
        else:
            seen.add(tag)
        if not str(m.get("statement") or "").strip():
            out.append(f"mutations[{i}] ({tag or '?'}): нет утверждения — что именно ломали")
        v = m.get("verdict")
        if v == _VERDICT_GREEN:
            out.append(f"mutations[{i}] ({tag or '?'}): вердикт «{_VERDICT_GREEN}» — мутация прошла "
                       f"незамеченной. Это НАХОДКА, а не квитанция: звено не стережётся")
        elif v != _VERDICT_RED:
            out.append(f"mutations[{i}] ({tag or '?'}): вердикт {v!r} вне "
                       f"{{{_VERDICT_RED!r}, {_VERDICT_GREEN!r}}}")
    return out


def evaluate_new_modules(ix, added, tests, sidecars, policy, perimeter=_UNSET_PERIM):
    """Чистая логика (контроли инжектят источники). added/tests: {relpath: source}.
    Возврат (blocks, warns) — списки строк для вывода.

    `perimeter` — таблица двух периметров (см. `perimeter_policy`). По умолчанию поднимается
    из пакета; контроли передают свою. None означает «таблица не прочитана» — тогда с каждого
    файла спрашивается ВСЁ: отказ инструмента суждения не может ослаблять требования."""
    if perimeter is _UNSET_PERIM:
        perimeter = perimeter_policy(ix.get("root", ".") if isinstance(ix, dict) else ".")
    blocks, warns = [], []
    if perimeter is None:
        blocks.append("периметр НЕ прочитан (project_context/perimeter.json) — без него неизвестно, "
                      "с кого что спрашивать. Отказ инструмента суждения: судим по строгому и "
                      "блокируем, а не пропускаем")
    if policy.get("degraded"):
        # Раньше это был WARN, и проба DG-15 показала цену: удаление disposability.json снимало
        # требование ответов по D1..D6 — K1 вырождался в «четыре поля непустые», а единственный
        # признак тонул среди прочих warns. Отказ ИНСТРУМЕНТА суждения — не пропуск ритуала:
        # безопасного авто-действия нет, но остановить вредное верно, поэтому fail-closed
        # (§13 ступень 2), а не эскалация и не тихий пропуск.
        blocks.append("движковый чек-лист НЕ прочитан (project_context/disposability.json) — без "
                      "него пункты D1..D6 перестают требоваться и гейт судит по пустой "
                      "методологии. Это отказ инструмента суждения, а не пропуск ритуала")
    for path in sorted(added):
        key = path[:-3]
        tree = None
        try:
            tree = ast.parse(added[path] or "")
        except Exception:
            blocks.append(f"{path}: не парсится — контракт и вердикт проверить невозможно")
            continue
        if os.path.basename(path) == "__init__.py" and not _defines_anything(tree):
            continue        # узкое машинно-проверяемое исключение: судить нечего (см. _defines_anything)
        want_k1, want_k2 = demands_for(path, perimeter, tree)
        if not want_k1 and not want_k2:
            continue                                     # с этого класса не спрашивается ничего
        pub = _module_publics(tree)
        sc = sidecars.get(key)

        # K1 — сайдкар, совпадение поверхности, полнота вердикта
        if not want_k1:
            pass
        elif not sc:
            blocks.append(f"{path}: нет сайдкара contracts/{key}.json — модуль без объявленного "
                          f"контракта нельзя безопасно переписать (D2)")
        else:
            declared = set(sc.get("public") or [])
            undeclared, phantom = sorted(pub - declared), sorted(declared - pub)
            if undeclared:
                blocks.append(f"{path}: публичные вне контракта: {undeclared} — объяви или сделай _private")
            if phantom:
                blocks.append(f"{path}: контракт обещает несуществующее: {phantom}")
            for p in _verdict_problems(sc, policy):
                blocks.append(f"contracts/{key}.json: {p}")
            for p in _depends_problems(sc, sidecars):
                blocks.append(f"contracts/{key}.json: {p}")

        # K2 — характеризация. У модуля это тест; у пробы — исполненный негативный контроль.
        # Классы с k2=false до этой ветки не доходят: с них тест не спрашивается по построению,
        # и режим `kind` для них — способ ДОБРОВОЛЬНО предъявить негативный контроль, а не
        # обязанность. Обязателен он только там, где k2=true и автор объявил kind=probe.
        # `or`, а не второй аргумент get: с 2026-07-29 ридер ПЕРЕНОСИТ поле `kind` всегда,
        # и у обычного модуля оно приезжает как None. `get("kind", "module")` тогда вернул бы
        # None (ключ-то есть), None не входит в KINDS, и блокировался бы каждый модуль без
        # объявленного режима — поймано полным прогоном сразу после починки ридера.
        # Умолчание «не объявлен → module» — решение судьи, а не ридера: ридер обязан
        # передавать то, что написано в файле, включая «ничего».
        kind = (sc or {}).get("kind") or "module"
        if want_k2 or kind == "probe":
            if kind not in KINDS:
                blocks.append(f"contracts/{key}.json: kind={kind!r} вне {list(KINDS)} — режим "
                              f"строгости выбирается из закрытого списка, а не придумывается на месте")
                kind = "module"                          # неизвестный режим судим по строгому
            if kind == "probe":
                for p in _probe_evidence_problems(sc):
                    blocks.append(f"contracts/{key}.json: {p}")
            else:
                names = pub or _declared_names(sc)       # модуль из одних классов → берём контракт
                callers = [t for t, src in tests.items() if _characterising_call(src, key, names)]
                if not callers:                          # K2 независим от K1: свой сигнал, своя строка
                    blocks.append(f"{path}: нет теста, который импортирует модуль и зовёт его "
                                  f"публичное имя (D5) — без характеризации модуль нельзя "
                                  f"безопасно убить")

        # K3 — чужие приватные
        fp = _foreign_private(tree, key, ix)
        if fp:
            blocks.append(f"{path}: лезет в чужие приватные: {fp} — зависимость на внутренностях "
                          f"соседа, а не на его контракте (D3)")

        # K4 — только предупреждения
        lim = policy.get("limits", {})
        if lim.get("public_surface_warn") and len(pub) > lim["public_surface_warn"]:
            warns.append(f"{path}: публичных функций {len(pub)} > {lim['public_surface_warn']} — "
                         f"кандидат на разбиение (D4, ориентир не догма)")
        n_lines = len((added[path] or "").splitlines())
        if lim.get("module_lines_warn") and n_lines > lim["module_lines_warn"]:
            warns.append(f"{path}: строк {n_lines} > {lim['module_lines_warn']} — модуль может не "
                         f"влезть в рабочий контекст целиком (D4)")
    return blocks, warns


def _declared_names(sc):
    """Имена для поиска в тестах, когда фактическая поверхность пуста (модуль из одних классов)."""
    return set((sc or {}).get("public") or ())


def _dotted(node):
    """Полное точечное имя выражения: `Name` → `a`, `Attribute` → `a.b.c`. Иначе None (вызов
    результата выражения — `f()(x)`, `obj[0].m()` — характеризацией не считаем)."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _characterising_call(src, key, names):
    """Зовёт ли тест публичное имя ИМЕННО этого модуля — по СВЯЗАННОМУ вызову, не по подстроке.

    Подстрока `f"{n}("` засчитывала за характеризацию комментарий, строковый литерал, мёртвый текст
    и одноимённый метод чужого объекта (DG-02: `other.do_thing(1)` при `other = SomeClass()`).
    Здесь имя вызова обязано быть привязано к импорту ЭТОГО модуля: либо `<алиас модуля>.<имя>(…)`,
    либо `<имя>(…)` при прямом `from <наш модуль> import <имя>`.

    Связывание СТРОГОЕ — только по полному точечному пути модуля, без резолва по стему. Стем
    неоднозначен (в соседнем проекте пять `runner.py`), а ошибка в пользу «зачесть» ослабляет гейт молча;
    ошибка в пользу «не зачесть» видна автору сразу и правится одной строкой импорта. Формы,
    которые распознаются: `import pkg.mod` · `import pkg.mod as m` · `from pkg import mod` ·
    `from pkg import other, mod` · `from pkg.mod import fn`.

    Непарсящийся тест → False: молчаливого зачёта не даём.
    """
    try:
        tree = ast.parse(src or "")
    except Exception:
        return False
    dot = key.replace("/", ".")
    mods, funcs = set(), set()          # алиасы САМОГО модуля · прямо связанные публичные имена
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == dot:
                    mods.add(a.asname or a.name)
        elif isinstance(n, ast.ImportFrom) and n.module:
            for a in n.names:
                if f"{n.module}.{a.name}" == dot:
                    mods.add(a.asname or a.name)
                elif n.module == dot and a.name in names:
                    funcs.add(a.asname or a.name)
    accepted = {f"{m}.{n}" for m in mods for n in names} | funcs
    if not accepted:
        return False
    return any(isinstance(n, ast.Call) and _dotted(n.func) in accepted for n in ast.walk(tree))


def check(root="."):
    """IO-обёртка. ВСЁ судимое читается из ОДНОГО снимка индекса (project_context.staged): новый
    модуль, сайдкары, тесты, индекс модулей и проектная политика. Рабочее дерево гейт не открывает
    вообще — до 2026-07-26 открывал, и это давало DG-01 (частичный стейдж проходит) и DG-14 (K3
    промахивается по чужому модулю, удалённому из дерева).

    Исключение ровно одно и оно намеренное: движковый чек-лист `disposability.json` читается из
    установленного ПАКЕТА (`disposability_policy` берёт его от `__file__`), потому что это
    инструмент суждения, а не судимое содержимое. Иначе проект с пакетом вне репозитория перестал
    бы судить вовсе. Отказ самого чек-листа теперь блокирует — см. `evaluate_new_modules`.

    git недоступен → ([],[]) fail-open: поломка окружения не блокирует коммит (симметрия
    dupgate/check-precommit). Это политика семьи из трёх гейтов; менять её в одиночку значило бы
    получить три семантики отказа (DG-13, вне объёма ремонта)."""
    with staged.snapshot(root) as snap:
        if not snap.ok or not snap.added:
            return [], []
        sroot = snap.root
        ix = indexer.build(sroot)
        sidecars, bad = indexer.contract_sidecars(sroot)
        policy = indexer.disposability_policy(sroot)
        _perim = perimeter_policy(sroot)
        # Имя канона, которое ничему не соответствует, — та же опечатка, что в числах политики,
        # но молчаливая: строгость снимается, и ни одна проверка типов этого не видит (F-R2-07).
        # Судим против индекса снимка, то есть против той же версии, что и всё остальное.
        _bad_canon = unresolved_canon_names(ix, (_perim or {}).get("project_canon_modules") or ())
        if _bad_canon:
            raise indexer.ProjectManifestUnusable(
                f"{_PROJ_FILE}: perimeter.canon.modules — в индексе нет модулей с именами: "
                f"{', '.join(_bad_canon)}. Имя, которое не резолвится, ничего не защищает: "
                "касание канона перестаёт опознаваться, и характеризацию никто не спросит")
        added = {}
        for f in snap.added:
            if not f.endswith(".py"):
                continue
            # Blanket-фильтра `basename.startswith("_")` здесь больше НЕТ. Он был скопирован из
            # дубль-гейта без вопроса и выводил из периметра все `_*.py` и каждый `__init__.py`
            # вопреки норме «каждый новый .py» (DG-03). Ведущее подчёркивание — соглашение об
            # ИМЕНОВАНИИ, а не граница способности: файл с ним умеет ровно то же. Узкое исключение
            # для пустого `__init__.py` живёт ниже, в evaluate_new_modules, и проверяется машиной.
            # Сплошного отбрасывания по `indexer.SKIP` здесь БОЛЬШЕ НЕТ (решение владельца
            # 2026-07-29, ред. 2). Оно ставило один периметр на два разных требования: файл,
            # которому не нужен характеризационный тест, уходил из-под суда ЦЕЛИКОМ — вместе с
            # обязанностью объявить контракт и вынести вердикт. Теперь решает таблица
            # `perimeter.json` через `demands_for`, отдельно по K1 и по K2. `indexer.SKIP`
            # остаётся scope'ом ИНДЕКСА (его читает дубль-гейт) — это разные вопросы, и
            # склейка их одним множеством и была источником неправды в §15.
            # Признак касания канона считается по ДЕРЕВУ, поэтому файл сначала читается, а
            # отсев делается ниже, в evaluate. Здесь остаётся только дешёвый отсев по классу,
            # который не перекрывается влиянием на канон ни при каком содержимом (тесты,
            # генерируемое, не-Python): читать 425 тестовых файлов на каждом коммите незачем.
            _cls = class_for(f, _perim)
            if _cls and _cls in set((( _perim or {}).get("canon") or {}).get("never_overrides") or ()):
                continue
            try:
                added[f] = open(os.path.join(sroot, f), encoding="utf-8").read()
            except Exception:
                continue                               # silent-ok: нечитаемый блоб не судим
        if not added:
            return [], []
        tests = {}
        for dp, _, fn in os.walk(os.path.join(sroot, "tests")):
            for f in fn:
                if not f.endswith(".py"):
                    continue
                p = os.path.join(dp, f)
                try:
                    tests[os.path.relpath(p, sroot).replace(os.sep, "/")] = open(
                        p, encoding="utf-8").read()
                except Exception:
                    continue                           # silent-ok: нечитаемый тест не судим
        blocks, warns = evaluate_new_modules(ix, added, tests, sidecars, policy)
        warns += [f"нечитаемый сайдкар {b}" for b in bad]
        return blocks, warns
