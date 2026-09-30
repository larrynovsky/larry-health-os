"""Ратчет соответствия сайдкар-контрактов и модулей (§15, K1).

ЗАЧЕМ. Ключ сайдкара считается ОДНИМ способом — `indexer.contract_sidecars` берёт его из поля
`module`, а форма ключа — путь БЕЗ `.py`, как у индекса. Но подсказку печатал другой код
(`project_context disposability`), и печатал он форму С `.py`. Сайдкар, написанный по скелету
собственного инструмента дословно, оказывался гейту НЕВИДИМ: файл есть, контракт есть, а гейт
говорит «нет сайдкара». Поймано 30.07 на живом коммите, три итерации блока.

Класс шире случая: инструмент, печатающий подсказку, ошибался ровно в том, что подсказывал.
Такой дефект не ловится ни одним тестом ГЕЙТА, потому что гейт-то прав — неправа подсказка.
Поэтому ратчет судит не гейт и не подсказку по отдельности, а ИХ СОГЛАСИЕ с деревом.

ЧТО ЭТОТ ТЕСТ НЕ ДОКАЗЫВАЕТ: что содержимое сайдкара осмысленно. Он про адресацию — ключ
резолвится в существующий модуль, и у модуля с сайдкаром ключ ровно один.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.consistency

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"

# Известные расхождения. Пусто — и это утверждение, а не умолчание: список ЗАКРЫТ, любое
# новое расхождение обязано либо чиниться, либо появляться здесь с причиной и решением человека.
KNOWN_BROKEN: dict[str, str] = {}


def _sidecars():
    out = []
    for p in sorted(CONTRACTS.rglob("*.json")):
        rel = p.relative_to(CONTRACTS).as_posix()
        out.append((rel, json.loads(p.read_text(encoding="utf-8"))))
    return out


def test_there_are_sidecars_at_all():
    """Позитивный контроль охвата: пустой каталог сделал бы все проверки ниже зелёными впустую.

    Ровно тот класс, которым 30.07 отравились семь инструментов за сутки — верны по логике,
    слепы по области определения.
    """
    assert len(_sidecars()) >= 10, "сайдкаров подозрительно мало — проверь путь contracts/"


def test_every_sidecar_key_resolves_to_an_existing_module():
    """Ключ сайдкара обязан указывать на существующий `.py`, и БЕЗ расширения в самом ключе."""
    broken = []
    for rel, data in _sidecars():
        key = data.get("module") or rel[:-5]
        if rel in KNOWN_BROKEN:
            continue
        if key.endswith(".py"):
            broken.append(f"{rel}: module={key!r} — ключ с расширением, гейт ищет форму без .py")
        elif not (ROOT / f"{key}.py").exists():
            broken.append(f"{rel}: module={key!r} → файла {key}.py нет")
    assert not broken, (
        "сайдкары, невидимые гейту (файл есть, контракт есть, суда нет):\n  " + "\n  ".join(broken))


def test_filename_matches_the_declared_key():
    """Имя файла и поле `module` говорят об одном модуле.

    Расхождение не ломает гейт (он читает поле), но делает каталог нечитаемым для человека —
    а сайдкар пишется в том числе для человека, который будет переписывать модуль с нуля.
    """
    mismatched = []
    for rel, data in _sidecars():
        if rel in KNOWN_BROKEN:
            continue
        declared = data.get("module")
        if declared and declared != rel[:-5]:
            mismatched.append(f"{rel}: module={declared!r} ≠ имя файла")
    assert not mismatched, "имя файла разошлось с ключом:\n  " + "\n  ".join(mismatched)


def test_known_broken_list_is_honest():
    """Запись в списке известных расхождений обязана быть НАСТОЯЩИМ расхождением.

    Иначе список превращается в свалку: файл починили, строка осталась, и следующий читатель
    видит «известную дыру», которой нет. Равенство множеств в обе стороны — тот же приём, что
    в ратчете известных обходов гейта.
    """
    stale = []
    for rel, reason in KNOWN_BROKEN.items():
        p = CONTRACTS / rel
        if not p.exists():
            stale.append(f"{rel}: файла нет вовсе ({reason})")
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        key = data.get("module") or rel[:-5]
        if not key.endswith(".py") and (ROOT / f"{key}.py").exists() and key == rel[:-5]:
            stale.append(f"{rel}: расхождение УСТРАНЕНО — вычеркни строку ({reason})")
    assert not stale, "список известных расхождений врёт:\n  " + "\n  ".join(stale)
