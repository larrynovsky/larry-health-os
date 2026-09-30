"""project_context.mcp_server — MCP-обёртка (stdio) над capabilities (морковь против RN-F:
тул в списке инструментов агент видит и зовёт; shell-команду из памяти — нет; решение владельца
2026-07-23, CLI-first отвергнут). Zero-dep: stdlib, JSON-RPC 2.0, newline-delimited stdio.

Регистрация (конфиг MCP-серверов Claude Desktop, рядом с Desktop Commander):
  "project-context": {"command": "/opt/homebrew/bin/python3.11",
                      "args": ["-m", "project_context.mcp_server"],
                      "env": {"PYTHONPATH": "<repo>",
                              "PROJECT_CONTEXT_ROOT": "<repo>"}}
Движок домен-агностичен: root — любой репо (project_context.json опционален).
Индекс строится на КАЖДЫЙ вызов от текущего дерева (R1: свежесть, нет кэша к порче).
How-to: docs/how-to/discovery_before_build.md. ADR: docs/explanation/discovery_before_build.md.
"""
import json, os, sys, time
from project_context import indexer

ROOT=os.environ.get("PROJECT_CONTEXT_ROOT") or os.getcwd()
TOOL={
 "name":"project_capabilities",
 "description":("Discovery-до-постройки: НАЙДИ существующие функции по имени И по данным "
   "(таблице) ПРЕЖДЕ, чем писать новую функцию/модуль. Дубль часто живёт под другим "
   "именем и виден только через данные, которые он трогает (by_data — главный вывод). "
   "Зови с именем предполагаемой функции и/или таблицей. Scope: только data/def-"
   "способности; пустой результат НЕ значит «дублей нет»."),
 "inputSchema":{"type":"object","properties":{
     "query":{"type":"string","description":"имя функции или таблица (подстрока)"},
     "root":{"type":"string","description":"корень репо (default: PROJECT_CONTEXT_ROOT)"}},
   "required":["query"]}}

TOOL2={
 "name":"project_intent",
 "description":("Онбординг в СУЩЕСТВУЮЩЕЕ: реестр замысла и тёплые страницы проекта. Без аргументов — "
   "каталог подсистем (id, title, статусы инвариантов) — это МЕНЮ, не знание. С id — замысел, "
   "инварианты со статусами (не-holds = чему верить нельзя) и тёплая страница; принимает и имя "
   "страницы вне реестра (свежесть таких не сторожится). С module — к какой подсистеме принадлежит "
   "модуль (мост код→замысел: нашёл функцию через project_capabilities — узнай, зачем она такая). "
   "Страница — вход, не замена источника: после неё иди в код."),
 "inputSchema":{"type":"object","properties":{
     "id":{"type":"string","description":"id подсистемы из каталога или имя страницы docs/explanation"},
     "module":{"type":"string","description":"имя .py-модуля — найти его подсистему(ы)"},
     "root":{"type":"string","description":"корень репо (default: PROJECT_CONTEXT_ROOT)"}}}}

MENU_NOTE=("каталог — МЕНЮ, не знание: возьми id → читай замысел+инварианты → иди в код. "
           "Статус не-holds означает: утверждению верить нельзя, оно не построено или спорно.")

DESKTOP_CONFIG=os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")

def dead_roots_in_config(path=DESKTOP_CONFIG):
    """[(сервер, корень)] для серверов ЭТОГО движка, чей PROJECT_CONTEXT_ROOT не существует.

    ЗАЧЕМ (22.09, решение владельца «вариант Б»). Отказ на мёртвом корне видит только
    тот агент, который позвал тул; сервер, которого никто не зовёт, лежит мёртвым
    молча. Живой случай: адрес `project-context-mm` исправили 14.09, а 22.09 в
    конфиге снова стоял удалённый iCloud-путь — секция mcpServers совпала байт-в-байт
    с копией ДО правки, поменялись только preferences. Правдоподобная причина: Claude
    Desktop пишет конфиг целиком из памяти и затирает внешнюю правку, сделанную при
    запущенном приложении. Не доказано — поэтому сторож, а не вера в исправление.
    Файла конфига нет → [] : на машине без Desktop судить нечего (Studio)."""
    if not os.path.exists(path): return []
    cfg=json.load(open(path,encoding="utf-8"))
    out=[]
    for name,srv in (cfg.get("mcpServers") or {}).items():
        if "project_context.mcp_server" not in " ".join(srv.get("args") or []): continue
        root=(srv.get("env") or {}).get("PROJECT_CONTEXT_ROOT")
        if root and not os.path.isdir(root): out.append((name,root))
    return out

def _registry(root):
    """Реестр замысла или None (файла нет). PyYAML лениво: нужен только intent-веткам.
    None законен только на ЖИВОМ корне: на мёртвом «реестр не заведён» — ложь о проекте."""
    indexer.require_live_root(root)
    rp=os.path.join(root,"subsystem_intent.yaml")
    if not os.path.exists(rp): return None
    import yaml
    return yaml.safe_load(open(rp,encoding="utf-8")) or []

def _exp_dir(root): return os.path.realpath(os.path.join(root,"docs","explanation"))

def _mtime(path): return time.strftime("%Y-%m-%d",time.localtime(os.path.getmtime(path)))

def _teaser(txt, n=200):
    """Тизер по границе слова: обрезка посреди слова читается как обрыв, не как приглашение."""
    t=(txt or "").strip()
    if len(t)<=n: return t
    return t[:n].rsplit(" ",1)[0]+"…"

def intent_catalog(root):
    reg=_registry(root)
    if reg is None:
        return {"registry":None,"note":"в этом проекте нет subsystem_intent.yaml — реестр замысла не заведён; "
                "онбординг только по данным (project_capabilities) и файлам проекта"}
    subs=[]; reg_pages=set()
    for e in reg:
        inv=e.get("invariants",[]); nh=[i["id"] for i in inv if i.get("status")!="holds"]
        reg_pages.add(os.path.basename(e.get("explanation","")))
        subs.append({"id":e["id"],"title":e.get("title",""),
                     "intent":_teaser(e.get("intent","")),
                     "invariants":len(inv),"not_holds":nh})
    ung=[]; d=_exp_dir(root)
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith(".md") and f not in reg_pages:
                ung.append({"page":f[:-3],"mtime":_mtime(os.path.join(d,f))})
    return {"note":MENU_NOTE,"subsystems":subs,
            "unguarded_pages":{"note":"вне реестра — свежесть НЕ сторожится тестами, ориентируйся на mtime",
                               "pages":ung}}

def intent_page(root, sid):
    reg=_registry(root) or []
    e=next((x for x in reg if x["id"]==sid),None)
    if e:
        rel=e.get("explanation")
        if rel:                                           # путь ТОЛЬКО из реестра (whitelist-индирекция)
            path=os.path.join(root,rel)
            body=open(path,encoding="utf-8").read() if os.path.exists(path) else "(страница отсутствует на диске)"
            note="страница под провенанс-сторожами; всё равно это вход — источник в code_anchors"
        else:                                             # реестр-без-страниц (v1 нового проекта) — не отказ
            body=None
            note="тёплой страницы нет (реестр без страниц): замысел и инварианты — здесь, источник — code_anchors"
        return {"id":sid,"guarded":True,"title":e.get("title",""),"intent":e.get("intent",""),
                "invariants":[{"id":i["id"],"status":i.get("status"),"claim":i.get("claim")}
                              for i in e.get("invariants",[])],
                "code_anchors":e.get("code_anchors",[]),"page":body,"note":note}
    if "/" in sid or "\\" in sid or sid.startswith("."):   # id — имя, не путь (контракт строже basename)
        return {"error":f"id не может быть путём: {sid!r}","hint":"каталог: project_intent без аргументов"}
    d=_exp_dir(root)                                       # F6 2026-07-24: членство в server-side словаре,
    names={f[:-3] for f in os.listdir(d) if f.endswith(".md")} if os.path.isdir(d) else set()
    base=sid[:-3] if sid.endswith(".md") else sid          # …не конкатенация запроса в путь
    if base in names:
        real=os.path.join(d,base+".md")
        return {"id":sid,"guarded":False,"page":open(real,encoding="utf-8").read(),
                "note":"страница ВНЕ реестра: свежесть НЕ сторожится, сверяй с кодом; mtime "+_mtime(real)}
    return {"error":f"нет подсистемы/страницы {sid!r}","known_ids":[x["id"] for x in reg],
            "hint":"каталог: project_intent без аргументов"}

def intent_module(root, module):
    ix=indexer.build(root)
    from project_context import receipt
    rmap=receipt._reverse_map(ix)
    m=module.replace("\\","/"); m=m[:-3] if m.endswith(".py") else m   # ключ = relpath без .py (F1);
    cand={m} if m in rmap else set(ix.get("by_stem",{}).get(m.rsplit("/",1)[-1],()))  # стем-тёзки → ВСЕ ключи
    wide=sorted(set().union(*(rmap.get(k,set()) for k in cand)) if cand else set())
    bs=ix.get("by_stem",{})                                # вердикт-2 P3: прямые носители ≠ со-писатели
    def _anchored(sid):
        anch=set()
        for a in ix["intent"].get(sid,()): anch |= {a} if a in ix["PROJ"] else set(bs.get(a,()))
        return bool(anch & cand)
    direct=[s for s in wide if _anchored(s)]; related=[s for s in wide if s not in set(direct)]
    if wide:
        return {"module":m,"subsystems":direct,"related_via_data":related,
                "note":"subsystems — прямые носители замысла (code_anchors подсистемы); related_via_data — "
                       "со-писатели тех же данных: их замыслы УЧИТЫВАЙ при правке (граница L2-гейта), но модуль им не принадлежит",
                "next":"project_intent id=<…> — замысел и инварианты"}
    return {"module":m,"subsystems":[],
            "note":"модуль не входит в подсистемы реестра (или реестра нет) — это НЕ значит, что замысла нет; "
                   "ищи по данным (project_capabilities) и в docs/"}

def _usage(tool, args, root, resp_bytes):
    """Замер реальной картины (решение владельца 2026-07-24): строка JSONL на каждый
    tools/call — что зовут, с чем, сколько байт уехало. Решение про векторный
    поиск примут эти данные, не мнение. Путь: PC_USAGE_LOG или ~/.project_context_usage.jsonl."""
    try:
        path=os.environ.get("PC_USAGE_LOG") or os.path.expanduser("~/.project_context_usage.jsonl")
        arg=args.get("query") or args.get("id") or args.get("module") or ""
        open(path,"a",encoding="utf-8").write(json.dumps(
            {"ts":round(time.time()),"tool":tool,"arg":str(arg)[:80],
             "root":os.path.basename(root or ""),"resp_bytes":resp_bytes},ensure_ascii=False)+"\n")
    except Exception: pass  # silent-ok: замер не имеет права ломать канал

def _reply(rid, result=None, error=None):
    msg={"jsonrpc":"2.0","id":rid}
    if error is not None: msg["error"]=error
    else: msg["result"]=result
    sys.stdout.write(json.dumps(msg,ensure_ascii=False)+"\n"); sys.stdout.flush()

def main():
    for line in sys.stdin:
        line=line.strip()
        if not line: continue
        try: req=json.loads(line)
        except Exception: continue                           # silent-ok: мусорная строка — не наш кадр
        method=req.get("method"); rid=req.get("id")
        if method=="initialize":
            _reply(rid,{"protocolVersion":req.get("params",{}).get("protocolVersion","2024-11-05"),
                        "capabilities":{"tools":{}},
                        "serverInfo":{"name":"project-context","version":"1.0"}})
        elif method=="tools/list":
            _reply(rid,{"tools":[TOOL,TOOL2]})
        elif method=="tools/call":
            prm=req.get("params",{}) or {}
            name=prm.get("name"); args=prm.get("arguments",{}) or {}
            root=args.get("root") or ROOT
            try:
                if name==TOOL["name"]:
                    res=indexer.capabilities(indexer.build(root),args.get("query",""))
                elif name==TOOL2["name"]:
                    if args.get("id"): res=intent_page(root,args["id"])
                    elif args.get("module"): res=intent_module(root,args["module"])
                    else: res=intent_catalog(root)
                else:
                    res={"error":f"unknown tool {name!r}"}
                text=json.dumps(res,ensure_ascii=False,indent=1)
                _reply(rid,{"content":[{"type":"text","text":text}]})
                _usage(name,args,root,len(text.encode("utf-8")))
            except Exception as e:
                _reply(rid,{"content":[{"type":"text","text":f"{name} error: {e}"}],"isError":True})
        elif rid is not None:
            _reply(rid,error={"code":-32601,"message":f"method not found: {method}"})
        # notifications (без id) — молча игнорируем

if __name__=="__main__":
    main()
