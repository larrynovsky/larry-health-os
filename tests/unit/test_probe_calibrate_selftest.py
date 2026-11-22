"""Self-test проб калибровки профиля (Gate 2) зелёный на живом гейте.

С 2026-08-08 блок Gate 2 входит в `gate_pass`, и пробы, читавшие `gate_pass` как «прошёл
Gate 1», калибровали порог сквозь него самого: синтетический Negative отбивался профилем,
негативный контроль [а] «без порогов Negative проходит» был красным, пробы ушли в
SKIPPED_PROBES (run_probes.sh). Оракул ловит возврат: снимешь выключение Gate 2 в
`_through_gate` — оба теста краснеют.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(rel: str):
    spec = importlib.util.spec_from_file_location(Path(rel).stem, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("rel", [
    "plans/probe_calibrate_rmin_2026-08-01.py",
    "plans/probe_calibrate_two_component_2026-08-04.py",
])
def test_probe_self_test_green(rel):
    assert _load(rel)._self_test() == 0


def test_gate2_thresholds_restored_after_run():
    """Выключение Gate 2 на время прогона не протекает в процесс: пороги возвращены."""
    import signal_family as sf
    before = sf.GATE2_R_MIN, sf.GATE2_SIGN_MIN
    mod = _load("plans/probe_calibrate_two_component_2026-08-04.py")
    from methodology.validation_gate import synthetic_controls as sc
    x, y, _ = sc.control_series("negative", 42)
    mod._through_gate(x, y)
    assert (sf.GATE2_R_MIN, sf.GATE2_SIGN_MIN) == before


def test_gate2_thresholds_restored_when_gate_raises(monkeypatch):
    """Ветка исключения (ревью 7fcc4cde, п.2): гейт упал внутри прогона — пороги всё равно возвращены."""
    import signal_family as sf
    before = sf.GATE2_R_MIN, sf.GATE2_SIGN_MIN
    mod = _load("plans/probe_calibrate_two_component_2026-08-04.py")
    from methodology.validation_gate import synthetic_controls as sc

    def boom(*a, **k):
        raise RuntimeError("выдуманный сбой гейта")
    monkeypatch.setattr(mod.G, "gate_correlations", boom)
    x, y, _ = sc.control_series("negative", 42)
    with pytest.raises(RuntimeError):
        mod._through_gate(x, y)
    assert (sf.GATE2_R_MIN, sf.GATE2_SIGN_MIN) == before
