"""Оракул: повторный ключ в реестре замысла не проходит молча.

ЗАМЕР, РАДИ КОТОРОГО ЭТО ЕСТЬ (15.09.2026). В `agent_coordination` у двух
инвариантов — `note_delivery_is_a_mechanism_not_attention` и
`sandbox_identity_cannot_hide` — оказалось ПО ДВА блока `limits:`. YAML в таком
случае молча берёт последний. Проверено парсером: из четырёх написанных границ
до реестра доехали две, а две исчезли, не оставив следа ни в диффе, ни в
гейтах. Среди потерянных — «отметка прочитано лежит в служебном каталоге
ДЕРЕВА, а у главной копии он общий», то есть ровно та граница, ради признания
которой limits и заводились.

ПОЧЕМУ ЭТО ХУЖЕ ОБЫЧНОЙ ОПЕЧАТКИ. Реестр — авторитет проекта по замыслу, а
гейт `intent_receipt` сверяет с ним квитанции планов. Тихо потерянная граница
превращает реестр в то самое «написанное по памяти», против чего он и заведён:
утверждение остаётся, оговорка к нему исчезает, и статус holds начинает значить
больше, чем заслужил.

ГРАНИЦА ЧЕСТНО. Ловится ТОЛЬКО дубль ключа в одном отображении. Что содержимое
limits правдиво, этот тест не судит и судить не может.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = [
    pytest.mark.test_meta(status="implemented", confirmation="confirmed"),
]

РЕЕСТР = Path(__file__).resolve().parents[2] / "subsystem_intent.yaml"


class _ЛовитДубли(yaml.SafeLoader):
    """SafeLoader, который отказывается склеивать повторные ключи."""


def _mapping(loader, node, deep=False):
    ключи = set()
    for k, _ in node.value:
        имя = loader.construct_object(k, deep=deep)
        if имя in ключи:
            raise yaml.constructor.ConstructorError(
                None, None,
                f"повторный ключ {имя!r} — YAML оставит только последний, "
                f"написанное выше исчезнет молча",
                k.start_mark)
        ключи.add(имя)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_ЛовитДубли.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def test_в_реестре_замысла_нет_повторных_ключей():
    assert РЕЕСТР.is_file(), f"нет реестра замысла {РЕЕСТР}"
    текст = РЕЕСТР.read_text(encoding="utf-8")
    try:
        yaml.load(текст, Loader=_ЛовитДубли)
    except yaml.constructor.ConstructorError as e:
        pytest.fail(
            f"реестр замысла теряет написанное: {e}\n"
            f"Второй блок с тем же ключом затирает первый — правь на месте, "
            f"а не дописывай рядом.")


def test_оракул_действительно_ловит_дубль():
    """Характеризация самого датчика.

    Без неё тест зелен и когда дублей нет, и когда ловилка сломана — а это
    ровно тот класс, который в этом проекте уже стрелял дважды (О-14, M3).
    """
    with pytest.raises(yaml.constructor.ConstructorError):
        yaml.load("a: 1\nb: 2\na: 3\n", Loader=_ЛовитДубли)
    # и не ложится на честном документе с одинаковыми ключами В РАЗНЫХ отображениях
    assert yaml.load("- x: 1\n- x: 2\n", Loader=_ЛовитДубли) == [{"x": 1}, {"x": 2}]
