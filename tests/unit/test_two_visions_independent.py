"""ОРАКУЛ обещания реестра `lab_recognizer/two_model_reconciled`.

Замер У-1 (2026-07-30): обещание «скан читают ДВА независимых зрения разными промптами
постранично и сверяются между собой» не было защищено ничем. Второе зрение можно было
заменить копией первого (`p2 = p1`) — и ни один тест, ни один датчик не покраснели.
Реестр продолжал показывать `holds`, потому что проверял присутствие слов `_vision_call`,
`_reconcile`, `confidence` в файле.

ЦЕНА ПОЛОМКИ. Ансамбль существует не для красоты: модель, читающая скан напрямую, роняет
десятичную точку — 15.2 становится 152. Сверка двух проходов — единственное место, где такая
ошибка становится `disagree` вместо тихого числа в каноне. Копия первого прохода даёт
«agree» ВСЕГДА и превращает предохранитель в его имитацию: доверие растёт, проверка исчезла.

ГРАНИЦА, названная прямо. Тест доказывает, что зрений ДВА, что промпты и модели РАЗНЫЕ и что
сверка видит расхождение. Он НЕ доказывает, что два зрения независимы в смысле суждения:
две модели одного семейства могут ошибаться одинаково, и тогда «agree» не значит «верно».
Это tacit-суждение владельца, машинно недоступное.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


class _VisionSpy:
    """Шпион вместо обоих зрений. Возвращает РАЗНЫЕ показания в зависимости от модели.

    Разные показания обязательны: если бы шпион отвечал одинаково, тест не отличил бы
    два зрения от одного, вызванного дважды, — то есть проверял бы ровно не тот предмет.
    """

    def __init__(self, model_p1: str):
        self.calls: list[dict] = []
        self._model_p1 = model_p1

    def __call__(self, image: bytes, prompt: str, model: str) -> list[dict]:
        self.calls.append({"image": image, "prompt": prompt, "model": model})
        value = 5.0 if model == self._model_p1 else 9.0
        return [{"canonical_name": "Glucose", "raw_name": "Glucose",
                 "value": value, "unit": "mg/dL"}]


@pytest.fixture
def spied(monkeypatch, tmp_path):
    import lab_recognizer as lr
    spy = _VisionSpy(lr._MODEL_PASS1)
    # `pages=None` в подписи с 2026-08-08 (адресное перечитывание): подмена обязана
    # принимать тот же вызов, что и настоящая функция, иначе тест зеленеет на форме,
    # которой в проде нет.
    monkeypatch.setattr(lr, "_render_pages", lambda p, pages=None: [b"PAGE-1", b"PAGE-2"])
    monkeypatch.setattr(lr, "_vision_call", spy)
    res = lr.recognize(tmp_path / "doc.pdf", "2026-07-30")
    return lr, spy, res


def test_each_page_is_read_by_two_visions(spied):
    """Два вызова зрения НА КАЖДУЮ страницу, и оба по одной и той же странице.

    `p2 = p1` роняет этот тест по числу вызовов: второго обращения к зрению просто нет.
    """
    _lr, spy, res = spied
    assert res["stats"]["pages"] == 2
    assert len(spy.calls) == 4, \
        f"ожидалось 2 страницы × 2 зрения = 4 вызова, было {len(spy.calls)}: " \
        f"второе зрение не вызвано (заменено копией первого?)"
    for page_no, (a, b) in enumerate([spy.calls[0:2], spy.calls[2:4]], start=1):
        assert a["image"] == b["image"], \
            f"стр.{page_no}: два зрения смотрят РАЗНЫЕ картинки — сверка бессмысленна"


def test_two_visions_differ_in_prompt_and_model(spied):
    """Промпты различны И модели различны — иначе это одно зрение, вызванное дважды."""
    lr, spy, _res = spied
    for page_no, (a, b) in enumerate([spy.calls[0:2], spy.calls[2:4]], start=1):
        assert a["prompt"] != b["prompt"], \
            f"стр.{page_no}: промпты совпали — «два разных промпта» перестало быть правдой"
        assert a["model"] != b["model"], \
            f"стр.{page_no}: модель одна и та же — независимость зрений утрачена"
    assert {c["model"] for c in spy.calls} == {lr._MODEL_PASS1, lr._MODEL_PASS2}


def test_reconcile_sees_the_disagreement_between_visions(spied):
    """Расхождение двух зрений доезжает до `value_agreement`/`confidence` и до stats.

    Это утверждение, которое ломает копия первого прохода тише всего: вызовы могут быть
    оба на месте, но если второй читает тем же промптом и той же моделью, показания
    сойдутся и `agree` станет константой. Тогда `confidence: high` будет стоять на всём,
    включая уехавшую десятичную точку.
    """
    _lr, _spy, res = spied
    assert res["tests"], "реконсиляция не вернула ни одной строки"
    t = res["tests"][0]
    assert t["pass1_value"] == 5.0 and t["pass2_value"] == 9.0, \
        f"показания зрений слиплись: pass1={t['pass1_value']} pass2={t['pass2_value']}"
    assert t["value_agreement"] == "disagree", \
        "5.0 против 9.0 объявлено согласием — сверка не сверяет"
    assert t["confidence"] == "low", "расхождение не понизило доверие"
    assert res["stats"]["disagreements"] == len(res["tests"]), \
        "расхождение не доехало до stats — читатель маршрута его не увидит"
