"""Tenant artifact paths, receipt wiring and run locks; no database access."""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
ARTIFACTS = {
    "DEFAULT_OUT": "outputs/longitudinal_analysis.xlsx",
    "DRYRUN_DIR": "logs/dryrun",
    "MC_GAP_ARTIFACT": "logs/gate_mc_gap_latest.json",
    "PASSSET_ARTIFACT": "logs/gate_passset_history.json",
    "EPOCH_BREAK_MARK": "logs/passset_epoch_break.txt",
    "RUN_LOCK": "logs/gate_run.lock",
    "GATE_RUN_RECEIPT": "logs/gate_run_receipt.json",
}


@pytest.fixture
def load_tenant(monkeypatch, tmp_path):
    """Fresh service modules model separate processes without leaking reloads."""
    def no_database(*args, **kwargs):
        pytest.fail("Path and lock tests must not open a database")

    monkeypatch.setattr(sqlite3, "connect", no_database)

    def load(dirname):
        data_dir = tmp_path / dirname
        monkeypatch.setenv("HEALTH_DATA_DIR", str(data_dir))
        import health_db
        monkeypatch.setattr(health_db, "DB_PATH", data_dir / "data" / "health.db")
        modules = []
        for name in ("belief_contract", "longitudinal_analysis"):
            spec = importlib.util.spec_from_file_location(name, REPO / f"{name}.py")
            module = importlib.util.module_from_spec(spec)
            monkeypatch.setitem(sys.modules, name, module)
            spec.loader.exec_module(module)
            modules.append(module)
        return data_dir, modules[0], modules[1]

    return load


@pytest.mark.parametrize("dirname,owner", [
    ("tenant_alpha", False),
    ("health", True),
    ("health_clone", True),  # is_owner_data also recognizes owner-derived clones.
])
def test_artifact_paths(load_tenant, dirname, owner):
    data_dir, belief, longitudinal = load_tenant(dirname)
    root = REPO if owner else data_dir
    for symbol, relative_path in ARTIFACTS.items():
        path = getattr(longitudinal, symbol)
        assert path == root / relative_path
        if not owner:
            assert path.is_relative_to(data_dir)
    assert belief.GATE_RUN_RECEIPT == root / "logs/gate_run_receipt.json"
    assert longitudinal.GATE_RUN_RECEIPT == belief.GATE_RUN_RECEIPT
    assert longitudinal.run.__defaults__[0] == longitudinal.DEFAULT_OUT
    assert longitudinal.DEFAULT_OUT.with_suffix(".json") == root / "outputs/longitudinal_analysis.json"
    for symbol in ("MC_GAP_ARTIFACT", "PASSSET_ARTIFACT", "GATE_RUN_RECEIPT"):
        path = getattr(longitudinal, symbol)
        assert longitudinal._dryrun_path(path) == root / "logs/dryrun" / path.name
    assert not data_dir.exists(), "Resolving paths must not create data directories"


def test_receipt_writer_and_reader_use_tenant_default(load_tenant, monkeypatch):
    data_dir, belief, longitudinal = load_tenant("tenant_alpha")
    monkeypatch.setattr(belief, "_latest_row", lambda conn: None)
    receipt = data_dir / "logs/gate_run_receipt.json"
    assert longitudinal.GATE_RUN_RECEIPT == belief.GATE_RUN_RECEIPT == receipt
    assert longitudinal.DRYRUN_DIR == data_dir / "logs/dryrun"
    assert longitudinal._write_gate_run_receipt(
        {}, False, error="synthetic failure", run_id="synthetic-run"
    )
    before = receipt.read_bytes()
    result = belief.read_belief()
    assert result["run_failed"] is True
    assert result["receipt_run_id"] == "synthetic-run"
    assert result["error"] == "synthetic failure"

    assert longitudinal._write_gate_run_receipt({}, False, dry_run=True, run_id="dry-run")
    assert receipt.read_bytes() == before
    dry_receipt = data_dir / "logs/dryrun/gate_run_receipt.json"
    assert json.loads(dry_receipt.read_text())["run_id"] == "dry-run"

    other_dir, other_belief, _ = load_tenant("tenant_beta")
    monkeypatch.setattr(other_belief, "_latest_row", lambda conn: None)
    assert other_belief.GATE_RUN_RECEIPT == other_dir / "logs/gate_run_receipt.json"
    other_result = other_belief.read_belief()
    assert other_result["receipt_run_id"] is None
    assert other_result["run_failed"] is False


def test_run_locks_are_independent_between_tenants(load_tenant):
    first_dir, _, first = load_tenant("tenant_alpha")
    second_dir, _, second = load_tenant("tenant_beta")
    assert first.RUN_LOCK == first_dir / "logs/gate_run.lock"
    assert second.RUN_LOCK == second_dir / "logs/gate_run.lock"
    assert first.RUN_LOCK != second.RUN_LOCK
    first_lock = first._acquire_run_lock(dry_run=False)
    try:
        with pytest.raises(first.RunLockBusy):
            first._acquire_run_lock(dry_run=False)
        second_lock = second._acquire_run_lock(dry_run=False)
        try:
            assert second_lock is not None
        finally:
            second._release_run_lock(second_lock)
    finally:
        first._release_run_lock(first_lock)


def test_history_backup_and_atomic_write_stay_in_tenant(load_tenant):
    data_dir, _, longitudinal = load_tenant("tenant_alpha")
    history = longitudinal.PASSSET_ARTIFACT
    assert history == data_dir / "logs/gate_passset_history.json"
    history.parent.mkdir(parents=True)
    longitudinal._atomic_write_json(history, [])
    assert json.loads((data_dir / "logs/gate_passset_history.json").read_text()) == []
    assert not list(history.parent.glob("gate_passset_history.json.tmp*"))
    backup = longitudinal._free_backup_path(history, "synthetic-stamp")
    assert backup == data_dir / "logs/gate_passset_history.corrupt-synthetic-stamp.json"
