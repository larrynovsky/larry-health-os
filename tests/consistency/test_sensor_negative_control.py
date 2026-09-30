"""НОВЫЙ датчик приносит исполненный негативный контроль (решение владельца 15.09).

ЗАЧЕМ. Датчик бывает зелёным по двум причинам: всё в порядке — или он сломан и не умеет
краснеть. Различить их можно ровно одним способом: сломать механизм руками и посмотреть.
На 15.09 такая проверка предъявлена у ДВУХ датчиков из 165. Про остальные 163 неизвестно
не то, что они плохи, а то, что мы не знаем. История проекта показывает, чем это
кончается: демон шестнадцать суток исполнял код из памяти, и ВСЕ датчики были зелёными.

ПОЧЕМУ РАТЧЕТ, А НЕ ТРЕБОВАНИЕ КО ВСЕМ. Мутация на каждый из 163 — недели работы,
большая часть которой уйдёт на датчики, которых никто не читает. Ратчет останавливает
рост долга, не требуя его погашения: старое живёт в списке легаси, новое приходит с
доказательством. Долг при этом ВИДЕН числом, а не растворён.

ФОРМАТ НЕ ИЗОБРЕТЁН. Блок `negative_control` (harness · ran_at · mutations[tag ·
statement · verdict]) — тот же, что у проб в `contracts/plans/*.json`, и судит его тот же
код: `project_context.dispgate._probe_evidence_problems`. Второй дом формата для того же
предмета был бы ровно тем split-brain, от которого §15 и защищает.

ЧЕСТНАЯ СИЛА, дословно как у соседа-судьи: гейт проверяет, что квитанция ПОЛНА и
внутренне непротиворечива. Он НЕ может проверить, что прогон был — квитанцию можно
написать руками. Это названо вслух, а не спрятано.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from project_context import dispgate

pytestmark = pytest.mark.consistency

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "project_context" / "integrity_sensors.json"

# Ратчет ОХВАТА: столько датчиков было зарегистрировано 15.09. Нижняя граница ловит не
# рост долга, а слепоту предиката — AST-обход, переставший находить регистрации, иначе
# отрапортовал бы «нарушений 0», осмотрев ноль кандидатов (урок test_probe_coverage).
RATCHET_SENSORS_SEEN = 160


def _registered_checks() -> set[str]:
    """Имена функций, зарегистрированных как датчики через `check(<текст>, <функция>)`.

    По AST, а не по подстроке: `check_` в комментарии или в строковом литерале не должен
    считаться регистрацией.
    """
    src = (ROOT / "integrity_tests.py").read_text(encoding="utf-8")
    return {n.args[1].id for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "check" and len(n.args) >= 2
            and isinstance(n.args[1], ast.Name)}


def _registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def test_predicate_still_finds_sensors():
    """Сторож сначала доказывает, что ему есть на что смотреть."""
    seen = len(_registered_checks())
    assert seen >= RATCHET_SENSORS_SEEN, (
        f"датчиков найдено {seen} при ратчете {RATCHET_SENSORS_SEEN} — скорее всего "
        f"сломался AST-обход, а не исчезли датчики")


def test_new_sensor_brings_an_executed_negative_control():
    """Датчик, которого не было 15.09, обязан предъявить исполненный негативный контроль.

    Это и есть ратчет: список легаси заморожен, и всё, что появилось после, либо несёт
    доказательство красноты, либо осознанно вносится в легаси отдельной правкой — то
    есть решением, а не умолчанием.
    """
    reg = _registry()
    known = set(reg["legacy_without_negative_control"]) | set(reg["negative_control"])
    newcomers = sorted(_registered_checks() - known)
    assert not newcomers, (
        f"новые датчики без исполненного негативного контроля: {newcomers}. "
        f"Датчик, про который неизвестно, умеет ли он краснеть, занимает место оракула "
        f"и молчит ровно тогда, когда механизм сломан. Добавь блок в "
        f"project_context/integrity_sensors.json::negative_control (формат — как у проб) либо "
        f"внеси в legacy_without_negative_control ОСОЗНАННО, отдельной строкой в коммите")


def test_registry_does_not_promise_sensors_that_do_not_exist():
    """Обратная асимметрия: реестр не должен помнить удалённые датчики.

    Без этого список легаси растёт молча и навсегда, а «долг 163» перестаёт быть
    замером — становится памятником.
    """
    reg = _registry()
    live = _registered_checks()
    phantom = sorted((set(reg["legacy_without_negative_control"]) | set(reg["negative_control"]))
                     - live)
    assert not phantom, (
        f"реестр помнит датчики, которых в коде нет: {phantom} — переименованы или "
        f"удалены; почисти, иначе счёт долга врёт")


def test_declared_controls_are_judged_by_the_same_judge_as_probes():
    """Каждая квитанция проходит ЧУЖОГО судью — того, что судит пробы.

    Своей проверки формата здесь нет намеренно: два судьи одного формата разъезжаются
    молча. Если у проб появится новое требование, оно автоматически применится и сюда.
    """
    bad = {}
    for name, nc in _registry()["negative_control"].items():
        problems = dispgate._probe_evidence_problems({"negative_control": nc})
        if problems:
            bad[name] = problems
    assert not bad, f"квитанции негативного контроля неполны или противоречивы: {bad}"


def test_legacy_is_a_debt_with_a_number_not_a_silence():
    """Долг обязан быть виден числом.

    Тест не требует его погашения — он требует, чтобы размер долга нельзя было потерять
    из виду, растворив легаси в пустом списке или в отсутствующем ключе.
    """
    reg = _registry()
    legacy = reg["legacy_without_negative_control"]
    assert isinstance(legacy, list) and legacy, (
        "список легаси пуст или отсутствует — значит либо все 165 датчиков получили "
        "негативный контроль (тогда удали ключ отдельным решением), либо долг потерян")
