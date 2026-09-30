"""Ратчет переписи личных данных: долг в private/pii_baseline.json равен живому дереву.

Pre-commit судит только застейдженное; этот тест судит ВСЁ дерево — ловит коммит в обход
хука (--no-verify, коммит на машине без хуков) и правку словаря, после которой старый долг
перестал совпадать с реальностью. Равенство в ОБЕ стороны: долг выше реальности — это
право незаметно вернуть убранное, то есть ратчет, который не держит.
"""
from pathlib import Path

import pii_census as pc

ROOT = Path(pc.__file__).resolve().parent


def test_долг_переписи_равен_дереву():
    now = {p: sum(c.values()) for p, c in pc.census(ROOT).items()}
    debt = pc._load_baseline(ROOT)
    grew = {p: (debt.get(p, 0), n) for p, n in now.items() if n > debt.get(p, 0)}
    stale = {p: (d, now.get(p, 0)) for p, d in debt.items() if now.get(p, 0) < d}
    assert not grew, (f"личные данные прибавились в обход pre-commit (долг → стало): {grew}. "
                      "Обезличить или объявить файл приватным в publication_zones.yaml с причиной.")
    assert not stale, (f"долг выше дерева (долг → стало): {stale}. "
                       "Опустить: python3 pii_census.py rebaseline")
