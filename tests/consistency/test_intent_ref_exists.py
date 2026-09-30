"""tests/consistency/test_intent_ref_exists.py — сторож висячих inv-ссылок.

Зеркало к `test_intent_doc_home_refs`: тот стережёт связь реестр→код→страница
(стрелки ОТ реестра). Здесь — обратная стрелка: если живой док упоминает
инвариант реестра по id (`подсистема::инвариант`), этот id обязан СУЩЕСТВОВАТЬ
в subsystem_intent.yaml.

Зачем: SECURITY.md машинно-дополняется `doc_agent` (append-only SEC-XX; старые
записи не переписываются НИКОГДА — не codegen-регенерация, свежести не даёт), и уже
несёт `multitenancy::delivery_coverage_open` (SEC-22). BLUEPRINT §253 несёт
`deterministic_norm_source::fires_regardless_of_signals`. Переименуют/удалят
инвариант в реестре — указатель повиснет в пустоту МОЛЧА. `doc_agent` (только
дописывает) это не чинит.

Оракул-чистая проверка: существование пары, НЕ правота статуса (это check, не test).
Дискриминатор inv-ref vs код-якорь: inv-id — dotless lowercase snake `a::b`; код-якорь
всегда несёт `.py` слева (`fname.partition('::')`, sentinel требует файл на диске).
Lookbehind `(?<![.\\w])` отсекает хвост код-якоря (`paths.py::get` → `py` за точкой).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import intent_registry as ir

pytestmark = pytest.mark.consistency

ROOT = Path(__file__).resolve().parents[2]
# Доки, несущие inv-указатели. Расширять по мере появления новых носителей —
# перед этим прогнать grep dotless `a::b` по дереву (см. census 2026-07-12).
REF_DOCS = ["SECURITY.md", "CLAUDE.md"]    # BLUEPRINT.md удалён 2026-08-03

# dotless lowercase snake a::b, не в хвосте код-якоря и не в середине слова
_REF = re.compile(r"(?<![.\w])([a-z][a-z0-9_]{2,}::[a-z][a-z0-9_]{2,})(?![.\w])")


def valid_pairs() -> set[str]:
    return {
        f"{e['id']}::{inv['id']}"
        for e in ir.load_registry()
        for inv in e.get("invariants", [])
    }


def dangling_refs(text: str, valid: set[str]) -> list[str]:
    """Строки с inv-ссылкой, которой нет в реестре. Чистая функция."""
    bad = []
    for i, line in enumerate(text.splitlines(), 1):
        for tok in _REF.findall(line):
            if tok not in valid:
                bad.append(f"L{i}: {tok}")
    return bad


@pytest.mark.parametrize("doc", REF_DOCS)
def test_doc_inv_refs_exist(doc):
    f = ROOT / doc
    if not f.exists():
        pytest.skip(f"{doc} отсутствует")
    bad = dangling_refs(f.read_text(encoding="utf-8"), valid_pairs())
    assert not bad, (
        f"{doc}: ссылка на инвариант реестра, которого НЕТ в subsystem_intent.yaml — "
        f"висячий указатель (переименовали/удалили инвариант, док стух молча). "
        f"Почини ссылку или верни инвариант. Строки:\n  " + "\n  ".join(bad)
    )


# ── Позитивные контроли (RST): ловит висячий, молчит на валидный и на код-якорь ──
_V = {"multitenancy::delivery_coverage_open"}


def test_control_flags_dangling():
    assert dangling_refs("см. multitenancy::renamed_gone в реестре", _V) == [
        "L1: multitenancy::renamed_gone"
    ]


def test_control_clean_on_valid():
    assert dangling_refs("SEC-22 → multitenancy::delivery_coverage_open", _V) == []


def test_control_ignores_code_anchor():
    # код-якорь с .py слева не должен читаться как inv-ref
    assert dangling_refs("якорь secrets_paths.py::get_chat_id живёт", _V) == []
