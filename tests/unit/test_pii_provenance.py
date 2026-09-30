"""Класс «provenance» словаря переписи (урок C-67, нить marker-guard 2026-09-26).

Пометка автора «это настоящие данные <чьи / откуда>» — самый дешёвый признак утечки, а перепись
литералов её не читала: генотип, вкус и онко-аудит владельца прошли три чистки при отчёте 0 и
нашлись по таким пометкам. Оракул: исторические формы пометок (писали другие сессии, июнь–сентябрь)
ловятся; соседние законные фразы — нет. Граница вслух: ловится ПОМЕТКА, не данные.

Словарь приватный → тест бежит только у владельца (owner_data). Образцы утечек — сами личные
данные, поэтому живут в приватном словаре (probes.provenance_leaks): склейка строк прятала их
от переписи, но не от читателя (чтение не автором 2026-09-26).
"""
import pytest

import pii_census as pc

pytestmark = pytest.mark.owner_data

_ = "".join
LEAKS = pc.probes("provenance_leaks")  # образцы — в приватном словаре (probes.provenance_leaks), см. pii_census.probes
CLEAN = [
    "проверено на реальных данных, не только на тестовых",
    "Метки дословно из logs/integrity_latest.json на 2026-09-13.",
    "Синтетика по образцу строк аудита state-класса",
    "не взят из живой базы.",
    "реальные данные продуктов из рубрикатора",
    "Хочешь проверить незакоммиченный код на реальных данных",
    "actual = compute(x)",
]


def test_probes_present():
    assert len(LEAKS) >= 5, "в private/pii_terms.yaml нет probes.provenance_leaks"


def _hit(text):
    p = pc._terms(pc.ROOT).get("provenance")
    assert p is not None, "класс provenance пропал из словаря"
    return p.search(text)


@pytest.mark.parametrize("text", LEAKS)
def test_provenance_marker_is_caught(text):
    assert _hit(text), f"пометка не поймана: {text!r}"


@pytest.mark.parametrize("text", CLEAN)
def test_neighbour_phrase_is_not_caught(text):
    assert not _hit(text), f"ложное срабатывание: {text!r} → {_hit(text).group(0)!r}"


def test_block_shows_line_of_wrapped_marker():
    """Находка через перенос строки получает адрес: иначе блок без адреса (26.09)."""
    where = pc._where(pc.ROOT, "x.py", _(["a\nТест — recall на реальных при", "мерах\nиз аудита"]))
    assert any("строка 2" in w and "[provenance]" in w for w in where), where
