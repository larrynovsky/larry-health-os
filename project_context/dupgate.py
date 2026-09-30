"""project_context.dupgate — дубль-гейт на staged diff (Слой 3 discovery-до-постройки).

BLOCK: staged-дифф ВВОДИТ модульную функцию, чьё имя уже определено в ДРУГОМ
prod-модуле (точный кросс-файл дубль, класс F-106/C-07). Только НОВЫЕ имена:
имя, существовавшее в HEAD-версии файла, не «введено» → легаси не красится (RN-E).
WARN: новая функция трогает таблицу, которую этот модуль в HEAD не трогал, а
другие модули обслуживают — кандидат «дубль под другим именем» (по данным).
Эвристика, никогда не блок (RN-B). Методы классов не судим: одинаковые имена
методов — интерфейс, не дубль. Включение гейта — только при зелёных позит+негат
контролях (tests/unit/test_dupgate.py). ADR: docs/explanation/discovery_before_build.md.
"""
import ast, os, subprocess
from project_context import indexer

# Системная механика (§9 п.2): конвенциональные имена, совпадающие между модулями
# по природе интерфейса (реестры, CLI-входы), не по дублированию способности.
SKIP_NAMES={"main","cli","run","setup","register","check","build","parse_args"}

def _tree(src):
    try: return ast.parse(src or "")
    except Exception: return None

def _defs(tree):
    """Только МОДУЛЬНЫЕ функции (методы — интерфейс, не судим)."""
    if tree is None: return {}
    return {n.name:n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}

def _tables(node):
    """Data-якоря внутри node: SQL-таблицы + файлы/каталоги данных — ОДНА логика с
    индексом (indexer.scan_data), иначе гейт и discovery разъезжаются (F2 2026-07-24)."""
    ws,rs=indexer.scan_data(node)
    return ws|rs

def evaluate(ix, staged, head, allow=frozenset()):
    """Чистая логика (контроли инжектят источники). staged/head: {relpath: source}.
    Возврат (blocks, warns) — списки строк для вывода."""
    blocks,warns=[],[]
    for path in sorted(staged):
        stem=(path[:-3] if path.endswith(".py") else os.path.splitext(path)[0]).replace(os.sep,"/")  # ключ = relpath без .py, синхронно с indexer.build (F1)
        st=_tree(staged[path]); ht=_tree(head.get(path,""))
        sdefs=_defs(st); hdefs=_defs(ht)
        head_tabs=_tables(ht)
        for name,node in sdefs.items():
            if name in hdefs: continue                       # не введено этим диффом (RN-E)
            if name.startswith("_") or name in SKIP_NAMES or name in allow: continue
            homes=sorted(m for m,fns in ix["defs"].items() if name in fns and m!=stem)
            if homes:
                blocks.append(f"{path}: новая `{name}` уже определена: "+", ".join(
                    f"{ix['path_of'].get(m,m)}:{ix['defline'].get((m,name),'?')}" for m in homes))
            for t in sorted(_tables(node)-head_tabs):        # чужая для модуля таблица
                others=sorted((ix["W"].get(t,set())|ix["R"].get(t,set()))-{stem})
                if others:
                    warns.append(f"{path}: `{name}` трогает `{t}`, которую уже обслуживают: "
                                 +", ".join(others[:6])
                                 +f" — сверь: capabilities {t}")
    return blocks,warns

def check(root="."):
    """IO-обёртка: staged-блобы из git + индекс текущего дерева (свежий каждый вызов, R1)."""
    ix=indexer.build(root)
    allow=set(ix["policy"].get("dup_allowlist",set()))
    try:
        files=subprocess.check_output(["git","diff","--cached","--name-only","--diff-filter=AM"],
                                      cwd=root).decode().split()
    except Exception:
        return [],[]                                         # git недоступен → fail-open
    staged={}; head={}
    for f in files:
        if not f.endswith(".py") or os.path.basename(f).startswith("_"): continue
        if any(p in indexer.SKIP for p in f.split("/")[:-1]): continue  # prod-scope = scope индекса
        try: staged[f]=subprocess.check_output(["git","show",":"+f],cwd=root).decode()
        except Exception: continue                           # silent-ok: нечитаемый блоб не судим
        try: head[f]=subprocess.check_output(["git","show","HEAD:"+f],cwd=root).decode()
        except Exception: head[f]=""                         # новый файл
    return evaluate(ix,staged,head,allow)
