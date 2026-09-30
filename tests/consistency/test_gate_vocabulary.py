"""Имя ворот, произнесённое в коде, обязано быть из словаря — и означать то же самое.

ЗАЧЕМ. Расхождение уже случилось и было тихим: профиль по периодам мы называли «Gate 3»,
автор критерия называет его «Gate 2». Обнаружилось это через два раунда переписки о
порогах, где номер ворот несёт смысл. Стоимость расхождения — не путаница в словах, а
риск откалибровать один механизм под порог другого.

ЧТО ЭТОТ ФАЙЛ НЕ ДЕЛАЕТ. Он не судит, верен ли словарь, и не проверяет, что механизм
делает обещанное. Он держит ровно одно: множество имён в коде ⊆ множество имён в
словаре, и у каждого объявленного реализованным ворота есть существующий носитель.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.consistency

ROOT = Path(__file__).resolve().parents[2]
VOCAB = ROOT / "methodology" / "validation_gate" / "gates.yaml"
# Переписка и планы — исторические документы: они фиксируют, как называли ТОГДА, и
# переписывать их задним числом значит потерять след расхождения.
SKIP_DIRS = {"tests", "plans", "docs", "migrations"}
GATE_RE = re.compile(r"\bGate\s(\d(?:\.\d)?)\b")


def _vocab() -> dict:
    return yaml.safe_load(VOCAB.read_text(encoding="utf-8"))["gates"]


def _sources():
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT)
        if rel.parts[0] in SKIP_DIRS or "__pycache__" in rel.parts:
            continue
        yield rel, p.read_text(encoding="utf-8")


def test_every_gate_named_in_code_is_in_the_vocabulary():
    known = set(_vocab())
    unknown = {}
    for rel, src in _sources():
        for m in GATE_RE.finditer(src):
            name = f"Gate {m.group(1)}"
            if name not in known:
                unknown.setdefault(name, []).append(str(rel))
    assert not unknown, (
        f"в коде названы ворота, которых нет в словаре: {unknown}. Либо опечатка, либо "
        f"словарь отстал от механизма — второе опаснее, потому что читается как согласие. "
        f"Дом словаря: {VOCAB.relative_to(ROOT)}")


def test_implemented_gates_point_at_something_that_exists():
    """Ссылка на несуществующий носитель — обещание механизма, которого нет.

    Такое поле хуже отсутствия: оно делает ворота «реализованными» для читателя словаря.
    """
    missing = []
    for gid, spec in _vocab().items():
        if not spec.get("implemented"):
            assert spec.get("why_absent"), (
                f"{gid} объявлены нереализованными без причины — отсутствие механизма "
                "законно, отсутствие объяснения нет")
            continue
        where = spec.get("where", "")
        mod, _, sym = where.partition("::")
        path = ROOT / mod
        if not path.exists() or not sym or sym not in path.read_text(encoding="utf-8"):
            missing.append(f"{gid} → {where!r}")
    assert not missing, f"реализованные ворота ссылаются в никуда: {missing}"


def test_our_period_profile_is_gate_two():
    """Позитивный контроль на КОНКРЕТНОЕ расхождение, стоившее двух раундов переписки.

    Мутация: вернуть в словарь «Gate 3 = Period Profile» — тест обязан покраснеть.
    """
    v = _vocab()
    assert v["Gate 2"]["name"] == "Period Profile"
    assert "_epoch_profile" in v["Gate 2"].get("where", "")
    assert v["Gate 3"]["name"] == "Leave-One-Period-Out"
    assert not v["Gate 3"]["implemented"], (
        "Gate 3 объявлены реализованными — но Leave-One-Period-Out мы не строили; "
        "если построили, замените и это утверждение")
