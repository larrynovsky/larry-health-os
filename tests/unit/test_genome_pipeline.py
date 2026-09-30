"""
genome_pipeline — оркестратор геном-стека: порядок-гейт, идемпотентность, verify.

Ключевой инвариант (инцидент 2026-07-04): S6 (конституции) не запускается на
пустом effect_allele; S7 ловит уже-полое состояние.
"""
from __future__ import annotations

import pytest

import genome_pipeline as gp

pytestmark = pytest.mark.unit


def test_s6_gate_blocks_without_effect_allele(monkeypatch):
    monkeypatch.setattr(gp, "_n_constitutions", lambda: 0)  # чтобы не сработал skip
    monkeypatch.setattr(gp, "_n_resolved", lambda: 0)
    with pytest.raises(RuntimeError, match="resolved==0"):
        gp.s6_constitutions()


def test_s7_verify_flags_hollow(monkeypatch):
    monkeypatch.setattr(gp, "_n_raw", lambda: 100)
    monkeypatch.setattr(gp, "_n_annotated", lambda: 100)
    monkeypatch.setattr(gp, "_n_resolved", lambda: 0)
    monkeypatch.setattr(gp, "_n_prs", lambda: 0)
    monkeypatch.setattr(gp, "_n_constitutions", lambda: 5)
    with pytest.raises(RuntimeError, match="ПОЛЫЕ|полы"):
        gp.s7_verify()


def test_s7_verify_ok_when_resolved(monkeypatch):
    monkeypatch.setattr(gp, "_n_raw", lambda: 100)
    monkeypatch.setattr(gp, "_n_annotated", lambda: 100)
    monkeypatch.setattr(gp, "_n_resolved", lambda: 60)
    monkeypatch.setattr(gp, "_n_prs", lambda: 10)
    monkeypatch.setattr(gp, "_n_constitutions", lambda: 5)
    res = gp.s7_verify()
    assert res["hollow_constitutions"] is False


def test_s1_idempotent_skip(monkeypatch):
    monkeypatch.setattr(gp, "_n_raw", lambda: 500)
    called = {"v": False}
    import genome_parser
    monkeypatch.setattr(genome_parser, "import_raw_genome",
                        lambda *a, **k: called.__setitem__("v", True))
    gp.s1_import(filepath="/x/genome.txt", force_import=False)
    assert called["v"] is False, "S1 должен пропуститься при непустом raw_snps"


def test_s2_idempotent_skip(monkeypatch):
    monkeypatch.setattr(gp, "_n_annotated", lambda: 900)
    called = {"v": False}
    import genome_annotator
    monkeypatch.setattr(genome_annotator, "annotate_all",
                        lambda *a, **k: called.__setitem__("v", True))
    gp.s2_annotate()
    assert called["v"] is False


def test_dry_run_no_execution(db):
    # dry не должен трогать стадии и возвращает пустую сводку
    assert gp.run_pipeline(dry=True) == {}


def test_s6_idempotent_skip_when_complete(monkeypatch):
    monkeypatch.setattr(gp, "_n_constitutions", lambda: 5)
    monkeypatch.setattr(gp, "_n_resolved", lambda: 60)
    import generate_constitutions as gc
    monkeypatch.setattr(gc, "_generate_one",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("regen без --regen!")))
    gp.s6_constitutions(regen=False)  # должен пропуститься, не вызвав генерацию


def test_stage_order_is_fixed():
    assert gp._ORDER == ["S1", "S2", "S3", "S4", "S5", "S6", "S7"]
