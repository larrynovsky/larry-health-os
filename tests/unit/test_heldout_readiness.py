"""Характеризация heldout_readiness.parks_due (D5) — гаситель оживления нити validation-gate-repair.

Чистая функция: тот же вход → тот же выход. Красит, если сломать порог/условие/провенанс.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

import heldout_readiness as h


def test_parks_due_branches():
    """Каждая ветка гасителя — своя строка. Порог floor инжектится, не хардкод."""
    assert h.parks_due(5, 0, 6) == [], "рано и лагов нет — ничего не поднимаем"
    assert [g for g, _ in h.parks_due(6, 0, 6)] == ["vg:heldout_ready"], "дозрело ровно на пол"
    assert [g for g, _ in h.parks_due(8, 0, 6)] == ["vg:heldout_ready"], "с запасом"
    assert [g for g, _ in h.parks_due(3, 2, 6)] == ["vg:signal_generative_promotion"], "лаг-выживший рано"
    assert [g for g, _ in h.parks_due(6, 1, 6)] == [
        "vg:heldout_ready", "vg:signal_generative_promotion"], "оба спусковых разом"
    assert h.parks_due(None, None, 6) == [], "нет данных → нечего судить (fail-open)"


def test_heldout_key_carries_freeze_promotion_key_does_not():
    """Один вопрос — один ключ (решение владельца 2026-08-23): «дозрело ли окно» — вопрос
    про КОНКРЕТНЫЙ freeze, ключ несёт frozen_at; иначе resolved по freeze N глушит звонок
    freeze N+1 навсегда (check_heldout_ready resolved не переоткрывает намеренно).
    Промоут §6.1 — standing-политика, его ключ freeze не несёт."""
    keys = [g for g, _ in h.parks_due(6, 1, 6, frozen_at="2026-07-12")]
    assert keys == ["vg:heldout_ready:2026-07-12", "vg:signal_generative_promotion"]
    # Свойство, ради которого правка: разные freeze → РАЗНЫЕ ключи heldout-гейта.
    k1 = h.parks_due(6, 0, 6, frozen_at="2026-07-12")[0][0]
    k2 = h.parks_due(6, 0, 6, frozen_at="2026-11-01")[0][0]
    assert k1 != k2, "resolved старого freeze не должен адресовать новый"
    # Обратная совместимость: без frozen_at — прежний голый ключ (старые записи стора).
    assert h.parks_due(6, 0, 6)[0][0] == "vg:heldout_ready"


def test_cards_offer_choices_without_engineering_details():
    for _, text in h.parks_due(6, 2, 6):
        assert "Варианты:" in text and text.count("• ") >= 2
        assert "Если промолчишь" in text
        assert not any(term in text for term in ("§", "gate_meta", "held-out", "FDR", ".py"))
