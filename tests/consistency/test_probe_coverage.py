"""ОРАКУЛ НА ОХВАТ, а не на логику (Ш-A, 2026-07-29).

ЗАЧЕМ ЭТОТ ФАЙЛ. За двое суток семь инструментов нити оказались верными по логике и
слепыми по охвату: делали ровно написанное, но на множестве меньше заявленного, иногда
на пустом. Ни один из семи не был ошибкой в вычислении — все семь были ошибками в
ОБЛАСТИ ОПРЕДЕЛЕНИЯ. Самый показательный: сторож, требующий у probe-backed `holds` дату
и место подтверждения, честно зеленел, потому что из трёх инвариантов, поднятых пробами,
признак `probe_backed` находил ОДИН. Ссылка на пробу у двух других была записана
YAML-комментарием, а комментарий не попадает в данные.

Сторож, сообщающий «нарушений 0», неотличим от сторожа, осмотревшего 0 кандидатов. Этот
файл — первая в проекте проверка, которая сравнивает ЧЕЛОВЕЧЕСКИЙ взгляд на реестр
(упоминание пути пробы в сыром тексте yaml, включая комментарии) с МАШИННЫМ (`probes_of`
по полю). Расхождение = слепое пятно, и оно краснеет здесь.

ЧЕГО ЭТОТ ФАЙЛ НЕ ДЕЛАЕТ. Он не судит, достаточна ли проба. Он судит, ВИДИТ ли механизм
то, что видит читатель. Это разные вопросы, и второй — предмет У-1, где оракул человек.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import intent_registry as ir

pytestmark = pytest.mark.consistency
ROOT = Path(__file__).resolve().parents[2]
YAML = ROOT / "subsystem_intent.yaml"
PROBE_RE = re.compile(r"plans/probe_[A-Za-z0-9_\-]+\.py")


def _mentions_by_invariant() -> dict:
    """Ссылки на пробы, найденные в СЫРОМ тексте реестра, привязанные к инварианту.

    Читаем текст, а не разобранный yaml, СПЕЦИАЛЬНО: комментарии из данных выпадают, и
    именно в них жило слепое пятно. Разбор простой: блок инварианта начинается со `- id:`
    и длится до следующего `- id:` или до конца записи подсистемы.
    """
    out: dict = {}
    current = None
    for line in YAML.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*- id:\s*(\S+)", line)
        if m:
            current = m.group(1).strip().strip('"').strip("'")
            out.setdefault(current, set())
            continue
        if current is None:
            continue
        for hit in PROBE_RE.findall(line):
            out[current].add(hit)
    return out


def _registry_invariants() -> dict:
    out = {}
    for entry in ir.load_registry():
        for inv in entry.get("invariants") or []:
            out[inv["id"]] = inv
    return out


def _evidence_probes() -> set:
    """Пробы НА ДИСКЕ, несущие доказательство: сайдкар с kind=probe и ИСПОЛНЕННЫМ
    негативным контролем. Проба без исполненного контроля доказательством не считается
    (§15), поэтому и в охвате не участвует."""
    found = set()
    for sc in (ROOT / "contracts" / "plans").glob("probe_*.json"):
        try:
            data = json.loads(sc.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if data.get("kind") != "probe":
            continue
        nc = data.get("negative_control") or {}
        if not str(nc.get("ran_at") or "").strip():
            continue
        # Каноническое имя сайдкара — БЕЗ `.py` (`contracts/plans/probe_x.json`), потому что
        # ключ модуля в индексе тоже без него. Модуль на диске — с `.py`. Обе формы старых
        # сайдкаров (`probe_x.py.json`) поддержаны: `.py` снимается, потом добавляется ровно
        # один раз, и результат одинаков для любого имени файла.
        found.add(f"plans/{sc.stem.removesuffix('.py')}.py")
    return found


# ── Собственно охват ──────────────────────────────────────────────────────────────────

def test_every_text_mention_is_visible_to_the_mechanism():
    """⭐ ТОТ САМЫЙ СЛУЧАЙ. Если путь пробы упомянут в тексте инварианта, механизм обязан
    видеть его ПОЛЕМ. Ссылка, живущая только в комментарии, — слепое пятно: читатель её
    видит и считает связь установленной, сторож её не видит и молчит."""
    mentions = _mentions_by_invariant()
    invs = _registry_invariants()
    blind = []
    for iid, paths in mentions.items():
        if not paths:
            continue
        declared = set(ir.probes_of(invs.get(iid, {})))
        missing = sorted(paths - declared)
        if missing:
            blind.append((iid, missing))
    assert not blind, (
        "текст реестра ссылается на пробы, которых механизм не видит полем `probe`:\n" +
        "\n".join(f"  {iid}: {paths}" for iid, paths in blind) +
        "\nПеренеси ссылку из комментария в поле `probe`, иначе сторожа судят выборку "
        "меньше заявленной")


def test_probe_backed_and_the_field_agree():
    """Два способа узнать «инвариант стоит на пробе» обязаны совпадать. Разъехались —
    значит один из них судит не то множество, и неизвестно который."""
    invs = _registry_invariants()
    by_field = {i for i, inv in invs.items() if ir.probes_of(inv)}
    by_text = {i for i, inv in invs.items() if ir.probe_backed(inv)}
    only_text = sorted(by_text - by_field)
    assert not only_text, (
        f"probe_backed видит {sorted(by_text)}, а поле объявлено у {sorted(by_field)}. "
        f"Расхождение: {only_text} — эти опираются на пробу лишь по текстовому совпадению, "
        f"то есть их видимость случайна")


def test_no_evidence_is_dangling():
    """Обратная сторона охвата: проба с ИСПОЛНЕННЫМ негативным контролем существует, а ни
    один инвариант на неё не ссылается. Доказательство, которое ничего не доказывает —
    работа, потерянная молча."""
    claimed = set()
    for inv in _registry_invariants().values():
        claimed.update(ir.probes_of(inv))
    dangling = sorted(_evidence_probes() - claimed)
    assert not dangling, (
        f"пробы с исполненным негативным контролем, на которые не ссылается ни один "
        f"инвариант: {dangling}. Либо сослаться, либо объяснить, зачем доказательство "
        f"без утверждения")


@pytest.mark.owner_data
def test_declared_probe_files_exist():
    """Ссылка на несуществующий файл — обещание доказательства, которого нет. Такое поле
    хуже отсутствия: оно делает инвариант probe-backed и включает сторожей на пустом."""
    missing = []
    for iid, inv in _registry_invariants().items():
        for p in ir.probes_of(inv):
            if not (ROOT / p).exists():
                missing.append((iid, p))
    assert not missing, f"поле `probe` указывает на отсутствующие файлы: {missing}"


# ── ПОЗИТИВНЫЙ КОНТРОЛЬ САМОГО ОХВАТА ─────────────────────────────────────────────────
# Без него этот файл — восьмой экземпляр того же класса: проверки, чья выборка может
# оказаться пустой, о чём никто не узнает.

@pytest.mark.owner_data
def test_the_population_is_not_empty():
    """Все проверки выше — про расхождение множеств. На ПУСТЫХ множествах они зелены
    тривиально. Здесь мы утверждаем, что осматривать есть что, и называем числа."""
    mentions = {k: v for k, v in _mentions_by_invariant().items() if v}
    invs = _registry_invariants()
    by_field = {i for i, inv in invs.items() if ir.probes_of(inv)}
    evidence = _evidence_probes()

    assert len(invs) > 50, f"реестр прочитан не целиком: инвариантов {len(invs)}"
    assert mentions, "в тексте реестра нет НИ ОДНОГО упоминания пробы — разбор сломан"
    assert len(by_field) >= 3, (
        f"инвариантов с полем `probe`: {len(by_field)} — ожидалось не меньше трёх "
        f"(три пробы с исполненным негативным контролем на 2026-07-29). Меньше значит, "
        f"что охват сузился и проверки выше судят почти пустое множество")
    assert len(evidence) >= 3, (
        f"проб с исполненным негативным контролем: {len(evidence)} — ожидалось не меньше "
        f"трёх. Уменьшение — либо потеря сайдкара, либо потеря квитанции")


def test_parser_sees_comments_not_only_data():
    """Разбор сырого текста обязан находить ссылку В КОММЕНТАРИИ — иначе главная проверка
    этого файла слепа ровно там, где было слепое пятно. Проверяем на живом реестре:
    хотя бы одна ссылка в комментарии существует (их три на 2026-07-29), и парсер её видит."""
    raw = YAML.read_text(encoding="utf-8").splitlines()
    in_comment = [ln for ln in raw
                  if PROBE_RE.search(ln) and ln.lstrip().startswith("#")]
    assert in_comment, ("в реестре нет ссылок на пробы в комментариях — тогда этот тест "
                        "потерял предмет: перечитай, не переехали ли все ссылки в поля")
    mentions = _mentions_by_invariant()
    assert any(paths for paths in mentions.values()), \
        "парсер не нашёл ни одной ссылки, хотя в тексте они есть — разбор блоков сломан"
