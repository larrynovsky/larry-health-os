"""Два требования — два периметра (§15, решение владельца 2026-07-29, ред. 2).

K1 (контракт и вердикт) спрашивается с ЛЮБОГО нового Python-исходника. K2 (характеризационный
тест) — только с того, что система исполняет в штатной работе. До этой правки оба требования
делили один периметр, и файл в `plans/` или `migrations/` уходил из-под суда ЦЕЛИКОМ: вместе
с тестом с него переставали спрашивать и объяснение самого себя.

Контроли инжектят таблицу периметра явно — иначе они судили бы по файлу в пакете и молча
меняли смысл при его правке.
"""
import os

import pytest

from project_context import dispgate, indexer

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ix = indexer.build(_REPO)
POLICY = indexer.disposability_policy(_REPO)

SRC = '"""Что-то."""\ndef do_thing(x):\n    return x + 1\n'

PERIM = {
    "classes": {
        "not_source": {"k1": False, "k2": False},
        "not_python_module": {"k1": False, "k2": False},
        "not_executed_in_operation": {"k1": True, "k2": False},
        "not_rewritable": {"k1": True, "k2": False},
        "judgement_harness": {"k1": False, "k2": False},
    },
    "dirs": {"plans": "not_executed_in_operation", "migrations": "not_rewritable",
             "tests": "judgement_harness", "logs": "not_source"},
    "canon": {"modules": ["health_db"], "callables": ["get_conn"],
              "overrides_exemption_for": ["not_executed_in_operation", "not_rewritable"],
              "never_overrides": ["judgement_harness", "not_source", "not_python_module"]},
}

CANON_SRC = 'import health_db\ndef do_thing(x):\n    return x + 1\n'
CANON_SRC_FROM = 'from health_db import get_conn\ndef do_thing(x):\n    return x + 1\n'
CANON_SRC_CALL = 'from db import get_conn\ndef do_thing(x):\n    get_conn()\n    return x\n'


def _sc(key):
    return {key: {"public": ["do_thing"], "depends_on": [], "path": f"contracts/{key}.json",
                  "disposability": {"verdict": "disposable", "rationale": "одна функция",
                                    "date": "2026-07-29", "oracle": "agent",
                                    "items": {i["id"]: "ок" for i in POLICY["items"]}}}}


def _run(path, sidecars=None, tests=None, perim=PERIM, src=SRC):
    return dispgate.evaluate_new_modules(ix, {path: src}, tests or {},
                                         sidecars if sidecars is not None else {},
                                         POLICY, perimeter=perim)


def _has(blocks, needle):
    return any(needle in b for b in blocks)


# ── K1 спрашивается там, где раньше не спрашивалось ничего ────────────────────

@pytest.mark.parametrize("path", ["plans/zz_probe.py", "migrations/zz_mig.py"])
def test_contract_is_demanded_from_workshop_and_migrations(path):
    """Главный пункт правки: файл без сайдкара здесь БОЛЬШЕ не проходит молча."""
    blocks, _ = _run(path)
    assert _has(blocks, "нет сайдкара"), f"{path} прошёл без контракта: {blocks}"


@pytest.mark.parametrize("path", ["plans/zz_probe.py", "migrations/zz_mig.py"])
def test_but_characterising_test_is_NOT_demanded_there(path):
    """Вторая половина: с сайдкаром — молчание. Тест-заглушку требовать не за что."""
    blocks, _ = _run(path, sidecars=_sc(path[:-3]))
    assert blocks == [], f"с {path} спросили тест, которого он не должен: {blocks}"


def test_verdict_incompleteness_is_caught_in_the_workshop_too():
    """K1 спрашивается ПОЛНОСТЬЮ, а не «лишь бы файл был»."""
    sc = _sc("plans/zz_probe")
    sc["plans/zz_probe"]["disposability"].pop("oracle")
    blocks, _ = _run("plans/zz_probe.py", sidecars=sc)
    assert _has(blocks, "нет поля oracle"), blocks


# ── K2 спрашивается там, где система исполняет ────────────────────────────────

def test_production_code_still_needs_both():
    blocks, _ = _run("zz_prod.py", sidecars=_sc("zz_prod"))
    assert _has(blocks, "нет теста"), blocks


def test_unknown_path_is_judged_strictly():
    """Незнание — не освобождение: путь вне таблицы судится по полной."""
    blocks, _ = _run("some/new/place/zz_x.py")
    assert _has(blocks, "нет сайдкара") and _has(blocks, "нет теста"), blocks


# ── классы, с которых не спрашивается ничего ──────────────────────────────────

@pytest.mark.parametrize("path", ["tests/unit/test_zz.py", "logs/zz_dump.py"])
def test_harness_and_generated_are_asked_for_nothing(path):
    blocks, _ = _run(path)
    assert blocks == [], f"{path} судится, хотя не должен: {blocks}"


# ── отказ инструмента суждения ────────────────────────────────────────────────

def test_unreadable_perimeter_blocks_and_judges_everything_strictly():
    """Симметрия с disposability.json: отказ инструмента не ослабляет требования.

    Иначе удаление одного файла данных снимало бы весь второй периметр разом — и тем тише,
    чем аккуратнее удаливший."""
    blocks, _ = _run("plans/zz_probe.py", sidecars=_sc("plans/zz_probe"), perim=None)
    assert _has(blocks, "периметр НЕ прочитан"), blocks
    assert _has(blocks, "нет теста"), "при нечитаемой таблице обязан спрашиваться и K2"


def test_class_absent_from_table_is_judged_strictly():
    blocks, _ = _run("plans/zz_probe.py", perim={"classes": {}, "dirs": {"plans": "исчезнувший"}})
    assert _has(blocks, "нет сайдкара") and _has(blocks, "нет теста"), blocks


# ── влияние на канон снимает освобождение по каталогу (ред. 3) ───────────────

@pytest.mark.parametrize("src", [CANON_SRC, CANON_SRC_FROM, CANON_SRC_CALL])
def test_touching_the_canon_revokes_the_directory_exemption(src):
    """Главный пункт ред. 3: «это же черновик» не защищает health.db.

    Три формы касания — импорт модуля, импорт имени, вызов имени — и каждая обязана вернуть
    файлу полную строгость, хотя лежит он в освобождённом каталоге."""
    blocks, _ = _run("plans/zz_touchy.py", sidecars=_sc("plans/zz_touchy"), src=src)
    assert _has(blocks, "нет теста"), f"файл в plans/ трогает канон и прошёл без теста: {blocks}"


def test_probe_mode_still_satisfies_k2_for_canon_touching_files():
    """Проба, трогающая канон, не обязана заводить тест-заглушку — но обязана предъявить
    исполненный негативный контроль. Строгость выросла, способ её удовлетворить остался."""
    sc = _sc("plans/zz_probe")
    sc["plans/zz_probe"]["kind"] = "probe"
    sc["plans/zz_probe"]["negative_control"] = {
        "harness": "plans/verify_zz.py", "ran_at": "2026-07-29",
        "mutations": [{"tag": "M-01", "statement": "сняли запрет", "verdict": "ПОКРАСНЕЛ"}]}
    blocks, _ = _run("plans/zz_probe.py", sidecars=sc, src=CANON_SRC)
    assert blocks == [], f"проба с исполненным негативным контролем не прошла: {blocks}"


def test_migration_touching_the_canon_now_needs_a_test():
    """Следствие, названное вслух: новая миграция, пишущая в боевую базу, обязана
    характеризоваться. Это ужесточение, и оно намеренное — цена решения владельца ред. 3."""
    blocks, _ = _run("migrations/zz_mig.py", sidecars=_sc("migrations/zz_mig"), src=CANON_SRC)
    assert _has(blocks, "нет теста"), blocks


def test_tests_are_never_revoked_even_when_touching_the_canon():
    """123 тестовых файла импортируют health_db. Перекрывать этот класс значит завести
    123 сайдкара на оснастку суждения — та же рекурсия, от которой класс и заведён."""
    blocks, _ = _run("tests/unit/test_zz.py", src=CANON_SRC)
    assert blocks == [], f"тест начали судить из-за импорта health_db: {blocks}"


def test_not_touching_the_canon_keeps_the_exemption():
    """Негат-контроль ред. 3: признак РАЗЛИЧАЕТ, а не красит папку целиком."""
    blocks, _ = _run("plans/zz_pure.py", sidecars=_sc("plans/zz_pure"), src=SRC)
    assert blocks == [], f"файл без касания канона потерял освобождение: {blocks}"


def test_outermost_component_decides_the_class():
    """`tests/snapshots/x.py` выпал из-за `tests`, а не из-за `snapshots`.

    Ратчет границы поймал эту же ошибку в собственной арифметике 2026-07-29 — здесь она
    заморожена как поведение гейта, чтобы не вернулась молча."""
    perim = {"classes": {"judgement_harness": {"k1": False, "k2": False},
                         "not_executed_in_operation": {"k1": True, "k2": False}},
             "dirs": {"tests": "judgement_harness", "plans": "not_executed_in_operation"}}
    blocks, _ = _run("tests/plans/zz_weird.py", perim=perim)
    assert blocks == [], f"класс взят от внутреннего компонента, а не от внешнего: {blocks}"
