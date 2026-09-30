"""project_context.indexer — детерминантный спайн (AST) + preflight-дайджест.
Строится НА машине (D2), вывод структурный (I-8: имена/счётчики/file:line, без кода).
Клин-гейт консервативен (D3). Не авторитет — маршрут к источнику."""
import ast, os, re, json, collections, subprocess
# INTENT: project_context — замысел и инварианты: subsystem_intent.yaml (тёплый слой: docs/explanation/project_context.md)

SKIP={"tests","migrations","__pycache__",".git",".pytest_cache",".ruff_cache",
      "dashboard_templates","dashboard_static","constitutions","docs","plans","logs",
      "outputs","launchd","snapshots","reports",".claude"}
# ВНИМАНИЕ (вердикт-3 2026-07-24, P1): project_context БЫЛ в SKIP — движок не видел сам себя,
# второй анти-дубль-контур был бы невидим discovery (дыра класса «Этап 0 ТЗ»). Самоисключение снято.
STOP={"set","or","where","select","from"}
WPAT=re.compile(r'(?:INSERT\s+INTO|INSERT\s+OR\s+\w+\s+INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\s+\W?([a-z_][a-z0-9_]*)',re.I)
RPAT=re.compile(r'\bFROM\s+\W?([a-z_][a-z0-9_]*)',re.I)
# F2 2026-07-24: файлы данных — такой же якорь, как таблицы (people/, glossary/*.jsonl, docs/explanation/*.md).
# Путь с каталогом — любое data-расширение; голое имя — только сильно-данные расширения (иначе шум "config.json").
FPAT=re.compile(r'((?:[\w.\-]+/)+[\w.\-]+\.(?:txt|jsonl|csv|json|md|ya?ml|db|sqlite3?)|\b[\w\-]+\.(?:jsonl|csv|db|sqlite3?))\b',re.I)
FSTOP={"logs","tmp","temp","output","outputs","static","templates","tests","__pycache__"}

def scan_data(node):
    """ВСЕ data-якоря внутри node → (writes, reads). Три класса: SQL-таблицы (WPAT/RPAT),
    файлы данных (FPAT в строковых литералах; open(path, mode) различает W/R), каталоги
    данных — первый сегмент первого строкового аргумента os.path.join/Path («people/»,
    «glossary/»): путь по СИНТАКСИЧЕСКОЙ позиции, не по догадке — голое слово вне
    join/Path путём не считается (соседний проект ходит в файлы именно так, F2 2026-07-24).
    Одна логика для индекса и дубль-гейта (dupgate._tables) — иначе они разъедутся."""
    Ws,Rs=set(),set()
    if node is None: return Ws,Rs
    for nd in ast.walk(node):
        if isinstance(nd,ast.Constant) and isinstance(nd.value,str):
            for m in WPAT.finditer(nd.value):
                tb=m.group(1).lower()
                if tb not in STOP: Ws.add(tb)
            for m in RPAT.finditer(nd.value):
                tb=m.group(1).lower()
                if tb not in STOP: Rs.add(tb)
            for m in FPAT.finditer(nd.value):
                Rs.add(m.group(1).lower().lstrip("./"))
            m2=re.fullmatch(r'([\w\-]+)/',nd.value)      # весь литерал = «dir/»: префикс-часть f-строки
            if m2 and "." not in m2.group(1) and m2.group(1).lower() not in FSTOP:   # f"people/{slug}/"
                Rs.add(m2.group(1).lower()+"/")
        elif (isinstance(nd,ast.BinOp) and isinstance(nd.op,ast.Div)
              and isinstance(nd.right,ast.Constant) and isinstance(nd.right.value,str)):
            seg=nd.right.value.replace("\\","/").strip("/").split("/")[0].lower()   # pathlib: MM_ROOT / "people"
            if seg and "." not in seg and seg not in FSTOP: Rs.add(seg+"/")
        elif isinstance(nd,ast.Call):
            f=nd.func
            a0=nd.args[0] if (nd.args and isinstance(nd.args[0],ast.Constant)
                              and isinstance(nd.args[0].value,str)) else None
            if isinstance(f,ast.Name) and f.id=="open" and a0:
                mo=next((kw.value.value for kw in nd.keywords
                         if kw.arg=="mode" and isinstance(kw.value,ast.Constant)),None) \
                   or (nd.args[1].value if len(nd.args)>1 and isinstance(nd.args[1],ast.Constant)
                       and isinstance(nd.args[1].value,str) else "r")
                dst=Ws if any(c in str(mo) for c in "wax+") else Rs
                for m in FPAT.finditer(a0.value): dst.add(m.group(1).lower().lstrip("./"))
            elif a0 and not a0.value.startswith("/") and (
                    (isinstance(f,ast.Attribute) and f.attr in {"join","joinpath"})
                    or (isinstance(f,ast.Name) and f.id=="Path")):
                seg=a0.value.replace("\\","/").strip("/").split("/")[0].lower()
                if seg and "." not in seg and seg not in FSTOP: Rs.add(seg+"/")
    return Ws,Rs

class ProjectManifestUnusable(Exception):
    """`<root>/project_context.json` СУЩЕСТВУЕТ, но прочитать его как политику нельзя."""


def project_manifest(root, strict=False, label=None):
    """СЫРОЙ `<root>/project_context.json` или {}. Единственный дом чтения этого файла.

    До 2026-09-12 его открывали четырьмя одинаковыми try/except (три здесь, четвёртый
    собирался завести `dispgate` под проектный периметр) — дубль по ДАННЫМ, а не по имени:
    одна и та же «безопасная сторона» жила бы в четырёх редакциях и разъехалась бы при первой
    же правке (§9).

    ДВА РАЗНЫХ СОБЫТИЯ, КОТОРЫЕ РАНЬШЕ БЫЛИ ОДНИМ (внешнее ревью 12.09.2026, F-01).
    «Файла нет» — законное состояние: движок домен-агностичен, у проекта просто нет политики.
    «Файл есть, но негоден» — отказ инструмента суждения, и он обязан быть слышен. Первая
    редакция сводила оба к {} — и битый json молча снимал проектный канон, оставляя при этом
    движковое освобождение каталога: миграция, импортирующая канон, проходила БЕЗ
    характеризационного теста, до настоящего успешного `git commit`. «Fallback всегда строже»
    оказался неверен ровно там, где движковое освобождение встречается с проектным усилением.

    `strict=False` (умолчание) сохраняет прежнее поведение для потребителей, которые читают
    отсюда ЧИСЛА и списки-дополнения: у них негодный файл означает дефолты движка, и это их
    принятая политика отказа, менять её в одиночку нельзя. `strict=True` — для потребителя,
    у которого негодный файл МЕНЯЕТ СТРОГОСТЬ СУЖДЕНИЯ: он получает исключение.

    `label` — как файл НАЗЫВАТЬ человеку. Нужен потому, что строгий потребитель читает не
    рабочее дерево, а одноразовый снимок индекса: путь в сообщении вёл в `/tmp/pc-index-*`,
    который к моменту чтения сообщения уже удалён (внешнее ревью 12.09.2026, раунд 2,
    F-R2-05). Номер строки и колонки от json остаются — пропадал только адрес.

    СИМЛИНК ПРИ `strict=True` — ОТКАЗ (F-R2-08). `git checkout-index` кладёт в снимок ИМЯ
    ссылки, а байты живут снаружи индекса: вердикт менялся трижды при неизменном блобе.
    Пока проектный слой двигал только числа-ориентиры, цена дыры была мала; после F-01 он
    двигает ПЕРИМЕТР — ровно ту величину, ради строгости которой ремонт и делался.
    Потребителей ЧИСЕЛ это не касается: у них своя принятая политика отказа (`strict=False`),
    менять её в одиночку нельзя."""
    path = os.path.join(root, "project_context.json")
    shown = label or path
    if strict and os.path.islink(path):
        raise ProjectManifestUnusable(
            f"{shown}: символическая ссылка — политика уводится из-под индекса "
            f"(в индексе лежит имя, байты снаружи и меняются без него)")
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return {}                      # политики нет — законно, не отказ
    except OSError as e:
        if strict:
            raise ProjectManifestUnusable(f"{shown}: файл есть, но не читается ({e})")
        return {}
    try:
        doc = json.loads(raw)
    except ValueError as e:
        if strict:
            raise ProjectManifestUnusable(f"{shown}: не разбирается как JSON ({e})")
        return {}
    if not isinstance(doc, dict):
        if strict:
            raise ProjectManifestUnusable(
                f"{shown}: верхний уровень {type(doc).__name__}, а не объект политики")
        return {}
    return doc


def _manifest(root):
    """Проектная ПОЛИТИКА (не в движке): чувствительные классы D3, сантехника, Ф0-корпус.
    Читается из <root>/project_context.json. Нет файла/битый → нейтраль: движок домен-
    агностичен (ни D3-гейта, ни ложных путей). Health — первый манифест; так один движок
    раскатывается на любой проект — своя политика в корне каждого репо, а не хардкод в коде."""
    m=project_manifest(root)
    return dict(sensitive_tables=set(m.get("sensitive_tables",[])),
                sensitive_modules=set(m.get("sensitive_modules",[])),
                plumbing=set(m.get("plumbing",[])),
                dup_allowlist=set(m.get("dup_allowlist",[])),
                catch_log=m.get("catch_log","docs/reference/duplicate_catch_log.md"),  # F5: путь журнала — политика проекта
                corpus=_corpus(root,m))


def _corpus(root,m):
    """Ложные пути для preflight. С 28.09.2026 их дом — <root>/lessons.yaml (уроки с полем
    modules, любого статуса); manifest.corpus читается, только если дома уроков нет —
    ради проектов, не переехавших на общий движок. Два дома сразу — не законно: при
    наличии lessons.yaml corpus в манифесте игнорируется и об этом говорит тест."""
    from project_context import lessons
    if lessons.exists(root):
        return lessons.as_corpus(root)
    return m.get("corpus",[])

def disposability_policy(root=".",engine_dir=None):
    """Чек-лист одноразовости: ПУНКТЫ из движка (disposability.json, методология — домен-агностична)
    + ЧИСЛА, переопределяемые проектной политикой (<root>/project_context.json → disposability.limits).
    Отдаёт ОДИН слитый результат: это единственный законный путь к значениям, иначе два дома одного
    числа разъедутся. Прямое чтение файла в обход ридера ловит source-guard в test_project_context.
    degraded=True — движковый файл не прочитан или без пунктов/чисел: потребитель обязан сказать это
    ГРОМКО (fail-static), а не тихо пропустить проверку. unknown — ключи политики вне известных:
    опечатка в проектном файле иначе тихо не сработала бы.
    engine_dir — тест-сеам (Controllability): позволяет контролю подсунуть битый/пустой движковый
    каталог и проверить, что degraded действительно поднимается. В прод-вызовах не передаётся."""
    eng={}
    try:
        _ed=engine_dir or os.path.dirname(os.path.abspath(__file__))
        eng=json.load(open(os.path.join(_ed,"disposability.json"),encoding="utf-8"))
    except Exception:
        pass  # silent-ok: деградация выражается через degraded, не через исключение
    # degraded по ТИПУ, а не по непустоте. Проверка «есть и непусто» пропускала валидный JSON с
    # items="wrong" или limits="wrong": ридер отдавал их дальше, evaluator падал на i["id"] или
    # dict(...), и общий CLI-except превращал отказ инструмента в разрешение коммита
    # (ревью 2026-07-27, F-02). Схему держим здесь — это единственный дом ридера.
    _raw_lim=eng.get("limits"); _raw_it=eng.get("items")
    _lim_ok=isinstance(_raw_lim,dict) and bool(_raw_lim)
    _it_ok=isinstance(_raw_it,list) and bool(_raw_it) and all(
        isinstance(i,dict) and str(i.get("id") or "").strip() for i in _raw_it)
    limits=dict(_raw_lim) if _lim_ok else {}
    if not _it_ok: eng=dict(eng,items=[])
    degraded=not (_it_ok and _lim_ok)
    proj=project_manifest(root)  # silent-ok: нет политики проекта → дефолты движка (движок домен-агностичен)
    _pd=proj.get("disposability") if isinstance(proj,dict) else None
    over=(_pd.get("limits") if isinstance(_pd,dict) else None) or {}
    if not isinstance(over,dict): over={}   # строка вместо объекта итерировалась бы по символам
    unknown=sorted(k for k in over if k not in limits)
    limits.update({k:v for k,v in over.items() if k in limits})
    return dict(items=eng.get("items") or [],criterion=eng.get("criterion",""),
                review_question=eng.get("review_question",""),machine_scope=eng.get("machine_scope",""),
                limits=limits,degraded=degraded,unknown=unknown)

def contract_sidecars(root="."):
    """Сайдкар-контракты: contracts/<путь-модуля>.json → {ключ-модуля: {...}}, + список нечитаемых.
    ЕДИНСТВЕННЫЙ ридер формата: его зовут и check_contracts (реестр поверхности), и dispgate (K1).
    Два ридера одного формата разъехались бы — это дубль значения, а не реплика.
    Ключ модуля — путь-форма без .py (project_context/dispgate), как у индекса (F1); имя импорта
    получается заменой / на точку у вызывающего. Нечитаемый сайдкар НЕ проглатывается: возвращается
    в bad, вызывающий обязан сказать это громко (иначе битый json = «контракта нет» = тихо зелено)."""
    out={}; bad=[]
    d=os.path.join(root,"contracts")
    if not os.path.isdir(d): return out,bad
    for dp,_,fn in os.walk(d):
        for f in sorted(fn):
            if not f.endswith(".json"): continue
            p=os.path.join(dp,f)
            rel=os.path.relpath(p,d).replace(os.sep,"/")
            try:
                data=json.load(open(p,encoding="utf-8"))
            except Exception as e:
                bad.append(f"{rel}: {str(e)[:60]}"); continue
            key=data.get("module") or rel[:-5]
            # `kind` и `negative_control` переносятся ОБЯЗАТЕЛЬНО: на них стоит режим K2'
            # (Р-6, 2026-07-29) — проба предъявляет исполненный негативный контроль вместо
            # характеризационного теста. Пока их здесь не было, dispgate читал kind=None →
            # судил пробу как модуль → требовал тест, а ветка kind=="probe" и вся
            # _probe_evidence_problems были МЁРТВЫМ кодом: правило объявлено в докстринге и
            # не подключено к единственному ридеру. Поймано 2026-07-29 первой же пробой,
            # которая попыталась этим режимом воспользоваться.
            out[key]=dict(public=list(data.get("public") or []),
                          depends_on=list(data.get("depends_on") or []),
                          disposability=data.get("disposability"),
                          kind=data.get("kind"),
                          negative_control=data.get("negative_control"),
                          path="contracts/"+rel)
    return out,bad

def disposability_causes(root="."):
    """Реестр причин отрицательных вердиктов: {причина: {...}}. Считаем РАЗНЫЕ КАЛЕНДАРНЫЕ НЕДЕЛИ,
    а не модули и не дни. Почему: разрез health_db дал 37 новых модулей за ОДИН день с одной
    причиной — счётчик по модулям принял бы одну работу за закономерность (инфляция 37:1); дни
    слишком дешёвые (51 активный день из 113), бурст растягивается на два-три. Неделя — прокси
    «причина пережила отдельную единицу работы».
    resolved: причина помечена разобранной в политике (disposability.resolved_causes) — такая
    больше не сигналит, повторный стук по разобранному тренирует игнорировать сигналы.
    Дат «сейчас» не берём вообще: считаем только по датам, записанным в вердиктах."""
    import datetime as _dt
    pol=disposability_policy(root); thr=pol["limits"].get("cause_repeat_weeks") or 0
    proj=project_manifest(root)  # silent-ok: нет политики → нет помеченных разобранными
    resolved=(proj.get("disposability") or {}).get("resolved_causes") or {}
    sc,_bad=contract_sidecars(root)
    acc={}
    for key,v in sorted(sc.items()):
        d=v.get("disposability") or {}
        if d.get("verdict")!="not_disposable": continue
        cause=str(d.get("cause") or "").strip()
        if not cause: continue                            # гейт K1 такое не пропустит, но ридер честен
        e=acc.setdefault(cause,dict(weeks=set(),modules=[],rationales=[]))
        e["modules"].append(key)
        if d.get("rationale"): e["rationales"].append(d["rationale"])
        try:
            y,w,_=_dt.date.fromisoformat(str(d.get("date"))).isocalendar()
            e["weeks"].add(f"{y}-W{w:02d}")
        except Exception:
            pass  # silent-ok: битая дата не считается неделей; полноту поля стережёт гейт
    out={}
    for cause,e in acc.items():
        out[cause]=dict(weeks=sorted(e["weeks"]),count=len(e["weeks"]),modules=e["modules"],
                        rationales=e["rationales"],resolved=resolved.get(cause),
                        over_threshold=bool(thr) and len(e["weeks"])>=thr and cause not in resolved)
    return out

def require_live_root(root):
    """Отказ на мёртвом корне — ОДИН дом предиката для обоих тулов (22.09).

    14.09 проверка встала только в `build`, то есть на путь `project_capabilities`
    и `project_intent module=`. Каталог и страница замысла (`mcp_server._registry`)
    шли мимо и на мёртвом корне отвечали «реестр замысла не заведён» — неправдой
    о проекте. Замер 22.09: `project-context-mm` смотрел в удалённый iCloud-путь,
    capabilities отказывал, intent лгал. Второй копией проверки в mcp_server это
    не лечится: два текста одного отказа разошлись бы молча."""
    if not os.path.isdir(root):
        raise FileNotFoundError(
            f"корень проекта не существует: {root!r} — ответ по нему был бы пустотой, а "
            "пустота читается как знание («дублей нет», «реестр не заведён»). Это отказ, "
            "не ответ. Проверь PROJECT_CONTEXT_ROOT в конфиге MCP-серверов / cwd")

def build(root="."):
    # Корень обязан СУЩЕСТВОВАТЬ, и это не гигиена (14.09). `os.walk` по мёртвому
    # пути молча отдаёт пустоту, индекс выходит пустым, и `capabilities` отвечает
    # `by_name: []` со своей честной пометкой «пустой результат ≠ дублей нет».
    # Читатель при этом видит ровно то же, что при живом репозитории без дублей:
    # «искал и не нашёл» неотличимо от «искать было негде». Ровно этот класс нить
    # `project-context` осудила вердиктом-2 (нет движка → красить, а не пропускать),
    # только там он был про движок, а здесь про корень.
    # Замер, на котором нашлось: MCP-сервер `project-context-mm` смотрел в
    # iCloud-путь репозитория соседний проект, которого нет с 13.09 (репо переехал), и
    # полсуток отвечал пустотой как нормой.
    require_live_root(root)
    files=[]
    for dp,dn,fn in os.walk(root):
        dn[:]=[d for d in dn if d not in SKIP]
        for f in fn:
            if f.endswith(".py") and not f.startswith("_"): files.append(os.path.join(dp,f))
    path_of={}; trees={}
    for p in files:
        rel=os.path.relpath(p,root).replace(os.sep,"/")
        s=rel[:-3]   # ключ = ОТНОСИТЕЛЬНЫЙ ПУТЬ без .py (F1 2026-07-24: basename-ключ терял
        # файлы-тёзки — в соседнем проекте 5×runner.py, индекс видел ОДИН и путал file:line)
        path_of[s]=rel
        try: trees[s]=ast.parse(open(p,encoding="utf-8").read())
        except Exception: pass  # silent-ok: непарсящийся файл не входит в индекс
    PROJ=set(trees)
    by_stem=collections.defaultdict(set)   # стем → ключи: импорты и intent-якоря говорят стемами
    for k in PROJ: by_stem[k.rsplit("/",1)[-1]].add(k)
    def _res(name):
        """имя из import → ключ индекса; только при однозначности (коллизия → None, консервативно)"""
        if not name: return None
        d=name.replace(".","/")
        if d in PROJ: return d
        ks=by_stem.get(name.split(".")[-1],())
        return next(iter(ks)) if len(ks)==1 else None
    defs=collections.defaultdict(set); defline={}
    im=collections.defaultdict(dict); isym=collections.defaultdict(dict)
    for s,t in trees.items():
        for node in t.body:
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                defs[s].add(node.name); defline[(s,node.name)]=node.lineno
            if isinstance(node,ast.ClassDef):
                for m in node.body:
                    if isinstance(m,(ast.FunctionDef,ast.AsyncFunctionDef)):
                        defs[s].add(m.name); defline.setdefault((s,m.name),m.lineno)
        for node in ast.walk(t):
            if isinstance(node,ast.Import):
                for a in node.names:
                    k=_res(a.name)
                    if k: im[s][a.asname or a.name.split('.')[0]]=k
            elif isinstance(node,ast.ImportFrom):
                mk=_res(node.module)
                for a in node.names:
                    if mk: isym[s][a.asname or a.name]=(mk,a.name)
                    ak=_res((node.module+"."+a.name) if node.module else a.name) or _res(a.name)
                    if ak: im[s][a.asname or a.name]=ak
    edges=set()
    for s,t in trees.items():
        for node in ast.walk(t):
            if isinstance(node,ast.Call):
                f=node.func
                if isinstance(f,ast.Name):
                    n=f.id
                    if n in isym[s]: tg,og=isym[s][n]; edges.add((s,tg,og))
                    elif n in defs[s]: edges.add((s,s,n))
                elif isinstance(f,ast.Attribute) and isinstance(f.value,ast.Name):
                    if f.value.id in im[s]: edges.add((s,im[s][f.value.id],f.attr))
    sym_anch={}   # (модуль, символ верхнего уровня) → data-якоря в его значении: PEOPLE_DIR = MM_ROOT/"people"
    for s,t in trees.items():
        for node in t.body:
            tg=node.targets[0].id if (isinstance(node,ast.Assign) and len(node.targets)==1
                                      and isinstance(node.targets[0],ast.Name)) else \
               (node.target.id if isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name) else None)
            if tg and getattr(node,"value",None) is not None:
                ws,rs=scan_data(node.value)
                if ws|rs: sym_anch[(s,tg)]=ws|rs
    W=collections.defaultdict(set); R=collections.defaultdict(set)
    for s,t in trees.items():                            # одна логика с dupgate._tables (scan_data)
        ws,rs=scan_data(t)
        for tb in ws: W[tb].add(s)
        for tb in rs: R[tb].add(s)
        for sym,(mk,orig) in isym[s].items():            # one-hop: `from config import PEOPLE_DIR` тянет якорь
            for a in sym_anch.get((mk,orig),()): R[a].add(s)
        for nd in ast.walk(t):                           # …и config.PEOPLE_DIR через импорт модуля
            if isinstance(nd,ast.Attribute) and isinstance(nd.value,ast.Name) and nd.value.id in im[s]:
                for a in sym_anch.get((im[s][nd.value.id],nd.attr),()): R[a].add(s)
    intent={}; orch=collections.defaultdict(set)
    ip=os.path.join(root,"subsystem_intent.yaml")
    if os.path.exists(ip):
        txt=open(ip,encoding="utf-8").read()
        for b in re.split(r'\n- id:\s*',txt)[1:]:
            sid=b.splitlines()[0].strip(); intent[sid]=set(re.findall(r'([A-Za-z_][\w/\-]*)\.py::',b))  # якорь: стем ИЛИ путь (F1)
            mo=re.search(r'orchestrators:\s*\[([^\]]*)\]',b)  # не-.py носители подсистемы (carrier-fix 2026-07-23)
            if mo:
                for fn in re.findall(r'[\w.\-/]+\.\w+',mo.group(1)): orch[os.path.basename(fn)].add(sid)
    # root может быть МАТЕРИАЛИЗОВАННЫМ СНИМКОМ ИНДЕКСА (project_context.staged), а не рабочим
    # деревом: там нет .git, и это законно. Тогда head="?" — потребитель, которому нужен head
    # (canonical_token), обязан строить индекс по настоящему корню. stderr глушим: «fatal: not a
    # git repository» в выводе хука читается оператором как поломка, хотя это штатный режим.
    try: head=subprocess.check_output(["git","rev-parse","--short","HEAD"],cwd=root,stderr=subprocess.DEVNULL).decode().strip()
    except Exception: head="?"
    return dict(path_of=path_of,PROJ=PROJ,by_stem=dict(by_stem),defs=defs,defline=defline,edges=edges,W=W,R=R,intent=intent,orch=dict(orch),head=head,root=root,policy=_manifest(root))

def canonical_token(ix, sid):
    """Неподделываемая метка «preflight по sid прогнан на ЭТОМ коде».
    = hash(HEAD | sid | отсортированные члены). Привязана к HEAD (любой коммит в
    репо гасит квитанцию) и к составу подсистемы. `touch`-пустышкой не подделать:
    чтобы получить верный токен, надо построить индекс = прогнать инструмент."""
    import hashlib
    _,mem,_,_=members(ix,sid)
    payload=ix["head"]+"|"+sid+"|"+",".join(sorted(mem))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]

def composition_token(ix, sid):
    """Метка СОСТАВА подсистемы без HEAD — вторая строка квитанции. По ней гейт слияния
    засчитывает квитанцию дерева нити (receipt.merge_credit): ребейз при закрытии меняет
    SHA, не меняя прочитанного, а состав — меняет (BL-PREFLIGHT-NEW-SUB-1)."""
    import hashlib
    _,mem,_,_=members(ix,sid)
    return hashlib.sha256(("|"+sid+"|"+",".join(sorted(mem))).encode()).hexdigest()[:16]

def receipts_dir(root):
    """Каталог квитанций preflight — в git-каталоге ЭТОГО дерева, вне версионирования.

    Не `root/.git`: в дереве нити (§21) `.git` — ФАЙЛ-указатель, и os.makedirs по
    такому пути падает. Писатель глотал это молча (best-effort), читатель тоже —
    и гейт честно требовал квитанцию, которую физически некуда положить.

    Каталог берётся per-worktree (`--git-dir`), а не общий: квитанция привязана к
    HEAD, а у деревьев нитей HEAD разные — общий каталог давал бы ложно-свежую
    квитанцию соседней нити. Изоляция здесь дешевле, чем разбор такого зелёного."""
    try:
        import subprocess
        g=subprocess.check_output(["git","rev-parse","--absolute-git-dir"],
                                  cwd=root,stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        g=os.path.join(root,".git")
    return os.path.join(g,"preflight_receipts")

    # Конвергенция двух нитей (13.09): error-channel и question-answer-channel
    # независимо написали эту функцию с одинаковым диагнозом. Оставлена редакция
    # error-channel — `--absolute-git-dir` не требует разбора «путь относительный или
    # нет». Вторая половина вклада: у ПУТИ один дом, и читатель квитанции
    # (`receipt.receipt_valid`) зовёт эту функцию, а не клеит путь сам; оракул —
    # tests/unit/test_preflight_receipts_dir.py.

def emit_receipt(root, sub, token):
    """Квитанция «preflight прогнан для sub» — best-effort, вне git (см. receipts_dir).
    Содержимое = canonical_token (не подделать touch'ем, гаснет при смене HEAD);
    второй строкой — composition_token (зачёт при слиянии нити)."""
    try:
        d=receipts_dir(root); os.makedirs(d,exist_ok=True)
        open(os.path.join(d,sub),"w",encoding="utf-8").write(token+"\n")
    except Exception: pass  # silent-ok: квитанция не критична для дайджеста

def _callers(ix,home,name): return sorted({c for c,ts,nm in ix["edges"] if nm==name and ts==home and c!=home})
def _ntables(ix,m): return sum(1 for t,ms in ix["W"].items() if m in ms)
def _oneoff(ix,s):
    p=ix["path_of"].get(s,""); b=s.rsplit("/",1)[-1]   # имя-проверки — по стему (ключ теперь путь, F1)
    return p.startswith("scripts/") or "backfill" in b or b.startswith("seed_") or bool(re.match(r'^\d{4}',b)) or "migrat" in b

def capabilities(ix, query):
    """Discovery-до-постройки: что уже существует, связанное с query — по ИМЕНИ и по ДАННЫМ.
    by_name: функции с тем же/похожим именем в любом модуле (ловит точный дубль).
    by_data: если query — таблица (или подстрока), модули, трогающие её, + их функции
    (ловит дубль под ДРУГИМ именем через данные — ключевой случай)."""
    q=query.lower().strip()
    by_name=sorted({(fn,m,ix["path_of"].get(m,""),ix["defline"].get((m,fn),0))   # (имя, модуль, файл, СТРОКА) — вердикт-2
                    for m,fns in ix["defs"].items()
                    for fn in fns if q and (q in fn.lower() or fn.lower() in q)})
    tables=sorted(t for t in set(ix["W"])|set(ix["R"]) if q and (q==t or q in t))
    by_data={t:[(m,sorted(f"{fn}:{ix['defline'].get((m,fn),'?')}" for fn in ix["defs"].get(m,set())))
                for m in sorted(ix["W"].get(t,set())|ix["R"].get(t,set()))]
             for t in tables}
    return {"query":query,
            "scope":"поиск по ИМЕНАМ функций (by_name) и по ДАННЫМ — таблицам и файлам данных W/R (by_data). "
                    "НЕ покрыто: способности без данных (чистая логика, внешние API, ORM-обёртки). "
                    "Пустой результат НЕ значит «дублей нет» (RN-A).",
            "by_name":by_name,"by_data":by_data}

def members(ix,sub):
    seed=set(); hits=[sid for sid in ix["intent"] if sub in sid]
    bs=ix.get("by_stem",{})
    for sid in hits:
        for a in ix["intent"][sid]:                       # якорь: путь-ключ напрямую, стем — через by_stem (F1)
            seed |= {a} if a in ix["PROJ"] else set(bs.get(a,()))
    if not seed: seed={s for s in ix["PROJ"] if s.startswith(sub) or s.rsplit("/",1)[-1].startswith(sub)}
    seed_tables={t for t,ms in ix["W"].items() if ms&seed}
    cow={m for t in seed_tables for m in ix["W"][t]}
    core={m for m in cow if not _oneoff(ix,m)}
    return seed, seed|core, sorted(m for m in cow if _oneoff(ix,m)), hits

def preflight(ix,sub,budget=True):
    seed,mem,oneoff,hits=members(ix,sub)
    if not os.environ.get("PYTEST_CURRENT_TEST"):   # тесты не сеют реальные квитанции в .git репо (иначе прогон тестов молча гейт удовлетворяет)
        for _h in (hits or [sub]):
            emit_receipt(ix.get("root","."),_h,canonical_token(ix,_h)+"\n"+composition_token(ix,_h))
    infra=[m for m in seed if _ntables(ix,m)>12]
    pol=ix["policy"]
    clin_t=sorted(ct for ct in pol["sensitive_tables"] if (ix["W"][ct]|ix["R"][ct])&mem)
    clin_m=sorted(mem&pol["sensitive_modules"])
    by_list=(not hits) or any(h not in pol["plumbing"] for h in hits)  # клин по списку (D3), кроме сантехники
    corpus=pol["corpus"]
    fp=[c for c in corpus if set(c["modules"])&mem]
    L=[f"═ PREFLIGHT «{sub}» @{ix['head']} (seed=intent:{','.join(hits) or '—'}) ═",
       "МОДУЛИ: "+", ".join(sorted(mem))+(f"  ⚠ инфра-seed {infra}, подсистема широкая" if infra else "")]
    if clin_t or clin_m or by_list:
        why=(f"модули {clin_m} " if clin_m else "")+(f"таблицы {clin_t}" if clin_t else "")+("по списку D3" if by_list and not(clin_t or clin_m) else "")
        L.append("⚠ CLINICAL — карта НЕ авторитет, читай ИСТОЧНИК + гейт владельца (D3): "+why)
    sub_w=sorted(t for t,ms in ix["W"].items() if ms&mem)
    multi=[(t,sorted(ix["W"][t]-mem)) for t in sub_w if ix["W"][t]-mem]
    if multi:
        L.append("ВНЕШНИЕ writers таблиц (C-02):")
        for t,ext in multi[:4 if budget else 99]: L.append(f"  {t} ← {ext}")
    cand=sorted(((len(_callers(ix,s,n)),n,s,_callers(ix,s,n)) for s in mem for n in ix["defs"].get(s,()) if not n.startswith("_") and _callers(ix,s,n)),reverse=True)
    if cand:
        L.append("КЛЮЧЕВЫЕ СИМВОЛЫ (callers — кто сломается):")
        for cnt,n,s,cs in cand[:4 if budget else 99]:
            L.append(f"  {n} @{ix['path_of'][s]}:{ix['defline'].get((s,n),'?')} · {cnt}: {', '.join(cs[:5])}")
    if fp:
        L.append("ИЗВЕСТНЫЕ ЛОЖНЫЕ ПУТИ (Ф0):")
        # Порядок — по ПРИЦЕЛЬНОСТИ (доля модулей записи, попавших в подсистему),
        # а не по номеру. До 2026-08-02 резалось просто fp[:3], то есть побеждал
        # тот, кто раньше заведён: девять граблей C-26…C-34, заведённых в этот день,
        # не доезжали ни до одной широкой подсистемы вовсе. Чем дольше живёт corpus,
        # тем вернее новая запись оказывалась за отсечкой — молча.
        fp = sorted(fp, key=lambda c: (-len(set(c["modules"]) & mem) / max(len(c["modules"]), 1),
                                       c["id"]))
        _lim = 3 if budget else 99
        for c in fp[:_lim]: L.append(f"  • {c['id']}: {c['false_path']}")
        if len(fp) > _lim:                      # усечение НЕ молча (иначе читается как «это всё»)
            L.append(f"  … ещё {len(fp)-_lim}: {', '.join(c['id'] for c in fp[_lim:])}"
                     f"  (полностью — lessons.yaml, `python3 -m project_context lessons --modules …`)")
    _cz={c:v for c,v in disposability_causes(ix.get("root",".")).items() if set(v["modules"])&mem}
    if _cz:                                          # §15: урок идёт ТОМУ, КТО ЗДЕСЬ РАБОТАЕТ, а не в инбокс
        L.append("ПРИЧИНЫ «НЕ ОДНОРАЗОВЫЙ» ЗДЕСЬ (§15) — повтор причины = сигнал об архитектуре:")
        for _c,_v in sorted(_cz.items(),key=lambda kv:-kv[1]["count"])[:3 if budget else 99]:
            _mk=" · РАЗОБРАНО" if _v["resolved"] else (" · ПОРОГ ПРОЙДЕН" if _v["over_threshold"] else "")
            L.append(f"  {_c}: {_v['count']} нед. ({', '.join(_v['weeks'])}) — "
                     f"{', '.join(_v['modules'][:4])}{_mk}")
    L.append("ЗАМЫСЕЛ: project_intent "+(", ".join(hits) if hits else sub)+" (MCP-тул) — зачем подсистема устроена так")
    L.append("→ ЧИТАЙ ИСТОЧНИК: карта — маршрут, не замена. Открой файлы выше.")
    return "\n".join(L)
