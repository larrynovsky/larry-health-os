"""tests/unit/test_validation_gate_methodology_sentinel.py — датчик наличия методологии гейта.

Инвариант validation_gate::spec_provenance_in_git: load-bearing методология гейта
(спека = замысел подсистемы, на неё ссылается реестр; fdr_harness/fdr_sweep = числа
вердикта статистиков) перенесена из iCloud в git (СК-1) и под сторожем. Позитивный
контроль (RST): сторож обязан краснеть при пропаже. Плюс проверяем, что реальная
git-методология проходит.
"""
from __future__ import annotations

import pytest

import integrity_tests as it

pytestmark = pytest.mark.unit


def test_vg_methodology_sentinel_passes_on_real_git():
    """Реальная git-методология: спека непуста, harness с ключевыми функциями."""
    r = it.check_validation_gate_methodology_present()
    assert r["spec_bytes"] > 2000
    assert r["harness_fns"] == 4
    assert r["family_version"] >= 1   # signal_family.yaml (замороженная семья §12.3) на месте и полон
    assert r["has_qual_scaffold"] is True   # qualification_protocol.yaml + fdr_qualification.py (Ф0-каркас)


def test_vg_methodology_sentinel_fails_when_missing(tmp_path):
    """Позитивный контроль: пустой root (нет спеки/harness) → AssertionError (не тихо OK)."""
    with pytest.raises(AssertionError):
        it.check_validation_gate_methodology_present(root=tmp_path)
