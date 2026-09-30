"""Контроли ВТОРОГО режима строгости §15 — `kind: "probe"` (решение владельца Р-6, 2026-07-29).

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ, а не секция в test_dispgate.py: там негат-контроль устроен вокруг
«соответствующего МОДУЛЯ», и добавление второго режима туда заставило бы каждую фикстуру
знать про оба. Здесь свой негат-контроль — соответствующая ПРОБА проходит молча.

ЧТО ДОКАЗЫВАЕТСЯ. Что режим включается объявлением, что он снимает требование теста и что
взамен он требует полную и непротиворечивую квитанцию негативного контроля — раздельно по
каждому пункту. Позит-контроль на каждый пункт отдельно: гейт, зелёный на всём сразу,
неотличим от гейта, который смотрит на один пункт из шести.

ЧЕГО НЕ ДОКАЗЫВАЕТСЯ. Что негативный контроль ДЕЙСТВИТЕЛЬНО исполнялся: квитанцию можно
написать руками. Названо вслух в §15 и в докстринге `_probe_evidence_problems`.
"""
import os

from project_context import dispgate, indexer

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ix = indexer.build(_REPO)
POLICY = indexer.disposability_policy(_REPO)

PROBE_SRC = '''"""Проба: доказывает свойство живой системы, а не поведение функции."""
def main():
    return 0
'''


def _nc(**over):
    nc = {"harness": "plans/verify_zz_probe.py", "ran_at": "2026-07-29",
          "mutations": [
              {"tag": "M-01", "statement": "убрали запрет — оракул обязан покраснеть",
               "verdict": "ПОКРАСНЕЛ"},
              {"tag": "M-02", "statement": "подменили источник — оракул обязан покраснеть",
               "verdict": "ПОКРАСНЕЛ"},
          ]}
    nc.update(over)
    return nc


def _sidecar(kind="probe", nc=None, drop_nc=False):
    sc = {"public": ["main"], "depends_on": [], "path": "contracts/zz_probe.json",
          "disposability": {"verdict": "not_disposable", "cause": "review_harness_bound_to_internals",
                            "rationale": "оснастка разбора привязана к внутренностям подсистемы",
                            "date": "2026-07-29", "oracle": "agent",
                            "items": {i["id"]: "ок" for i in POLICY["items"]}}}
    if kind is not None:
        sc["kind"] = kind
    if not drop_nc:
        sc["negative_control"] = _nc() if nc is None else nc
    return {"zz_probe": sc}


def _run(sidecars=None, tests=None):
    """Тестов НЕТ намеренно: у пробы характеризация другая, и это предмет проверки."""
    return dispgate.evaluate_new_modules(ix, {"zz_probe.py": PROBE_SRC}, tests or {},
                                         _sidecar() if sidecars is None else sidecars, POLICY)


def _has(blocks, needle):
    return any(needle in b for b in blocks)


# ── негат-контроль: соответствующая проба проходит молча, БЕЗ теста ────────────

def test_negative_control_probe_with_receipt_passes_without_any_test():
    blocks, warns = _run()
    assert blocks == [], f"ложняк на соответствующей пробе: {blocks}"
    assert warns == []


def test_module_kind_still_demands_a_test():
    """Граница режима: снятие K2 касается ТОЛЬКО проб. Иначе это не режим, а дыра."""
    blocks, _ = _run(sidecars=_sidecar(kind="module", drop_nc=True))
    assert _has(blocks, "нет теста"), f"обычный модуль перестал требовать тест: {blocks}"


def test_absent_kind_defaults_to_module():
    """Умолчание — строгий режим. Иначе забытое поле молча ослабляло бы гейт."""
    blocks, _ = _run(sidecars=_sidecar(kind=None, drop_nc=True))
    assert _has(blocks, "нет теста")


def test_unknown_kind_blocks_and_is_judged_strictly():
    blocks, _ = _run(sidecars=_sidecar(kind="почти_проба", drop_nc=True))
    assert _has(blocks, "вне ['module', 'probe']"), blocks
    assert _has(blocks, "нет теста"), "неизвестный режим обязан судиться по строгому"


# ── позит-контроль: раздельно по каждому пункту квитанции ─────────────────────

def test_probe_without_negative_control_block_is_blocked():
    blocks, _ = _run(sidecars=_sidecar(drop_nc=True))
    assert _has(blocks, "нет блока negative_control"), blocks


def test_probe_with_negative_control_not_an_object_is_blocked():
    sc = _sidecar()
    sc["zz_probe"]["negative_control"] = "прогонял, всё красное"
    blocks, _ = _run(sidecars=sc)
    assert _has(blocks, "нет блока negative_control"), blocks


def test_empty_harness_is_blocked():
    blocks, _ = _run(sidecars=_sidecar(nc=_nc(harness="  ")))
    assert _has(blocks, "harness пуст"), blocks


def test_empty_ran_at_is_blocked():
    blocks, _ = _run(sidecars=_sidecar(nc=_nc(ran_at="")))
    assert _has(blocks, "ran_at пуст"), blocks


def test_unparseable_ran_at_is_blocked():
    """DG-05 научил: непроверяемая дата — это поле, которое можно заполнить чем угодно."""
    blocks, _ = _run(sidecars=_sidecar(nc=_nc(ran_at="вчера")))
    assert _has(blocks, "не разбирается как дата"), blocks


def test_no_mutations_at_all_is_blocked():
    blocks, _ = _run(sidecars=_sidecar(nc=_nc(mutations=[])))
    assert _has(blocks, "mutations пуст"), blocks


def test_mutation_without_tag_is_blocked():
    blocks, _ = _run(sidecars=_sidecar(nc=_nc(mutations=[
        {"statement": "что-то ломали", "verdict": "ПОКРАСНЕЛ"}])))
    assert _has(blocks, "нет тега"), blocks


def test_duplicate_tags_are_blocked():
    blocks, _ = _run(sidecars=_sidecar(nc=_nc(mutations=[
        {"tag": "M-01", "statement": "первое", "verdict": "ПОКРАСНЕЛ"},
        {"tag": "M-01", "statement": "второе", "verdict": "ПОКРАСНЕЛ"}])))
    assert _has(blocks, "повторяется"), blocks


def test_mutation_without_statement_is_blocked():
    blocks, _ = _run(sidecars=_sidecar(nc=_nc(mutations=[
        {"tag": "M-01", "verdict": "ПОКРАСНЕЛ"}])))
    assert _has(blocks, "нет утверждения"), blocks


def test_surviving_mutation_is_blocked_and_named_a_finding():
    """Главный пункт. Мутация, прошедшая незамеченной, — не деталь квитанции, а находка:
    объявленное звено не стережётся, и «доказано» про него неверно."""
    blocks, _ = _run(sidecars=_sidecar(nc=_nc(mutations=[
        {"tag": "M-01", "statement": "убрали запрет", "verdict": "ПОКРАСНЕЛ"},
        {"tag": "M-02", "statement": "подменили источник", "verdict": "НЕ покраснел"}])))
    assert _has(blocks, "прошла незамеченной"), blocks
    assert _has(blocks, "НАХОДКА"), blocks


def test_verdict_outside_the_closed_pair_is_blocked():
    blocks, _ = _run(sidecars=_sidecar(nc=_nc(mutations=[
        {"tag": "M-01", "statement": "убрали запрет", "verdict": "вроде сработало"}])))
    assert _has(blocks, "вне"), blocks


def test_probe_mode_does_not_weaken_k1():
    """Режим меняет ТОЛЬКО характеризацию. Контракт и вердикт с пробы спрашиваются так же."""
    sc = _sidecar()
    sc["zz_probe"]["disposability"].pop("rationale")
    sc["zz_probe"]["public"] = []
    blocks, _ = _run(sidecars=sc)
    assert _has(blocks, "нет поля rationale"), blocks
    assert _has(blocks, "публичные вне контракта"), blocks


# ── ШОВ «ридер → гейт» ────────────────────────────────────────────────────────────────
# Всё выше передаёт словарь сайдкаров в гейт РУКАМИ. Это проверяет логику гейта и НЕ
# проверяет, доедет ли объявление из файла до этой логики. 2026-07-29 выяснилось, что не
# доезжало: `indexer.contract_sidecars` — единственный ридер формата — не переносил `kind`
# и `negative_control`, поэтому в бою kind всегда был None, проба судилась как модуль, а
# ветка kind=="probe" и вся `_probe_evidence_problems` были мёртвым кодом. Тринадцать
# зелёных тестов режима при неработающем режиме — ровно тот класс, против которого §15
# и построен: правило объявлено и не подключено.

def test_reader_carries_kind_and_receipt_to_the_gate(tmp_path):
    """⭐ Поле, объявленное в ФАЙЛЕ, обязано дойти до гейта. Уберите `kind` из
    contract_sidecars — этот тест краснеет, а режим пробы снова станет недостижим."""
    import json

    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts" / "zz_probe.json").write_text(json.dumps({
        "module": "zz_probe", "kind": "probe", "public": ["main"],
        "negative_control": _nc(),
        "disposability": {"verdict": "not_disposable", "cause": "x", "rationale": "y",
                          "date": "2026-07-29", "oracle": "agent"},
    }, ensure_ascii=False), encoding="utf-8")

    sidecars, bad = indexer.contract_sidecars(str(tmp_path))
    assert not bad, bad
    sc = sidecars["zz_probe"]
    assert sc.get("kind") == "probe", "ридер потерял kind — режим пробы недостижим"
    assert isinstance(sc.get("negative_control"), dict), \
        "ридер потерял negative_control — гейт не сможет проверить квитанцию"


def test_probe_from_a_real_file_needs_no_characterising_test(tmp_path):
    """Сквозной позитивный контроль: сайдкар прочитан ИЗ ФАЙЛА, тестов нет — и гейт молчит.
    Именно этот путь был сломан, пока `kind` терялся по дороге."""
    import json

    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts" / "zz_probe.json").write_text(json.dumps({
        "module": "zz_probe", "kind": "probe", "public": ["main"], "depends_on": [],
        "negative_control": _nc(),
        "disposability": {"verdict": "not_disposable",
                          "cause": "review_harness_bound_to_internals",
                          "rationale": "оснастка разбора привязана к внутренностям подсистемы",
                          "date": "2026-07-29", "oracle": "agent",
                          "items": {i["id"]: "ок" for i in POLICY["items"]}},
    }, ensure_ascii=False), encoding="utf-8")

    sidecars, _ = indexer.contract_sidecars(str(tmp_path))
    blocks, _warns = dispgate.evaluate_new_modules(
        ix, {"zz_probe.py": PROBE_SRC}, {}, sidecars, POLICY)
    assert not _has(blocks, "нет теста"), \
        f"проба, объявленная в файле, всё ещё требует характеризационный тест: {blocks}"
