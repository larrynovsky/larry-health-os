"""tests/unit/test_cbcr_methodology_sentinel.py — датчик наличия CBCR-методологии.

Инвариант self_monitoring::external_registry_incomplete_open: load-bearing CBCR
(manifest = system-prompt генерации гипотез, wiki = concept-tool) перенесён из iCloud
в git (СК-1) и под сторожем. Позитивный контроль (RST): сторож обязан краснеть при
пропаже. Плюс проверяем, что реальная git-методология проходит.
"""
from __future__ import annotations

import pytest

import integrity_tests as it
import cbcr_lookup

pytestmark = pytest.mark.unit


def test_cbcr_sentinel_passes_on_real_git():
    """Реальная git-методология (methodology/cbcr) — манифест непуст, ≥50 концептов."""
    r = it.check_cbcr_methodology_present()
    assert r["manifest_bytes"] > 1000
    assert r["wiki_concepts"] >= 50


def test_cbcr_sentinel_fails_when_wiki_missing(monkeypatch, tmp_path):
    """Позитивный контроль: пустой/пропавший wiki → AssertionError (не тихо OK)."""
    empty = tmp_path / "cbcr" / "wiki"
    empty.mkdir(parents=True)  # каталог есть, но 0 концептов и нет манифеста рядом
    monkeypatch.setattr(cbcr_lookup, "WIKI_DIR", empty)
    with pytest.raises(AssertionError):
        it.check_cbcr_methodology_present()
