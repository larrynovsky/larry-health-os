"""Шаг import_medical_events собирает файлы и не падает на этом.

Дефект, ради которого тест существует: выражение `(".pdf",) | IMAGE_EXTS`
(кортеж | множество) давало TypeError, шаг падал на КАЖДОМ запуске вотчера
с 2026-05-18 по 2026-07-29 — 231 отказ в ~/health_watcher.log, ни одного алерта.
Сухой прогон с починкой показал: часть медицинских документов доехала бы,
остальные пропущены как уже импортированные либо платёжные.

Честная граница: это тест на ОТБОР файлов, а не на весь шаг. Настоящий оракул
класса «мёртвый шаг выглядит живым» — видимость отказов вотчера, и она
строится отдельно (§14). Тест здесь стоит одну строку и ловит ровно ту
ошибку, что жила два месяца.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_doc_exts_is_a_usable_set_of_extensions():
    import import_medical_events as ime
    assert isinstance(ime.DOC_EXTS, set)
    assert {".pdf", ".jpg", ".jpeg", ".png", ".heic"} <= ime.DOC_EXTS


@pytest.mark.parametrize("suffix,expected", [
    (".pdf", True), (".PDF".lower(), True), (".heic", True),
    (".jpeg", True), (".zip", False), (".md", False), ("", False),
])
def test_membership_decides_what_the_step_takes(suffix, expected):
    """Ровно та проверка, которая и падала: принадлежность расширения набору.
    Мембership на множестве работает; на выражении с `|` над кортежем — нет."""
    import import_medical_events as ime
    assert (suffix in ime.DOC_EXTS) is expected
