"""Класс «хронология» словаря переписи (решение владельца 2026-09-25 «чистить сейчас»).

Словарь приватный, поэтому тест бежит только у владельца (owner_data). Оракул: образцы
утечки из прежних комментариев ловятся, соседние законные фразы (дата коммита, синтетический
номер) — нет. Снятие шаблона из словаря краснеет здесь, а не молчит в pre-commit.
"""
import pytest

import pii_census as pc

pytestmark = pytest.mark.owner_data

# Образцы утечки — сами личные данные, поэтому живут в приватном словаре (probes), не здесь:
# склейка строк прятала их от переписи, но не от читателя (чтение не автором 2026-09-26).
LEAKS = pc.probes("chronology_leaks")  # образцы — в приватном словаре (probes.chronology_leaks), см. pii_census.probes
CLEAN = [
    "Сверка 2026-08-12 с бланками",
    "# Найдено 2026-08-31 дроем триажа живого бланка.",
    "Заявка №: 1000000001",
    "(найдено на бланке CR/10000000001.PDF)",
    "vagal_activation",
    "DPYD: 5-ФУ/капецитабин — общая фармакогенетика",
]


def test_probes_present():
    """Пустой список образцов превратил бы оракул в молчащий зелёный (параметризация по пустому)."""
    assert len(LEAKS) >= 5, "в private/pii_terms.yaml нет probes.chronology_leaks"


def _hit(line):
    return [c for c, p in pc._terms(pc.ROOT).items() if p.search(line)]


@pytest.mark.parametrize("line", LEAKS)
def test_chronology_leak_is_caught(line):
    assert _hit(line), f"утечка не поймана словарём: {line!r}"


@pytest.mark.parametrize("line", CLEAN)
def test_neighbour_phrase_is_not_caught(line):
    assert not _hit(line), f"ложное срабатывание: {line!r} → {_hit(line)}"
