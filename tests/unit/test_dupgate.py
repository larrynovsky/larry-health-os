"""Контроли дубль-гейта (УСЛОВИЕ ВКЛЮЧЕНИЯ, RN-B/RN-E): позитивный ДОЛЖЕН зажечь,
негативный ДОЛЖЕН молчать. evaluate() чистая — источники инжектятся, git не нужен."""
import os
from project_context import indexer, dupgate

_REPO=os.path.abspath(os.path.join(os.path.dirname(__file__),"..",".."))
ix=indexer.build(_REPO)

def test_block_seed_exact_cross_file_dup():
    # позит-контроль BLOCK: staged вводит имя, уже живущее в другом модуле (F-106-класс)
    b,w=dupgate.evaluate(ix,{"newmod.py":"def get_active_protocols():\n    return 1\n"},{})
    assert b and "get_active_protocols" in b[0] and "protocols_db" in b[0]

def test_silent_on_unique_new_function():
    # негат-контроль: уникальное имя без SQL → полная тишина (иначе гейт самоубьётся, RN-B)
    b,w=dupgate.evaluate(ix,{"newmod.py":"def zz_unique_capability_xyz_2026():\n    return 1\n"},{})
    assert not b and not w

def test_warn_seed_dup_by_data():
    # позит-контроль WARN: новая функция в чужую таблицу → кандидаты по данным
    src='def load_all_protocols_v2():\n    q="SELECT * FROM protocols"\n    return q\n'
    b,w=dupgate.evaluate(ix,{"newmod.py":src},{})
    assert not b
    assert any("`protocols`" in x and "protocols_db" in x for x in w)

def test_legacy_untouched():
    # RN-E: имя было в HEAD-версии файла → не «введено», гейт молчит на легаси
    src="def get_active_protocols():\n    return 1\n"
    b,w=dupgate.evaluate(ix,{"hai_hypotheses.py":src},{"hai_hypotheses.py":src})
    assert not b

def test_allowlist_and_conventions_skip():
    b,_=dupgate.evaluate(ix,{"n.py":"def get_active_protocols():\n    return 1\n"},{},
                         allow={"get_active_protocols"})
    assert not b
    b2,_=dupgate.evaluate(ix,{"n.py":"def main():\n    return 1\n"},{})
    assert not b2                                            # конвенция, не дубль

def test_own_table_no_warn():
    # модуль уже обслуживал таблицу в HEAD → новая функция к своей таблице не шумит (RN-B)
    head='def old():\n    return "SELECT a FROM protocols"\n'
    b,w=dupgate.evaluate(ix,{"m.py":head+'def newer():\n    return "SELECT b FROM protocols"\n'},
                         {"m.py":head})
    assert not w

def test_methods_not_judged():
    # методы классов — интерфейс: одинаковые имена легитимны, гейт не судит
    src="class X:\n    def get_active_protocols(self):\n        return 1\n"
    b,w=dupgate.evaluate(ix,{"n.py":src},{})
    assert not b

def test_capabilities_scope_marker():
    # RN-A: вывод discovery обязан нести scope-пометку; пусто ≠ «дублей нет»
    r=indexer.capabilities(ix,"qxqzvqxqz9")
    assert "scope" in r and "не" in r["scope"].lower()
    assert not r["by_name"] and not r["by_data"]             # пусто, но с пометкой

def test_precommit_wiring_contains_dupgate():
    # Сторож вирирования (Follow-up 3): unit-контроли гейта зелены и при удалённой
    # секции хука — этот чек красит suite, если dupgate отключили от pre-commit.
    hook=open(os.path.join(_REPO,"scripts","git-hooks","pre-commit"),encoding="utf-8").read()
    assert "-m project_context dupgate --block" in hook
    assert "_dg_rc -eq 4" in hook                     # обработка exit 4 (блок) на месте

def test_mcp_server_smoke_known_case():
    # Сторож MCP (Follow-up 4): сервер поднимается и отвечает known-case'ом (F-106)
    # со scope-пометкой (RN-A). Ловит битый сервер ДО того, как тул тихо исчезнет.
    import json as _json, subprocess as _sp, sys
    inp=('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n'
         '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}\n'
         '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"project_capabilities","arguments":{"query":"protocols"}}}\n'
         '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"project_intent","arguments":{"id":"morning_brief"}}}\n')
    import tempfile
    fd,ulog=tempfile.mkstemp(prefix="pc_usage_"); os.close(fd)   # не mktemp (CodeQL #5)
    env=dict(os.environ,PYTHONPATH=_REPO,PROJECT_CONTEXT_ROOT=_REPO,PC_USAGE_LOG=ulog)
    out=_sp.run([sys.executable,"-m","project_context.mcp_server"],input=inp,
                capture_output=True,text=True,env=env,timeout=120).stdout.strip().splitlines()
    assert len(out)==4
    names={t["name"] for t in _json.loads(out[1])["result"]["tools"]}
    assert names=={"project_capabilities","project_intent"}          # оба тула видимы
    txt=_json.loads(out[2])["result"]["content"][0]["text"]
    assert "get_active_protocols" in txt and '"scope"' in txt
    txt2=_json.loads(out[3])["result"]["content"][0]["text"]
    assert "morning_brief" in txt2 and "invariants" in txt2          # intent сквозь stdio
    lines=[_json.loads(l) for l in open(ulog,encoding="utf-8")]      # замер пишется (2026-07-24)
    assert len(lines)==2 and all(l["resp_bytes"]>0 for l in lines)
    assert {l["tool"] for l in lines}=={"project_capabilities","project_intent"}


def test_collision_basenames_indexed_separately(tmp_path):
    # F1-контроль (внешний вердикт 2026-07-24): файлы-тёзки в разных пакетах — в соседнем проекте
    # 5×runner.py; basename-ключ индексировал ОДИН и путал file:line. Оба видны, пути честные.
    (tmp_path/"alpha").mkdir(); (tmp_path/"beta").mkdir()
    (tmp_path/"alpha"/"runner.py").write_text("def do_alpha():\n    return 1\n")
    (tmp_path/"beta"/"runner.py").write_text("def do_beta():\n    return 2\n")
    ix2=indexer.build(str(tmp_path))
    got={(fn,p,ln) for fn,_,p,ln in indexer.capabilities(ix2,"do_")["by_name"]}
    assert ("do_alpha","alpha/runner.py",1) in got and ("do_beta","beta/runner.py",1) in got  # (имя, файл, СТРОКА)
    # и гейт видит тёзку СКВОЗЬ пакеты: та же функция в третьем файле → BLOCK с честным адресом
    b,_=dupgate.evaluate(ix2,{"gamma/runner.py":"def do_alpha():\n    return 3\n"},{})
    assert b and "alpha/runner.py" in b[0]

def test_file_data_anchor_discovery(tmp_path):
    # F2-контроль (внешний вердикт 2026-07-24): способность, заякоренная на ФАЙЛ данных
    # (не SQL-таблицу), видна by_data — и дубль-гейт предупреждает по такому якорю.
    (tmp_path/"writer.py").write_text('def save_gloss():\n    open("glossary/terms.jsonl","a").write("x")\n')
    (tmp_path/"reader.py").write_text('def load_gloss():\n    return open("glossary/terms.jsonl").read()\n')
    ix2=indexer.build(str(tmp_path))
    r=indexer.capabilities(ix2,"glossary")
    assert "glossary/terms.jsonl" in r["by_data"]
    assert {m for m,_ in r["by_data"]["glossary/terms.jsonl"]}=={"writer","reader"}
    _,w=dupgate.evaluate(ix2,{"newmod.py":'def dump_terms():\n    open("glossary/terms.jsonl","a").write("y")\n'},{})
    assert any("glossary/terms.jsonl" in x for x in w)

def test_dir_anchor_via_join(tmp_path):
    # F2-контроль, каталог-якорь: соседний проект ходит в данные через os.path.join("people", …) —
    # путь доказан синтаксической позицией (join/Path), голое слово путём не считается.
    (tmp_path/"scanner.py").write_text('import os\ndef scan_people():\n    return os.path.join("people","x")\n')
    (tmp_path/"cards.py").write_text('import os\ndef load_card(s):\n    return os.path.join("people",s,"card.md")\n')
    (tmp_path/"pure.py").write_text('def greet():\n    return "people are nice"\n')   # НЕ якорь: слово вне join
    ix2=indexer.build(str(tmp_path))
    r=indexer.capabilities(ix2,"people")
    assert "people/" in r["by_data"]
    assert {m for m,_ in r["by_data"]["people/"]}=={"scanner","cards"}

def test_anchor_propagates_via_imported_constant(tmp_path):
    # F2-контроль, one-hop: реальный потребитель ходит через `from config import PEOPLE_DIR` —
    # якорь обязан дотянуться до него, иначе by_data показывает только config.py (полуслепота).
    (tmp_path/"config.py").write_text('from pathlib import Path\nROOT=Path("base")\nPEOPLE_DIR=ROOT/"people"\n')
    (tmp_path/"archiver.py").write_text('from config import PEOPLE_DIR\ndef organize():\n    return PEOPLE_DIR\n')
    ix2=indexer.build(str(tmp_path))
    r=indexer.capabilities(ix2,"people")
    assert "people/" in r["by_data"]
    assert {m for m,_ in r["by_data"]["people/"]}>={"config","archiver"}

def test_engine_sees_itself():
    # вердикт-3 P1 (2026-07-24, «проспан»): project_context был в SKIP — движок не видел
    # сам себя, второй анти-дубль-контур был бы невидим discovery (дыра класса «Этап 0 ТЗ»).
    r=indexer.capabilities(ix,"canonical_token")
    hits=[e for e in r["by_name"] if e[0]=="canonical_token"]
    assert hits and hits[0][2]=="project_context/indexer.py" and hits[0][3]>0
