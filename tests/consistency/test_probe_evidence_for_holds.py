"""tests/consistency/test_probe_evidence_for_holds.py — сторож R-7.

ЧТО СТЕРЕЖЁТ. Статус `holds` в реестре замысла, опирающийся на пробу, обязан
опираться на пробу С ИСПОЛНЕННЫМ негативным контролем. Иначе «доказано» держится
на честном слове автора — ровно то, ради чего заведён режим `kind: "probe"` (§15,
решение владельца Р-6 ред. 3).

ЗАЧЕМ ОТДЕЛЬНЫЙ СТОРОЖ, если гейт уже это проверяет. Не проверяет. `dispgate`
судит файлы со статусом `A` — то есть НОВЫЕ. Обе живые пробы закоммичены до того,
как режим появился (2026-07-29), и под правило не попадают никогда. Механизм,
введённый для будущего и молча не применённый к прошлому, — это класс отказа
R-7 плана нити `validation-gate-repair`; статусы реестра стоят именно на прошлом.

ГРАНИЦА (не потерять). Сторож проверяет ПОЛНОТУ квитанции негативного контроля,
а не факт прогона: квитанцию можно написать руками. Та же честная слабость, что
у `legacy`-пометки в K1 и у «K2 доказывает синтаксис, не исполнение» — названа в
§15 вслух, а не спрятана. Проверку полноты выполняет тот же
`dispgate._probe_evidence_problems`, которым судится новый файл: две реализации
одного правила разъехались бы молча.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import intent_registry as ir
from project_context import dispgate

pytestmark = pytest.mark.consistency

ROOT = Path(__file__).resolve().parents[2]
_PROBE_REF = re.compile(r"(plans/probe_[A-Za-z0-9_\-]+\.py)")


def _blob(inv: dict) -> str:
    """Весь текст инварианта: claim, limits и всё прочее. Ссылка на пробу может
    жить в любом поле — искать её только в claim значило бы зависеть от того,
    где автор её упомянул."""
    return json.dumps(inv, ensure_ascii=False)


def probes_backing_holds() -> list[tuple[str, str]]:
    """[(inv_id, путь_пробы)] для инвариантов со статусом holds, называющих пробу."""
    out: list[tuple[str, str]] = []
    for entry in ir.load_registry():
        for inv in entry.get("invariants") or []:
            if inv.get("status") != "holds":
                continue
            for ref in dict.fromkeys(_PROBE_REF.findall(_blob(inv))):
                out.append((inv.get("id", "<без id>"), ref))
    return out


def sidecar_of(probe_rel: str) -> Path:
    """Канонический путь сайдкара — `contracts/<ключ-модуля>.json`, где ключ БЕЗ `.py`
    (та же форма, что у индекса и у `contract_sidecars`).

    До 2026-07-29 здесь склеивалось `<путь>.py.json`, и сайдкары трёх проб лежали под этим
    именем. Следствие обнаружилось, когда гейт одноразовости впервые стал судить `plans/`:
    ключ он считает по полю `module`, то есть без `.py`, — и сайдкаров проб просто не видел.
    Один файл, два способа адресации, и каждый механизм пользовался своим.

    Старая форма поддержана как запасная: сайдкар, не переехавший на новое имя, продолжает
    находиться, а не превращается в «доказательства нет»."""
    canonical = ROOT / "contracts" / (probe_rel.removesuffix(".py") + ".json")
    if canonical.exists():
        return canonical
    return ROOT / "contracts" / (probe_rel + ".json")


@pytest.mark.owner_data
@pytest.mark.parametrize("inv_id,probe", probes_backing_holds() or [("<нет>", "")])
def test_holds_probe_carries_executed_negative_control(inv_id: str, probe: str) -> None:
    if not probe:
        pytest.skip("в реестре нет holds-инвариантов, опирающихся на пробу")

    assert (ROOT / probe).exists(), (
        f"{inv_id}: статус holds ссылается на пробу {probe}, которой нет на диске — "
        f"доказательство указывает в пустоту")

    sc_path = sidecar_of(probe)
    assert sc_path.exists(), (
        f"{inv_id}: у пробы {probe} нет сайдкара {sc_path.relative_to(ROOT)} — "
        f"статус holds стоит на пробе без контракта")

    sc = json.loads(sc_path.read_text(encoding="utf-8"))
    assert sc.get("kind") == "probe", (
        f"{inv_id}: сайдкар {probe} не объявляет kind='probe', значит негативный контроль "
        f"с него не спрашивается ни здесь, ни гейтом. Статус holds — без носителя")

    problems = dispgate._probe_evidence_problems(sc)
    assert not problems, (
        f"{inv_id}: квитанция негативного контроля пробы {probe} неполна:\n  - "
        + "\n  - ".join(problems))


def test_registry_actually_has_such_pairs() -> None:
    """Позитивный контроль сторожа. Без него весь файл — vacuous pass: сломается
    регулярка или load_registry вернёт пусто, параметризация схлопнется в skip, и
    зелёное будет означать «ничего не проверено» (класс BL-017)."""
    pairs = probes_backing_holds()
    assert pairs, (
        "сторож не нашёл НИ ОДНОГО holds-инварианта со ссылкой на пробу. Либо реестр "
        "изменился, либо сломан поиск ссылок — в обоих случаях этот файл перестал "
        "стеречь что-либо и зелёный цвет обманывает")


def test_detector_rejects_incomplete_receipt() -> None:
    """Второй позитивный контроль: детектор обязан РАЗЛИЧАТЬ. Проверка полноты,
    которая пропускает пустую квитанцию, — не проверка."""
    assert dispgate._probe_evidence_problems({"kind": "probe"}), \
        "детектор пропустил kind=probe без блока negative_control"
    assert dispgate._probe_evidence_problems(
        {"kind": "probe", "negative_control": {"harness": "h", "ran_at": "2026-07-29",
                                               "mutations": [{"tag": "t", "statement": "s",
                                                              "verdict": "НЕ покраснел"}]}}), \
        "детектор пропустил мутацию с вердиктом «НЕ покраснел» — это находка, а не квитанция"
