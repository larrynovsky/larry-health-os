"""Контроли project_intent (onboarding-слой): позит/негат/traversal/пустой-root.
Логика импортируется из mcp_server (main под guard'ом) — stdio не нужен."""
import os
from project_context import indexer, receipt
from project_context import mcp_server as srv

_REPO=os.path.abspath(os.path.join(os.path.dirname(__file__),"..",".."))

def test_catalog_lists_registry_and_unguarded():
    c=srv.intent_catalog(_REPO)
    ids=[s["id"] for s in c["subsystems"]]
    assert "morning_brief" in ids and len(ids)>=16
    assert all(s["title"] for s in c["subsystems"])          # title из реестра, не пусто
    assert "МЕНЮ" in c["note"]                               # RO-B: каталог честно называет себя меню
    ung=[u["page"] for u in c["unguarded_pages"]["pages"]]
    assert "discovery_before_build" in ung                   # ADR виден в секции вне реестра
    assert "НЕ сторожится" in c["unguarded_pages"]["note"]

def test_page_guarded_matches_disk_and_statuses():
    r=srv.intent_page(_REPO,"morning_brief")
    assert r["guarded"] is True and r["invariants"] and r["code_anchors"]
    assert all(i["status"] for i in r["invariants"])         # статусы отдаются (не-holds видимы)
    disk=open(os.path.join(_REPO,"docs","explanation","morning_brief.md"),encoding="utf-8").read()
    assert r["page"]==disk                                   # байт-в-байт с диска, не пересказ

def test_page_unguarded_has_honest_note():
    r=srv.intent_page(_REPO,"discovery_before_build")
    assert r["guarded"] is False and "НЕ сторожится" in r["note"] and "mtime" in r["note"]

def test_page_negative_and_traversal():
    r=srv.intent_page(_REPO,"zzz_nope")
    assert "error" in r and "morning_brief" in r["known_ids"]
    for evil in ("../../project_context.json","../how-to/discovery_before_build",
                 "/etc/passwd","..%2F..%2Fsecrets"):
        rr=srv.intent_page(_REPO,evil)
        assert "error" in rr, evil                           # RO-A: наружу docs/explanation не выйти

def test_module_bridge_consistent_with_reverse_map():
    # вердикт-2 P3: subsystems = ПРЯМЫЕ носители (code_anchors), related_via_data = со-писатели;
    # объединение обязано совпадать с reverse-map L2 (широкая граница гейта не потеряна)
    ix=indexer.build(_REPO)
    rmap=receipt._reverse_map(ix)
    m,sids=sorted(rmap.items())[0]
    r=srv.intent_module(_REPO,m+".py")
    assert set(r["subsystems"])|set(r["related_via_data"])==set(sids)
    assert set(r["subsystems"])&set(r["related_via_data"])==set()   # разделение чистое
    assert "со-писатели" in r["note"]                               # агенту объяснено различие
    r2=srv.intent_module(_REPO,"zz_no_such_module")
    assert r2["subsystems"]==[] and "НЕ значит" in r2["note"]  # пусто ≠ «замысла нет»

def test_empty_root_is_honest():
    import tempfile
    r=srv.intent_catalog(tempfile.mkdtemp(prefix="pc_empty_"))
    assert r["registry"] is None and "не заведён" in r["note"]

def test_preflight_has_intent_pointer():
    ix=indexer.build(_REPO)
    d=indexer.preflight(ix,"morning_brief")
    assert "project_intent" in d                             # мост на форс-моменте (T2-реюз)

def test_registry_without_pages_is_first_class():
    # соседний проект v1: запись без explanation — не отказ, а честное «страницы нет» (RP-C)
    import tempfile, yaml
    root=tempfile.mkdtemp(prefix="pc_nopage_")
    open(os.path.join(root,"subsystem_intent.yaml"),"w",encoding="utf-8").write(yaml.safe_dump(
        [{"id":"demo","title":"Демо","intent":"замысел без страницы",
          "invariants":[{"id":"i1","claim":"держится","status":"holds"}]}],allow_unicode=True))
    c=srv.intent_catalog(root)
    assert [x["id"] for x in c["subsystems"]]==["demo"]
    r=srv.intent_page(root,"demo")
    assert r["guarded"] is True and r["page"] is None and "страницы нет" in r["note"]
    assert r["invariants"][0]["status"]=="holds"

def test_gate_output_reminds_catch_log():
    # RP-A: напоминание про catch-log в момент срабатывания. С F5 (2026-07-24) путь журнала —
    # политика проекта (manifest catch_log), дефолт живёт в indexer._manifest: сторожим ОБА звена.
    src=open(os.path.join(_REPO,"project_context","__main__.py"),encoding="utf-8").read()
    assert '["catch_log"]' in src                          # CLI печатает путь из политики
    from project_context import indexer as _ixm
    assert _ixm._manifest("/nonexistent")["catch_log"]=="docs/reference/duplicate_catch_log.md"  # дефолт жив



def _call(monkeypatch, capsys, name, args):
    """Один tools/call через НАСТОЯЩИЙ диспетчер main() — stdin/stdout подменены.
    Зовём диспетчер, а не функцию-предикат: щель жила в том, что на ПУТИ вызова
    intent-веток проверки не было, и тест на сам предикат был бы тавтологией."""
    import io, json as _j, sys as _sys
    req={"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":name,"arguments":args}}
    monkeypatch.setattr(_sys,"stdin",io.StringIO(_j.dumps(req)+"\n"))
    srv.main()
    return _j.loads(capsys.readouterr().out.strip().splitlines()[-1])["result"]


def test_мёртвый_корень_intent_отказывает_во_всех_формах(tmp_path, monkeypatch, capsys):
    """⭐ project_intent на мёртвом корне — ОТКАЗ, а не «реестр замысла не заведён».

    22.09.2026: `project-context-mm` смотрел в удалённый iCloud-путь соседнего проекта.
    capabilities отказывал (правка 14.09 стояла в build), а intent отвечал
    «реестра нет» при живом реестре в ~/neighbour-dev — агент соседнего проекта получал
    неправду о замысле. Три формы вызова, потому что две из них (каталог, id)
    шли мимо build, а третья (module) — через него.
    Позитивный контроль: живой пустой корень по-прежнему честно «не заведён» —
    иначе тест зеленел бы и на сервере, который отказывает всегда."""
    monkeypatch.setenv("PC_USAGE_LOG", str(tmp_path/"usage.jsonl"))   # §20: не писать в ~
    dead=str(tmp_path/"этого-каталога-нет")
    for args in ({"root":dead},{"root":dead,"id":"project_context"},
                 {"root":dead,"module":"mcp_server"}):
        r=_call(monkeypatch,capsys,"project_intent",args)
        assert r.get("isError") is True, args
        assert "не существует" in r["content"][0]["text"], args
    live=tmp_path/"живой"; live.mkdir()
    r=_call(monkeypatch,capsys,"project_intent",{"root":str(live)})
    assert not r.get("isError") and "не заведён" in r["content"][0]["text"]
