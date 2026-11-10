"""Сторож BL-ANTHROPIC-SDK-TEMPERATURE-1: установленный SDK anthropic принимает `temperature`.

Замер 2026-10-01 (нить llm-provider): в anthropic 1.11.0 у `Messages.create` параметра
`temperature` нет — вызов падает TypeError. `treatment_extractor.extract_regimens` передаёт
`temperature=0` и ловит ЛЮБОЕ исключение: после обновления SDK он молча вернёт пусто (строка
в журнале, тревоги нет). Прод на 0.92.0. Второй такой вызов, `lab_extractor.extract_labs`,
удалён 2026-11-09 вместе с модулем.

Тест краснеет на машине, где стоит SDK без параметра, — то есть сразу после обновления,
в полном прогоне. Что делать, когда покраснел: не откатывать тест, а решить, чем заменить
детерминизм в экстракторе (см. запись в BACKLOG), и только потом обновлять SDK.
"""
import inspect

import pytest

anthropic = pytest.importorskip("anthropic")


def _accepts(fn, name: str) -> bool:
    params = inspect.signature(fn).parameters
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def test_installed_sdk_accepts_temperature():
    from anthropic.resources.messages import AsyncMessages, Messages
    for cls in (Messages, AsyncMessages):
        assert _accepts(cls.create, "temperature"), (
            f"anthropic {anthropic.__version__}: {cls.__name__}.create не принимает temperature — "
            "treatment_extractor начнёт возвращать пусто. BL-ANTHROPIC-SDK-TEMPERATURE-1")


def test_guard_is_load_bearing():
    """Негативный контроль: функция без параметра судится «не принимает»."""
    def create(*, model, max_tokens, messages):
        return None
    assert not _accepts(create, "temperature")
    assert _accepts(lambda **kw: None, "temperature")
