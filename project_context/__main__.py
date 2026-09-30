import os
import sys
from project_context import indexer
def main():
    a=sys.argv[1:]; cmd=a[0] if a else "preflight"
    if cmd=="check-precommit":
        from project_context import receipt
        block="--block" in a
        try:
            miss=receipt.check(".")
        except Exception as e:
            # fail-open: поломка инструмента не блокирует коммит (блок — только за пропуск ритуала).
            print(f"⚠ preflight check упал ({e}) — пропускаю (fail-open)")
            return
        if miss:
            tag="БЛОК" if block else "WARN"
            print(f"⛔ preflight {tag}: коммит трогает подсистемы без честной свежей квитанции (<6ч, на текущем HEAD): "+", ".join(miss))
            for m in miss:
                print(f"  прогони: /opt/homebrew/bin/python3.11 -m project_context preflight {m}")
            if block:
                print("  обход (осознанно, пропускает ВСЕ хуки): git commit --no-verify")
                sys.exit(3)
        return
    if cmd=="lessons":
        # Дом уроков проекта (project_context/lessons.py): корень — --root или git-корень cwd,
        # чтобы ритуалы звали одну и ту же команду из любого проекта.
        from project_context import lessons
        import subprocess
        root=a[a.index("--root")+1] if "--root" in a else (subprocess.run(
            ["git","rev-parse","--show-toplevel"],capture_output=True,text=True).stdout.strip() or ".")
        sys.exit(lessons.main(a[1:],root=root))
    if cmd=="roots-check":
        # Сторож адресов MCP-серверов в конфиге Claude Desktop (22.09). Зовётся из
        # post-commit MacBook: агент, который коммитит, — тот же, кто зовёт тулы.
        from project_context.mcp_server import dead_roots_in_config
        dead=dead_roots_in_config(*a[1:2])
        for name,root in dead:
            print(f"⛔ MCP-сервер {name!r} смотрит в несуществующий корень {root!r}: его тулы "
                  "отказывают. Поправь PROJECT_CONTEXT_ROOT в claude_desktop_config.json "
                  "и СРАЗУ перезапусти Claude Desktop (гипотеза 22.09: запущенное приложение "
                  "пишет конфиг из памяти и затирает внешнюю правку), "
                  "см. docs/how-to/discovery_before_build.md", file=sys.stderr)
        sys.exit(1 if dead else 0)
    if cmd=="dupgate":
        from project_context import dupgate
        try:
            blocks,warns=dupgate.check(".")
        except Exception as e:
            # fail-open: поломка инструмента не блокирует коммит (симметрия check-precommit)
            print(f"⚠ dupgate упал ({e}) — пропускаю (fail-open)")
            return
        for w in warns: print("⚠ дубль-кандидат по данным: "+w)
        if warns: print("  исход (ложняк/реальный дубль) → строка в "+indexer._manifest(".")["catch_log"])  # F5: путь — политика проекта
        if blocks:
            print("⛔ точный кросс-файл дубль имени (введён этим коммитом):")
            for b in blocks: print("  "+b)
            print("  Переиспользуй существующее: python3 -m project_context capabilities <имя|таблица>")
            print("  Осознанный дубль: dup_allowlist в project_context.json (+строка в catch-log)")
            if "--block" in a: sys.exit(4)
        return
    if cmd=="dispgate":
        from project_context import dispgate
        try:
            blocks,warns=dispgate.check(".")
        except Exception as e:
            # НЕ fail-open, в отличие от соседей. Отказ ОКРУЖЕНИЯ (git недоступен) этот гейт
            # выражает СТРУКТУРНО — staged.snapshot возвращает ok=False, check отдаёт ([],[]) и
            # коммит проходит по общей политике семьи. Значит исключение здесь означает ровно одно:
            # БАГ ВНУТРИ ГЕЙТА или негодный вход, который не распознан. Раньше общий except делал
            # такое разрешением, то есть любой будущий дефект в этом коде был бы тихим по умолчанию
            # (ревью 2026-07-27, F-02/F-05: две разные ошибки типа в JSON — одинаковый тихий проход).
            # §13 ступень 2: безопасного авто-действия нет, но остановить вредное верно.
            import traceback
            print("⛔ гейт одноразовости: ВНУТРЕННЯЯ ОШИБКА — блокирую, а не пропускаю.")
            print(f"  {type(e).__name__}: {e}")
            print("  Это не отказ окружения (его гейт отдаёт структурно), а дефект гейта либо")
            print("  негодный вход, который он не распознал. Осознанный обход: git commit --no-verify")
            traceback.print_exc()
            if "--block" in a: sys.exit(6)
            return
        for w in warns: print("⚠ одноразовость: "+w)
        if blocks:
            print("⛔ гейт одноразовости (CLAUDE.md §15): НОВЫЙ модуль не готов —")
            for b in blocks: print("  "+b)
            print("  чек-лист и скелет вердикта: python3 -m project_context disposability <модуль>")
            print("  Осознанно НЕ одноразовый — законно: verdict=not_disposable + cause в сайдкаре;")
            print("  блокирует отсутствие суждения, а не его знак.")
            print("  ГРАНИЦА: проверки ловят отсутствие НОСИТЕЛЯ, не его качество — зелёный гейт")
            print("  доказывает, что суждение вынесено и записано, а не что модуль одноразов.")
            if "--block" in a: sys.exit(6)   # 3=preflight, 4=dupgate/gen_tc, 5=forward-ref-страж
        return
    if cmd=="intentgate":
        from project_context import intentgate
        try:
            blocks,warns=intentgate.check(".")
        except Exception as e:
            # НЕ fail-open — та же дисциплина, что у dispgate: отказ ОКРУЖЕНИЯ гейт выражает
            # СТРУКТУРНО (snapshot ok=False → WARN и пропуск), поэтому исключение здесь означает
            # дефект гейта либо негодный вход (нечитаемый реестр, дыра в _PATHSPEC). Тихий проход
            # по нему сделал бы тихим каждый будущий дефект (урок ревью dispgate 2026-07-27).
            import traceback
            print("⛔ квитанция замысла: ВНУТРЕННЯЯ ОШИБКА — блокирую, а не пропускаю.")
            print(f"  {type(e).__name__}: {e}")
            print("  Осознанный обход: git commit --no-verify")
            traceback.print_exc()
            if "--block" in a: sys.exit(7)
            return
        for w in warns: print("⚠ "+w)
        if blocks:
            print("⛔ квитанция замысла (нить intent-receipts): артефакт закрытия не готов —")
            for b in blocks: print("  "+b)
            print("  Формат и образцы: docs/reference/intent_receipt_format.md (нормативный дом);")
            print("  живые статусы — в subsystem_intent.yaml затронутых записей.")
            print("  ГРАНИЦА: гейт проверяет, что квитанция собрана с живого реестра, —")
            print("  не что замысел понят и не что вердикт сверки верен.")
            if "--block" in a: sys.exit(7)   # 3=preflight, 4=dupgate/gen_tc, 5=forward-ref, 6=dispgate
        return
    if cmd=="disposability":
        p=indexer.disposability_policy(".")
        if p["degraded"]: print("⚠ движковый чек-лист НЕ прочитан — методология пуста, это не «ок»")
        print("КРИТЕРИЙ: "+p["criterion"]); print("ВОПРОС РЕВЬЮ: "+p["review_question"]); print()
        for i in p["items"]:
            print(f"  {i['id']} · {i['text']}")
            print(f"       оракул: {i['oracle']}"+(f" · авточек: {i['check']}" if i["check"] else " · машина НЕ проверяет"))
        print("\nГРАНИЦА: "+p["machine_scope"])
        # Ключ модуля — путь-форма БЕЗ .py, ровно как его считает indexer.contract_sidecars
        # (и как устроен индекс). Нормализуем ЗДЕСЬ, в единственной точке чтения аргумента:
        # 2026-07-30 сайдкар, написанный по этому скелету дословно, оказался гейту НЕВИДИМ —
        # скелет печатал `"module": "plans/x.py"` и имя файла `x.py.json`, а гейт искал ключ
        # `plans/x`. Инструмент, печатающий подсказку, ошибался ровно в том, что подсказывал.
        # Ратчет соответствия: tests/consistency/test_contract_sidecar_keys.py
        mod=a[1] if len(a)>1 else None
        if mod and mod.endswith(".py"):
            mod=mod[:-3]
        if mod:
            import json as _j
            cs,_b=indexer.contract_sidecars(".")
            print(f"\nсайдкар contracts/{mod}.json: "+("ЕСТЬ" if mod in cs else "НЕТ, скелет ниже"))
            if mod not in cs:
                print(_j.dumps({"module":mod,"public":[],"depends_on":[{"module":"","uses":[],"why":""}],
                    "disposability":{"verdict":"disposable|not_disposable",
                    "items":{i["id"]:"" for i in p["items"]},"rationale":"","date":"","oracle":"",
                    "cause":"<стабильный ключ, ТОЛЬКО при not_disposable>"}},ensure_ascii=False,indent=2))
        return
    ix=indexer.build(".")
    if cmd=="scope":
        print(indexer.preflight(ix,a[1],budget=False))
    elif cmd=="preflight" or not a:
        print(indexer.preflight(ix, a[1] if len(a)>1 else "memory"))
    elif cmd=="capabilities":
        import json as _j
        _arg=a[1] if len(a)>1 else ""
        _text=_j.dumps(indexer.capabilities(ix, _arg), ensure_ascii=False, indent=1)
        print(_text)
        # Журнал ЗАМЕРА ведёт и CLI, не только MCP-канал (14.09). До этой строки
        # `_usage` звал ровно один вызывающий — `mcp_server`, — и журнал описывал
        # не привычку звать discovery, а привычку звать её ЧЕРЕЗ MCP-тул.
        # Цена названа замером: из 27 коммитов, вводивших новый судимый `.py` за 51
        # день, у 18 нашёлся вызов capabilities в сутки до коммита, у 9 — нет. Но
        # «нет» тут неразличимо с «звали из терминала», поэтому инвариант
        # `discovery_called_before_build` по таким данным закрыть НЕЛЬЗЯ ни в одну
        # сторону. Дом функции остаётся один (`mcp_server._usage`), здесь только
        # второй вызывающий: своя запись формата развела бы журнал надвое.
        try:
            from project_context.mcp_server import _usage
            _usage("project_capabilities", {"query": _arg}, os.getcwd(),
                   len(_text.encode("utf-8")))
        except Exception:  # silent-ok: замер не имеет права ломать инструмент
            pass
    else:
        print("usage: python3 -m project_context preflight|scope <subsystem> | capabilities <имя|таблица> | dupgate [--block] | dispgate [--block] | intentgate [--block] | disposability [<модуль>] | check-precommit")
main()
