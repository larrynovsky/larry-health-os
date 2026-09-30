"""Контроли гейта одноразовости (CLAUDE.md §15). Гейт включается ТОЛЬКО когда зелены ОБА:
позит-контроль РАЗДЕЛЬНО по каждому пункту (падает ли на вводящем дефекте) и негат-контроль
(молчит ли на соответствующем модуле). Зелёный гейт без позит-контроля неотличим от гейта,
который ничего не проверяет — класс genome fake-green.

Контроли инжектят источники в чистую evaluate_new_modules и НЕ трогают git.
"""
import os
from project_context import indexer, dispgate

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ix = indexer.build(_REPO)
POLICY = indexer.disposability_policy(_REPO)

CLEAN_SRC = '''"""Соответствующий модуль."""
def do_thing(x):
    return x + 1
'''
CLEAN_TEST = {"tests/unit/test_zz_new.py": "import zz_new\ndef test_it():\n    assert zz_new.do_thing(1)==2\n"}


def _sidecar(public=("do_thing",), verdict="disposable", cause=None, drop=None, items=True):
    d = {"verdict": verdict, "rationale": "домен один, поверхность одна функция, тест зовёт её",
         "date": "2026-07-26", "oracle": "agent"}
    if cause:
        d["cause"] = cause
    if items:
        d["items"] = {i["id"]: "ок" for i in POLICY["items"]}
    for f in (drop or ()):
        d.pop(f, None)
    return {"zz_new": {"public": list(public), "depends_on": [], "disposability": d,
                       "path": "contracts/zz_new.json"}}


def _run(src=CLEAN_SRC, sidecars=None, tests=None, policy=None):
    return dispgate.evaluate_new_modules(ix, {"zz_new.py": src},
                                         CLEAN_TEST if tests is None else tests,
                                         _sidecar() if sidecars is None else sidecars,
                                         policy or POLICY)


# ── негат-контроль: соответствующий модуль проходит молча ──────────────────────

def test_negative_control_clean_module_passes():
    blocks, warns = _run()
    assert blocks == [], f"ложняк на соответствующем модуле: {blocks}"
    assert warns == []


def test_negative_control_nothing_added_means_nothing_judged():
    blocks, _ = dispgate.evaluate_new_modules(ix, {}, CLEAN_TEST, _sidecar(), POLICY)
    assert blocks == []


# ── периметр: исключение по ОТСУТСТВИЮ ПРЕДМЕТА, а не по имени файла ──────────
# Прежняя редакция этого раздела называлась «non_py_and_private_are_out_of_perimeter» и
# ЗАМОРАЖИВАЛА обход DG-03 как желаемое поведение. Здесь проверяется правило, а границу
# самого периметра (какие файлы вообще доезжают до evaluate) стережёт интеграционный
# контроль на настоящем хуке — из этого файла её проверить нельзя в принципе.

def test_init_without_definitions_is_out_of_perimeter():
    src = "from zz_pkg.core import do_thing  # ре-экспортный шов, поверхности нет\n"
    blocks, _ = dispgate.evaluate_new_modules(ix, {"zz_pkg/__init__.py": src}, {}, {}, POLICY)
    assert blocks == [], f"пустой __init__.py судить нечего: {blocks}"


def test_init_with_definition_is_judged_like_any_module():
    src = "def pkg_entry(x):\n    return x\n"
    blocks, _ = dispgate.evaluate_new_modules(ix, {"zz_pkg/__init__.py": src}, {}, {}, POLICY)
    assert any("нет сайдкара" in b for b in blocks), \
        "__init__.py с определением получил поблажку за имя файла"


def test_init_with_nested_definition_is_judged():
    # Ревью 2026-07-27 F-04: _defines_anything смотрел только tree.body, а норма говорит
    # «без определений», не «без определений на первом уровне».
    for src in ("if True:\n    def live_entry():\n        return 1\n",
                "if True:\n    class X:\n        pass\n",
                "try:\n    def f():\n        return 1\nexcept Exception:\n    pass\n",
                "for _ in [1]:\n    async def g():\n        return 1\n",
                "def outer():\n    def inner():\n        return 1\n"):
        blocks, _ = dispgate.evaluate_new_modules(ix, {"zz_pkg/__init__.py": src}, {}, {}, POLICY)
        assert blocks, f"определение под control-flow освободило __init__.py:\n{src}"


def test_init_pure_reexport_still_passes():
    # Негат-контроль: ужесточение не должно красить законный ре-экспортный шов.
    for src in ("from zz_pkg.core import do_thing  # noqa: F401\n",
                "import os\n__all__ = ['do_thing']\nVERSION = 1\n"):
        blocks, _ = dispgate.evaluate_new_modules(ix, {"zz_pkg/__init__.py": src}, {}, {}, POLICY)
        assert blocks == [], f"чистый ре-экспорт заблокирован:\n{src}"


def test_init_with_class_only_is_judged_too():
    src = "class Thing:\n    def m(self):\n        return 1\n"
    blocks, _ = dispgate.evaluate_new_modules(ix, {"zz_pkg/__init__.py": src}, {}, {}, POLICY)
    assert blocks, "__init__.py с классом прошёл незамеченным"


def test_leading_underscore_module_is_judged():
    blocks, _ = dispgate.evaluate_new_modules(ix, {"_helper.py": CLEAN_SRC}, {}, {}, POLICY)
    assert any("нет сайдкара" in b for b in blocks), "ведущее подчёркивание освободило от суждения"


# ── K1: referential integrity depends_on (DG-04) ─────────────────────────────

def _with_depends(dep):
    sc = _sidecar()
    sc["zz_new"]["depends_on"] = dep
    return sc


def test_depends_on_pointing_at_missing_contract_blocks():
    blocks, _ = _run(sidecars=_with_depends([{"module": "project_context/nowhere", "uses": [], "why": "x"}]))
    assert any("не существует" in b for b in blocks), f"ссылка в пустоту прошла: {blocks}"


def test_depends_on_legacy_marker_with_reason_passes():
    blocks, _ = _run(sidecars=_with_depends(
        [{"module": "health_db", "uses": ["get_conn"], "why": "БД",
          "legacy": "контракт появится в аудите владельца, отдельная нить"}]))
    assert blocks == [], f"честная пометка легаси заблокировала работу: {blocks}"


def test_depends_on_entry_without_module_blocks():
    blocks, _ = _run(sidecars=_with_depends([{"uses": ["f"], "why": "y"}]))
    assert any("module обязано быть непустой строкой" in b for b in blocks)


def test_depends_on_module_of_wrong_type_blocks_and_does_not_raise():
    # Ревью 2026-07-27 F-05: `{"module": 7}` проходил проверку на непустоту и падал на .strip();
    # исключение уходило в общий CLI-except и становилось РАЗРЕШЕНИЕМ коммита.
    for bad in (7, None, [], {}, True, ""):
        blocks, _ = _run(sidecars=_with_depends([{"module": bad, "uses": [], "why": "x"}]))
        assert any("module обязано быть непустой строкой" in b for b in blocks), f"тип {bad!r} прошёл"


def test_depends_on_boolean_legacy_is_not_a_reason():
    # Документ требует непустую строку-ПРИЧИНУ; `legacy: true` работал как безусловный люк.
    for bad in (True, 1, [], {}, "", "   "):
        blocks, _ = _run(sidecars=_with_depends(
            [{"module": "nowhere/missing", "uses": [], "why": "x", "legacy": bad}]))
        assert blocks, f"legacy={bad!r} принято за причину"


def test_depends_on_legacy_string_still_passes():
    # Негат-контроль к ужесточению: честная пометка обязана продолжать работать.
    blocks, _ = _run(sidecars=_with_depends(
        [{"module": "nowhere/missing", "uses": [], "why": "x", "legacy": "контракт в аудите владельца"}]))
    assert blocks == [], f"честная пометка легаси заблокирована: {blocks}"


def test_missing_depends_on_field_blocks():
    sc = _sidecar()
    sc["zz_new"].pop("depends_on")
    blocks, _ = _run(sidecars=sc)
    assert any("нет поля depends_on" in b for b in blocks)


# ── K1: сайдкар, совпадение поверхности, полнота вердикта ─────────────────────

def test_k1_blocks_missing_sidecar():
    blocks, _ = _run(sidecars={})
    assert any("нет сайдкара" in b for b in blocks)


def test_k1_blocks_undeclared_public():
    blocks, _ = _run(sidecars=_sidecar(public=()))
    assert any("публичные вне контракта" in b and "do_thing" in b for b in blocks)


def test_k1_blocks_phantom_contract():
    blocks, _ = _run(sidecars=_sidecar(public=("do_thing", "never_existed")))
    assert any("обещает несуществующее" in b and "never_existed" in b for b in blocks)


def test_k1_blocks_verdict_without_rationale():
    blocks, _ = _run(sidecars=_sidecar(drop=("rationale",)))
    assert any("нет поля rationale" in b for b in blocks)


def test_k1_blocks_verdict_with_unanswered_items():
    blocks, _ = _run(sidecars=_sidecar(items=False))
    assert any("пункты без ответа" in b and "D1" in b and "D6" in b for b in blocks)


def test_k1_blocks_unknown_verdict_value():
    blocks, _ = _run(sidecars=_sidecar(verdict="maybe"))
    assert any("verdict вне" in b for b in blocks)


def test_k1_negative_verdict_passes_but_needs_cause():
    # Блокирует ОТСУТСТВИЕ суждения, не его знак: осознанное «не одноразовый» проходит…
    blocks, _ = _run(sidecars=_sidecar(verdict="not_disposable", cause="shared_table_dumping_ground"))
    assert blocks == []
    # …но без стабильного ключа причины её нельзя посчитать по неделям → блок
    blocks2, _ = _run(sidecars=_sidecar(verdict="not_disposable"))
    assert any("without cause" in b or "без cause" in b for b in blocks2)


# ── K2: тест, который импортирует модуль и зовёт публичное имя ────────────────

def test_k2_blocks_when_no_test_at_all():
    blocks, _ = _run(tests={})
    assert any("нет теста" in b for b in blocks)


def test_k2_blocks_when_test_imports_but_never_calls():
    tests = {"tests/unit/test_zz_new.py": "import zz_new\ndef test_it():\n    assert zz_new\n"}
    blocks, _ = _run(tests=tests)
    assert any("нет теста" in b for b in blocks)


def test_k2_recognises_package_import_form():
    # Догфуд-находка 2026-07-26: подстрочный «import <стем>» промахивался на доминирующей в репо
    # форме `from project_context import indexer, dispgate` → тест признавался отсутствующим, и
    # гейт заблокировал бы СВОЙ ЖЕ корректный модуль. Детект переведён на AST; это его сторож.
    sc = {"project_context/zz_pkg": {"public": ["do_thing"], "depends_on": [],
                                     "disposability": _sidecar()["zz_new"]["disposability"],
                                     "path": "contracts/project_context/zz_pkg.json"}}
    tests = {"tests/unit/test_zz_pkg.py": "from project_context import indexer, zz_pkg\n"
                                          "def test_it():\n    assert zz_pkg.do_thing(1)\n"}
    blocks, _ = dispgate.evaluate_new_modules(ix, {"project_context/zz_pkg.py": CLEAN_SRC}, tests, sc, POLICY)
    assert blocks == [], f"форма импорта из пакета не распознана: {blocks}"


def test_k2_unparseable_test_gives_no_free_pass():
    # непарсящийся тест не должен молча зачитываться за характеризацию
    tests = {"tests/unit/test_zz_new.py": "import zz_new\ndef test_it(:\n    zz_new.do_thing(1)\n"}
    blocks, _ = _run(tests=tests)
    assert any("нет теста" in b for b in blocks)


def test_k2_call_in_comment_is_not_characterisation():
    # DG-02a: подстрока `do_thing(` встречалась в комментарии — гейт признавал тест.
    tests = {"tests/unit/test_zz_new.py":
             "import zz_new\ndef test_it():\n    assert zz_new\n    # zz_new.do_thing(1)\n"}
    blocks, _ = _run(tests=tests)
    assert any("нет теста" in b for b in blocks), "вызов в комментарии засчитан за характеризацию"


def test_k2_call_in_string_literal_is_not_characterisation():
    tests = {"tests/unit/test_zz_new.py":
             'import zz_new\nDOC = "zz_new.do_thing(1)"\ndef test_it():\n    assert zz_new\n'}
    blocks, _ = _run(tests=tests)
    assert any("нет теста" in b for b in blocks), "вызов в строке засчитан за характеризацию"


def test_k2_same_named_method_of_foreign_object_is_not_characterisation():
    # DG-02b: у чужого объекта метод с тем же именем — подстрока `.do_thing(` совпадала.
    tests = {"tests/unit/test_zz_new.py":
             "import zz_new\nclass O:\n    def do_thing(self, x):\n        return x\n"
             "other = O()\ndef test_it():\n    other.do_thing(1)\n"}
    blocks, _ = _run(tests=tests)
    assert any("нет теста" in b for b in blocks), "чужой одноимённый метод засчитан"


def test_k2_accepts_alias_import_form():
    # Негат-контроль к строгости связывания: законную форму гейт не должен красить.
    tests = {"tests/unit/test_zz_new.py":
             "import zz_new as z\ndef test_it():\n    assert z.do_thing(1) == 2\n"}
    blocks, _ = _run(tests=tests)
    assert blocks == [], f"алиасная форма импорта не распознана: {blocks}"


def test_k2_accepts_direct_function_import():
    tests = {"tests/unit/test_zz_new.py":
             "from zz_new import do_thing\ndef test_it():\n    assert do_thing(1) == 2\n"}
    blocks, _ = _run(tests=tests)
    assert blocks == [], f"прямой импорт функции не распознан: {blocks}"


def test_k2_blocks_when_name_called_but_module_not_imported():
    # «do_thing(» встречается в чужом тесте — это не характеризация нашего модуля
    tests = {"tests/unit/test_other.py": "def test_x():\n    assert do_thing(1)\n"}
    blocks, _ = _run(tests=tests)
    assert any("нет теста" in b for b in blocks)


# ── K3: чужие приватные, две формы; сиблинги пакета — не чужие ────────────────

def test_k3_blocks_private_import_from_foreign_module():
    src = "from health_db import _validate_db_path\ndef do_thing(x):\n    return _validate_db_path(x)\n"
    blocks, _ = _run(src=src)
    assert any("чужие приватные" in b and "_validate_db_path" in b for b in blocks)


def test_k3_blocks_private_attribute_on_foreign_module():
    src = "import health_db as db\ndef do_thing(x):\n    return db._PRIMARY_HOST\n"
    blocks, _ = _run(src=src)
    assert any("чужие приватные" in b and "_PRIMARY_HOST" in b for b in blocks)


def test_k3_sibling_private_inside_package_is_not_foreign():
    # пакет = одна одноразовая единица; иначе гейт красил бы project_context/__main__.py,
    # который законно зовёт indexer._manifest. Ложняк ловим ДО его появления.
    src = ("from project_context import indexer\n"
           "def do_thing(x):\n    return indexer._manifest(x)\n")
    sc = {"project_context/zz_sib": {"public": ["do_thing"], "depends_on": [],
                                     "disposability": _sidecar()["zz_new"]["disposability"],
                                     "path": "contracts/project_context/zz_sib.json"}}
    tests = {"tests/unit/test_zz_sib.py": "from project_context import zz_sib\ndef test_it():\n"
                                          "    assert zz_sib.do_thing({})\n"}
    blocks, _ = dispgate.evaluate_new_modules(ix, {"project_context/zz_sib.py": src}, tests, sc, POLICY)
    assert blocks == [], f"сиблинг пакета принят за чужого: {blocks}"


def test_k3_dunder_is_not_private_access():
    src = "import health_db as db\ndef do_thing(x):\n    return db.__name__\n"
    blocks, _ = _run(src=src)
    assert not any("чужие приватные" in b for b in blocks)


# ── K4: только предупреждения, никогда блок ──────────────────────────────────

def test_k4_size_warns_never_blocks():
    lim = POLICY["limits"]
    wide = "".join(f"def f{i}(x):\n    return x\n" for i in range(lim["public_surface_warn"] + 2))
    sc = _sidecar(public=[f"f{i}" for i in range(lim["public_surface_warn"] + 2)])
    tests = {"tests/unit/test_zz_new.py": "import zz_new\ndef test_it():\n    assert zz_new.f0(1)\n"}
    blocks, warns = _run(src=wide, sidecars=sc, tests=tests)
    assert blocks == [], f"размер не должен блокировать: {blocks}"
    assert any("кандидат на разбиение" in w for w in warns)

    tall = CLEAN_SRC + "\n" * (lim["module_lines_warn"] + 5)
    blocks2, warns2 = _run(src=tall)
    assert blocks2 == [] and any("влезть в рабочий контекст" in w for w in warns2)


# ── деградация методологии и непарсящийся файл ────────────────────────────────

def test_degraded_policy_blocks_not_warns():
    # Позит-контроль был ложно-слабым: он требовал лишь строку в warns, и проба DG-15 показала
    # цену — с пустой методологией пункты D1..D6 перестают требоваться, а сайдкар без единого
    # ответа проходит. Отказ ИНСТРУМЕНТА суждения обязан быть fail-closed (§13 ступень 2).
    import tempfile
    bad = indexer.disposability_policy(_REPO, engine_dir=tempfile.mkdtemp(prefix="dg_broken_"))
    blocks, warns = _run(policy=bad)
    assert any("НЕ прочитан" in b for b in blocks), f"деградация не блокирует: {blocks}"
    assert not any("НЕ прочитан" in w for w in warns), "деградация всё ещё уходит в warns"


def test_degraded_is_typed_not_merely_truthy():
    # Ревью 2026-07-27 F-02: валидный JSON с items/limits неверного ТИПА проходил проверку
    # «есть и непусто», а падал позже — внутри evaluator, где общий CLI-except делал отказ
    # инструмента разрешением коммита. Схема живёт в единственном ридере.
    import json as _j, tempfile as _t, os as _o
    good = indexer.disposability_policy(_REPO)
    for label, patch in (
        ("items строкой", {"items": "wrong"}),
        ("items скаляром", {"items": 1}),
        ("items объектом", {"items": {"D1": "x"}}),
        ("items без id", {"items": [{"text": "x"}]}),
        ("items с пустым id", {"items": [{"id": "  ", "text": "x"}]}),
        ("limits строкой", {"limits": "wrong"}),
        ("limits списком", {"limits": [1, 2]}),
    ):
        d = _t.mkdtemp(prefix="dg_schema_")
        blob = {"criterion": "c", "review_question": "q", "machine_scope": "m",
                "items": good["items"], "limits": good["limits"], **patch}
        with open(_o.path.join(d, "disposability.json"), "w", encoding="utf-8") as fh:
            _j.dump(blob, fh, ensure_ascii=False)
        pol = indexer.disposability_policy(_REPO, engine_dir=d)
        assert pol["degraded"], f"{label}: чек-лист негоден, но degraded не поднят"
        blocks, _ = _run(policy=pol)
        assert any("НЕ прочитан" in b for b in blocks), f"{label}: не блокирует"


def test_degraded_policy_does_not_silently_drop_items_requirement():
    # Собственно предмет DG-15: с пустой методологией сайдкар БЕЗ ответов по пунктам обязан
    # оставаться заблокированным, а не проходить «потому что требовать стало нечего».
    import tempfile
    bad = indexer.disposability_policy(_REPO, engine_dir=tempfile.mkdtemp(prefix="dg_broken_"))
    blocks, _ = _run(sidecars=_sidecar(items=False), policy=bad)
    assert blocks, "сайдкар без ответов по D1..D6 прошёл при пустой методологии"


def test_unparseable_new_module_blocks():
    blocks, _ = _run(src="def broken(:\n")
    assert any("не парсится" in b for b in blocks)


def test_gate_is_wired_into_canonical_hook():
    # §14: распаянный гейт = ложная безопасность (полагаешься на защиту, которой нет).
    # Хук ТРЕКАЕТСЯ (scripts/git-hooks/pre-commit, симлинк из .git/hooks), поэтому удаление
    # вызова видно и в pytest, и в ночном integrity (check_dispgate_liveness) — два независимых
    # читателя одного инварианта, потому что pytest на MacBook, а integrity на Studio.
    hook = os.path.join(_REPO, "scripts", "git-hooks", "pre-commit")
    src = open(hook, encoding="utf-8").read()
    assert "project_context dispgate --block" in src, "гейт распаян из pre-commit"
    # Первая редакция ассертила «exit 5 in src» — ПУСТО-ЗЕЛЁНЫЙ сторож: подстрока и так есть в
    # файле (5 занят forward-ref-стражем integrity_tests), тест прошёл бы и без моего блока.
    # Проверяем СВЯЗКУ: проброс кода внутри именно блока dispgate, а не наличие строки где-то.
    blk = src.split("dispgate --block")[1].split("exit 0")[0]
    assert "_disp_rc -eq 6" in blk and "exit 6" in blk, "блок-код 6 не проброшен из блока dispgate"
    assert "-eq 5" not in blk, "коллизия кодов: 5 занят forward-ref-стражем integrity_tests"


if __name__ == "__main__":
    import traceback, sys
    f = 0
    for n, fn in sorted(globals().items()):
        if n.startswith("test_") and callable(fn):
            try:
                fn(); print("OK  ", n)
            except Exception:
                print("FAIL", n); traceback.print_exc(); f += 1
    print("ИТОГ:", "ВСЕ ПРОШЛИ" if not f else f"{f} провал")
    sys.exit(1 if f else 0)
