"""Позитивные контроли Stage A oracle (§10, ступень 1).

Проверяют, что истинно-oracle p-симулятор воспроизводит гарантии вердикта:
  - BY контролирует FDR ≤ q под ЛЮБОЙ зависимостью (global null) — нормативная гарантия §4;
  - при частичном null мощность восстанавливается и FDR под контролем; BH мощнее BY (штраф §4);
  - под mixed-sign зависимостью BY по-прежнему контролирует.

Тяжёлый (numpy/scipy) → на MacBook без научного стека тест ПРОПУСКАЕТСЯ (importorskip),
гоняется на Studio/CI.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_HARNESS_DIR = str(Path(__file__).resolve().parents[2] / "methodology" / "validation_gate")
if _HARNESS_DIR not in sys.path:
    sys.path.insert(0, _HARNESS_DIR)

pytest.importorskip("numpy")
pytest.importorskip("scipy")
import fdr_stage_a as A  # noqa: E402

Q = 0.10
TRIALS = 1500


def test_by_controls_fdr_under_all_dependence_global_null():
    for dep in A.DEPENDENCE:
        r = A.run_oracle_cell(45, 0, dep, 0.5, 0.0, Q, TRIALS, seed=1)
        assert r["BY"]["fdr_hi"] <= Q + 0.02, f"BY не контролирует FDR под {dep}: {r['BY']}"


def test_partial_null_power_and_control():
    r = A.run_oracle_cell(45, 8, "independent", 0.5, 3.5, Q, TRIALS, seed=2)
    assert r["BH"]["power"] > 0.1
    assert r["BH"]["fdr_hi"] <= Q + 0.03
    assert r["BY"]["fdr_hi"] <= Q + 0.02
    assert r["BH"]["power"] >= r["BY"]["power"]   # BH мощнее — цена консервативности BY


def test_mixed_sign_by_controls():
    r = A.run_oracle_cell(45, 8, "mixed_sign", 0.5, 3.5, Q, TRIALS, seed=3)
    assert r["BY"]["fdr_hi"] <= Q + 0.02


def test_common_latent_no_false_discovery():
    # Общий скрытый драйвер (положительная эквикорреляция) под global-null не инфлирует FDR.
    r = A.run_oracle_cell(45, 0, "common_latent", 0.6, 0.0, Q, TRIALS, seed=4)
    assert r["BY"]["fdr_hi"] <= Q + 0.02
    assert r["BH"]["fdr_hi"] <= Q + 0.03   # положительная зависимость → BH тоже контролирует
