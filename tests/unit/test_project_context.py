"""Инварианты спайна project_context (AST call-graph, membership, D3 клин-гейт, структура-only)."""
import os
from project_context import indexer

_REPO=os.path.abspath(os.path.join(os.path.dirname(__file__),"..",".."))
ix=indexer.build(_REPO)

def test_callgraph_resolves_internal_call():
    assert "memory_consolidation" in {c for c,ts,nm in ix["edges"] if nm=="propose"}

def test_membership_memory_is_tight():
    seed,mem,oneoff,hits=indexer.members(ix,"memory")
    assert {"memory_facts_db","memory_consolidation","memory_truthcheck"} <= mem
    assert "health_db" not in mem                      # нет взрыва замыкания
    assert any("backfill" in m for m in oneoff)        # одноразовые вынесены

def test_clinical_gate_discriminates():
    assert "CLINICAL" in indexer.preflight(ix,"memory")
    assert "CLINICAL" not in indexer.preflight(ix,"validation_gate")
    assert "CLINICAL" in indexer.preflight(ix,"genome_effect_allele")  # бывший ложный негатив

def test_digest_is_structural_only():
    d=indexer.preflight(ix,"memory")
    assert "def " not in d and "= " not in d           # нет исходного кода/значений (I-8/D1)

def test_corpus_false_paths_wired():
    assert "C-04" in indexer.preflight(ix,"memory")

def test_receipt_key_space_aligned():
    # L2-регрессия: preflight ПИШЕТ квитанцию по intent-id (hits), а check() ИЩЕТ
    # по reverse-map. Если пространства ключей разойдутся — вечный WARN (баг был:
    # писали по имени-арга «memory», искали по «memory_temporal_axis»). Инвариант:
    # все значения reverse-map — реальные intent-id, т.е. та же ось, по которой пишет emit.
    from project_context import receipt
    rmap=receipt._reverse_map(ix)
    assert rmap                                        # непусто
    assert all(sids for sids in rmap.values())
    assert all(sid in ix["intent"] for sids in rmap.values() for sid in sids)

def test_receipt_freshness_roundtrip():
    # emit пишет квитанцию под <root>/.git/preflight_receipts/<sub>; свежая (<TTL)
    # → check() НЕ считает подсистему пропущенной. Проверяем инвариант напрямую.
    # tempfile, а не pytest-fixture tmp_path — чтобы работал и __main__-раннер ниже.
    import time, tempfile
    from project_context import receipt
    root=tempfile.mkdtemp(prefix="pc_receipt_")
    indexer.emit_receipt(root,"memory_temporal_axis","deadbeef")
    rf=os.path.join(root,".git","preflight_receipts","memory_temporal_axis")
    assert os.path.exists(rf)
    assert time.time()-os.path.getmtime(rf) < receipt.TTL_H*3600

def test_enrich_matches_traceback():
    # L0a: указатель строится по ТРЕЙСБЕКУ (реальный файл), не по имени теста.
    from project_context import enrich
    tb='File "gp_agent.py", line 42, in morning_brief\n    x=compute()\nValueError'
    out=enrich.pointer_for(tb,_REPO)
    assert "PREFLIGHT" in out and "gp_agent" in out   # карта задетой подсистемы вклеена

def test_enrich_negative_control_no_cry_wolf():
    # текст без модуля проекта → пусто, а не выдуманный маршрут (детект «крика волк»).
    from project_context import enrich
    assert enrich.pointer_for("generic AssertionError: 1 != 2",_REPO)==""

def test_enrich_never_raises():
    # best-effort: битый корень/мусор не смеет ронять вызывающего → ''.
    from project_context import enrich
    assert enrich.pointer_for(None,"/nonexistent/root/xyz")==""
    assert enrich.pointer_for("File \"gp_agent.py\"","/nonexistent/root/xyz")==""

def test_canonical_token_shape_and_determinism():
    # токен детерминирован, 16 hex, и РАЗНЫЙ у разных подсистем (не константа).
    t=indexer.canonical_token(ix,"memory_temporal_axis")
    assert t==indexer.canonical_token(ix,"memory_temporal_axis")
    assert len(t)==16 and all(c in "0123456789abcdef" for c in t)
    assert indexer.canonical_token(ix,"proactivity")!=t

def test_receipt_validity_honest_forge_stale():
    # L2-block ядро: честная=валидна, touch-подделка=невалидна, протухшая=невалидна.
    import tempfile, time as _t
    from project_context import receipt
    root=tempfile.mkdtemp(prefix="pc_v_"); S="memory_temporal_axis"
    d=os.path.join(root,".git","preflight_receipts"); os.makedirs(d)
    rf=os.path.join(d,S)
    open(rf,"w",encoding="utf-8").write(indexer.canonical_token(ix,S)+"\n")
    assert receipt.receipt_valid(ix,root,S) is True          # честная свежая
    open(rf,"w",encoding="utf-8").write("forged\n")
    assert receipt.receipt_valid(ix,root,S) is False         # подделка touch'ем
    open(rf,"w",encoding="utf-8").write(indexer.canonical_token(ix,S)+"\n")
    old=_t.time()-(receipt.TTL_H*3600+60); os.utime(rf,(old,old))
    assert receipt.receipt_valid(ix,root,S) is False         # верный токен, но протух


def test_orchestrator_carrier_in_model():
    # carrier-fix 2026-07-23: .sh-оркестратор self_monitoring в модели, не сирота
    # (класс feedback_guard_filtered_wrong_carrier — сторож не фильтрует носителя)
    assert "self_monitoring" in ix.get("orch",{}).get("run_checks.sh",set())


def test_orchestrator_gated_without_receipt(monkeypatch):
    # staged run_checks.sh без честной свежей квитанции → self_monitoring в miss-листе
    from project_context import receipt
    monkeypatch.setattr(receipt.subprocess,"check_output",lambda *a,**k:b"run_checks.sh\n")
    assert "self_monitoring" in receipt.check(_REPO)

def test_capabilities_finds_dup_by_name_and_data():
    # Фундамент discovery-до-постройки: дубль виден и по имени, и — ключевое — ПО ДАННЫМ
    # (под другим именем). Регресс на реальный F-106 (get_active_protocols ×2).
    r=indexer.capabilities(ix,"get_active_protocols")
    nm={(fn,m) for fn,m,*_ in r["by_name"]}   # by_name = 4-кортеж (имя,модуль,файл,строка) с вердикт-2; берём (имя,модуль)
    assert ("get_active_protocols","protocols_db") in nm
    assert ("get_active_protocols","hai_hypotheses") in nm      # кросс-файл дубль виден
    r2=indexer.capabilities(ix,"protocols")
    funcs=[f.split(":")[0] for m,fns in r2["by_data"].get("protocols",[]) for f in fns]   # by_data = «имя:строка» (вердикт-2); берём имя
    assert "get_active_protocols" in funcs                       # дубль под другим именем — по данным


def test_orchestrators_followup_declared():
    # carrier-fix follow-up 2026-07-23: прочие .sh-оркестраторы под квитанцией
    orch=ix.get("orch",{})
    assert "self_monitoring" in orch.get("run_full_test_suite.sh",set())
    assert "self_monitoring" in orch.get("watch_and_test.sh",set())
    assert "data_ingestion" in orch.get("watch_and_import.sh",set())

# ── Э1 · чек-лист одноразовости: пункты в движке, числа в политике, ОДИН ридер (§15) ──

def test_disposability_checklist_is_whole_and_wired():
    p=indexer.disposability_policy(_REPO)
    assert p["degraded"] is False and p["unknown"]==[]
    assert [i["id"] for i in p["items"]]==["D1","D2","D3","D4","D5","D6"]
    # связка чек-лист ↔ авточеки: переименуют K-код — упадёт ЗДЕСЬ, а не гейт молча раззявится
    assert {i["id"]:i["check"] for i in p["items"]}=={"D1":None,"D2":"K1","D3":"K3","D4":"K4","D5":"K2","D6":None}
    assert p["criterion"].startswith("Единица безопасности")
    assert set(p["limits"])=={"public_surface_warn","module_lines_warn","cause_repeat_weeks","cause_knock_max_per_run"}

def test_disposability_project_overrides_and_shouts_on_typo():
    # проектная политика переопределяет ЧИСЛА; опечатка в ключе не тонет молча (unknown)
    import tempfile, json as _j
    root=tempfile.mkdtemp(prefix="pc_disp_")
    _j.dump({"disposability":{"limits":{"cause_repeat_weeks":5,"couse_repeat_weeks":9}}},
            open(os.path.join(root,"project_context.json"),"w",encoding="utf-8"))
    p=indexer.disposability_policy(root)
    assert p["limits"]["cause_repeat_weeks"]==5           # проект переопределил
    assert p["limits"]["module_lines_warn"]==400          # прочее — дефолт движка
    assert p["unknown"]==["couse_repeat_weeks"]           # опечатка видна

def test_disposability_no_policy_is_neutral_not_degraded():
    # чужой проект без своей политики (напр. соседний проект): методология и числа приезжают с движком.
    # «нет файла политики» ≠ «нет методологии» — иначе гейт был бы с пустыми порогами.
    import tempfile
    p=indexer.disposability_policy(tempfile.mkdtemp(prefix="pc_nopol_"))
    assert p["degraded"] is False and p["limits"]["cause_repeat_weeks"]==3

def test_disposability_degraded_is_loud_not_silent():
    # позит-контроль fail-static: движкового файла НЕТ → degraded, а не тихие пустые пороги
    import tempfile
    p=indexer.disposability_policy(_REPO,engine_dir=tempfile.mkdtemp(prefix="pc_broken_"))
    assert p["degraded"] is True and p["items"]==[]

def test_disposability_single_reader_source_guard():
    # Дубль значения = split-brain. Инвариант: движковый файл читает РОВНО один модуль и имена
    # лимитов не рассыпаны по коду литералами. Scope честный: греп ловит обход через ИМЯ файла
    # или ключа, но НЕ того, кто впишет само число 400 — это остаётся на вердикте, не на машине.
    allowed={"project_context/indexer.py","project_context/dispgate.py"}
    keys=("disposability.json","cause_repeat_weeks","public_surface_warn","module_lines_warn")
    bad=[]
    for rel in ix["path_of"].values():
        if rel in allowed: continue
        try: src=open(os.path.join(_REPO,rel),encoding="utf-8").read()
        except Exception: continue                        # silent-ok: нечитаемый файл не судим
        bad += [f"{rel}: {k}" for k in keys if k in src]
    assert not bad, "чтение в обход ридера: "+", ".join(bad)

# ── Э2 · сайдкар-контракты: ридер ОДИН, живёт в движке (зовут check_contracts и dispgate) ──

def test_contract_sidecars_shape_and_key_matches_path():
    cs,bad=indexer.contract_sidecars(_REPO)
    assert not bad, f"нечитаемые сайдкары: {bad}"
    assert len(cs)>=10                                   # мигрированные 10, presence не точный счёт
    for key,v in cs.items():
        assert v["path"]=="contracts/"+key+".json", f"ключ и путь разъехались: {key} vs {v['path']}"
        assert v["public"], f"{key}: пустая публичная поверхность"
        assert "disposability" in v                       # ключ есть всегда; None = вердикта нет (легаси)

def test_contract_sidecars_cover_migrated_ten():
    # пин миграции Э2: удаление сайдкара уронит ЗДЕСЬ, а не молча сузит контракт до нуля
    cs,_=indexer.contract_sidecars(_REPO)
    assert {"health_db","health_ai","gp_agent","gp_context","hai_core","checkin_agent",
            "task_agent","genome_context","calendar_client","safety_net"} <= set(cs)
    assert "get_conn" in cs["health_db"]["public"] and len(cs["health_db"]["public"])>=80
    assert cs["health_db"]["disposability"] is None       # легаси: вердикт не выносился

def test_contract_sidecars_bad_json_reported_not_swallowed():
    # позит-контроль: битый json = «контракт НЕ прочитан», а не «контракта нет» (тихо зелено)
    import tempfile
    root=tempfile.mkdtemp(prefix="pc_badsc_"); d=os.path.join(root,"contracts"); os.makedirs(d)
    open(os.path.join(d,"broken.json"),"w",encoding="utf-8").write("{не json")
    cs,bad=indexer.contract_sidecars(root)
    assert cs=={} and len(bad)==1 and bad[0].startswith("broken.json")

def test_contract_sidecars_absent_dir_is_neutral():
    # чужой проект без каталога: пусто и без исключения (движок домен-агностичен)
    import tempfile
    assert indexer.contract_sidecars(tempfile.mkdtemp(prefix="pc_nosc_"))==({},[])

# ── Э6 · счётчик причин: НЕДЕЛИ, не модули; разобранное молчит ────────────────

def _causes_root(entries, resolved=None):
    """entries: [(ключ-модуля, дата, причина|None)] → временный корень с сайдкарами и политикой."""
    import tempfile, json as _j
    root=tempfile.mkdtemp(prefix="pc_cz_"); d=os.path.join(root,"contracts"); os.makedirs(d)
    for key,date,cause in entries:
        disp={"verdict":"not_disposable" if cause else "disposable","rationale":"r","date":date,
              "oracle":"agent","items":{}}
        if cause: disp["cause"]=cause
        _j.dump({"module":key,"public":["f"],"depends_on":[],"disposability":disp},
                open(os.path.join(d,key+".json"),"w",encoding="utf-8"))
    if resolved is not None:
        _j.dump({"disposability":{"resolved_causes":resolved}},
                open(os.path.join(root,"project_context.json"),"w",encoding="utf-8"))
    return root

def test_causes_count_weeks_not_modules():
    # РЕГРЕСС на инфляцию 37:1 — разрез health_db дал 37 модулей за ОДИН день с одной причиной.
    # Счётчик по модулям принял бы одну работу за закономерность. Считаем недели.
    root=_causes_root([(f"m{i}","2026-07-20","shared_table") for i in range(37)])
    cz=indexer.disposability_causes(root)
    assert cz["shared_table"]["count"]==1, "37 модулей одного дня посчитаны как повторы"
    assert len(cz["shared_table"]["modules"])==37
    assert cz["shared_table"]["over_threshold"] is False

def test_causes_cross_threshold_on_three_distinct_weeks():
    root=_causes_root([("a","2026-07-06","shared_table"),("b","2026-07-14","shared_table"),
                       ("c","2026-07-21","shared_table")])
    cz=indexer.disposability_causes(root)
    assert cz["shared_table"]["count"]==3 and cz["shared_table"]["over_threshold"] is True
    # две недели порога не проходят: пара случается сама (один рефактор живёт две недели)
    root2=_causes_root([("a","2026-07-06","shared_table"),("b","2026-07-14","shared_table")])
    assert indexer.disposability_causes(root2)["shared_table"]["over_threshold"] is False

def test_causes_resolved_mark_silences_forever():
    # повторный стук по разобранному = тренировка игнорировать сигналы
    ent=[("a","2026-07-06","shared_table"),("b","2026-07-14","shared_table"),("c","2026-07-21","shared_table")]
    cz=indexer.disposability_causes(_causes_root(ent,resolved={"shared_table":{"date":"2026-07-26","verdict":"живём так"}}))
    assert cz["shared_table"]["over_threshold"] is False and cz["shared_table"]["resolved"]

def test_causes_ignore_positive_verdicts():
    root=_causes_root([("a","2026-07-06",None),("b","2026-07-14",None)])
    assert indexer.disposability_causes(root)=={}

def test_causes_lesson_renders_in_preflight():
    # Сторож против МЁРТВОЙ РЕЛЬСЫ: счётчик может быть верным, а урок никуда не печататься.
    # Проверяем доставку в точке потребления — preflight того, кто пойдёт здесь работать.
    import tempfile, json as _j
    root=tempfile.mkdtemp(prefix="pc_lesson_"); os.makedirs(os.path.join(root,"contracts"))
    for i,date in enumerate(("2026-07-06","2026-07-14","2026-07-21"),start=1):
        open(os.path.join(root,f"m{i}.py"),"w",encoding="utf-8").write("def f():\n    return 1\n")
        _j.dump({"module":f"m{i}","public":["f"],"depends_on":[],
                 "disposability":{"verdict":"not_disposable","cause":"shared_table_dumping_ground",
                                  "rationale":"r","date":date,"oracle":"agent","items":{}}},
                open(os.path.join(root,"contracts",f"m{i}.json"),"w",encoding="utf-8"))
    open(os.path.join(root,"subsystem_intent.yaml"),"w",encoding="utf-8").write(
        "\n- id: sub1\n  code_anchors: [m1.py::f, m2.py::f, m3.py::f]\n")
    ix2=indexer.build(root)
    out=indexer.preflight(ix2,"sub1")
    assert "НЕ ОДНОРАЗОВЫЙ" in out, "урок не доставлен в preflight (мёртвая рельса)"
    assert "shared_table_dumping_ground" in out and "3 нед." in out and "ПОРОГ ПРОЙДЕН" in out

if __name__=="__main__":
    import traceback,sys; f=0
    for n,fn in sorted(globals().items()):
        if n.startswith("test_") and callable(fn):
            try: fn(); print("OK  ",n)
            except Exception: print("FAIL",n); traceback.print_exc(); f+=1
    print("ИТОГ:", "ВСЕ ПРОШЛИ" if not f else f"{f} провал"); sys.exit(1 if f else 0)
