"""L2-гейт: какие подсистемы задеты стейдж-диффом и есть ли по ним ЧЕСТНАЯ свежая
квитанция preflight. BLOCK-режим (глобально): нет валидной квитанции → коммит режется.

Честная = существует + свежая (<TTL) + содержимое == canonical_token (привязка к
HEAD + составу; `touch`-пустышкой не подделать, гаснет при смене HEAD).

Слияние нити (MERGE_HEAD есть): засчитывается и квитанция ДЕРЕВА НИТИ — свежая и с тем же
СОСТАВОМ подсистемы (вторая строка, composition_token). HEAD там не сравнивается: ребейз
при закрытии и коммиты артефактов меняют SHA, не меняя прочитанного; сменившийся состав
(сосед добавил писателя таблицы, нить ввела подсистему иначе) — блок, перечитай
(BL-PREFLIGHT-NEW-SUB-1: до 25.09 обход пробным слиянием руками на каждом закрытии)."""
import os, time, subprocess
from project_context import indexer
TTL_H=6

def _reverse_map(ix):
    rmap={}
    for sid in ix["intent"]:
        _,mem,_,_=indexer.members(ix,sid)
        for m in mem: rmap.setdefault(m,set()).add(sid)
    return rmap

def _fresh_lines(rf):
    """Строки квитанции, если она есть и свежая; иначе []."""
    if not os.path.exists(rf) or time.time()-os.path.getmtime(rf) >= TTL_H*3600: return []
    return open(rf,encoding="utf-8").read().split()

def receipt_valid(ix, root, sub):
    """Квитанция по sub честная и свежая? exists + mtime<TTL + токен совпадает."""
    try:
        lines=_fresh_lines(os.path.join(indexer.receipts_dir(root),sub))
        return bool(lines) and lines[0]==indexer.canonical_token(ix,sub)
    except Exception:
        return False

def merge_source_tree(root):
    """Дерево нити, которую сейчас сливают в root. Иначе None.

    Два источника. THREAD_MERGE_SOURCE ставит scripts/thread_finish.sh: при настоящем
    `git merge` хук pre-merge-commit зовётся ДО записи MERGE_HEAD (замер 25.09: пробное
    слияние --no-commit прошло гейт, настоящее — нет). Без него — worktree с HEAD == MERGE_HEAD
    (ручное `merge --no-commit`)."""
    env=os.environ.get("THREAD_MERGE_SOURCE")
    if env and os.path.isdir(env) and os.path.realpath(env)!=os.path.realpath(root):
        return env
    try:
        mh=subprocess.check_output(["git","rev-parse","-q","--verify","MERGE_HEAD"],
                                   cwd=root,stderr=subprocess.DEVNULL).decode().strip()
        wl=subprocess.check_output(["git","worktree","list","--porcelain"],cwd=root).decode()
    except Exception:
        return None
    path=None
    for line in wl.splitlines():
        if line.startswith("worktree "): path=line[len("worktree "):]
        elif line=="HEAD "+mh and path and os.path.realpath(path)!=os.path.realpath(root):
            return path
    return None

def merge_credit(ix, root, sub):
    """Квитанция дерева сливаемой нити: свежая и состав sub в сливаемом дереве тот же."""
    src=merge_source_tree(root)
    if not src: return False
    try:
        lines=_fresh_lines(os.path.join(indexer.receipts_dir(src),sub))
        return len(lines)>1 and lines[1]==indexer.composition_token(ix,sub)
    except Exception:
        return False

def check(root="."):
    """Список задетых подсистем БЕЗ честной свежей квитанции. [] если git недоступен
    (fail-open: поломка инструмента не должна блокировать коммит)."""
    ix=indexer.build(root)
    try:
        staged=subprocess.check_output(["git","diff","--cached","--name-only"],cwd=root).decode().split()
    except Exception:
        return []
    keys={f[:-3].replace(os.sep,"/") for f in staged if f.endswith(".py")}  # ключ = relpath без .py (F1)
    rmap=_reverse_map(ix); touched=set()
    for s in keys: touched|=rmap.get(s,set())
    orch=ix.get("orch",{})                             # не-.py оркестраторы (carrier-fix 2026-07-23)
    for f in staged: touched|=orch.get(os.path.basename(f),set())
    return [sub for sub in sorted(touched)
            if not receipt_valid(ix,root,sub) and not merge_credit(ix,root,sub)]
