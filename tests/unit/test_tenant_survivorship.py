"""Tenant inputs and wrapper checkout selection; no database or API calls."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("data_name,owner", [
    ("health_example", False),
    ("health", True),
    ("health_staging", True),
])
@pytest.mark.parametrize("analyzer_dry_run", [False, True])
def test_analyzer_inputs(monkeypatch, tmp_path, data_name, owner, analyzer_dry_run):
    home = tmp_path / "fake_home"
    data_dir = tmp_path / data_name
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HEALTH_DATA_DIR", str(data_dir))
    monkeypatch.syspath_prepend(str(ROOT))

    db = ModuleType("health_db")
    db._resolve_health_dir = lambda: os.environ["HEALTH_DATA_DIR"]
    db.save_agent_report = Mock()
    monkeypatch.setitem(sys.modules, "health_db", db)
    modules = {}
    for name in ("assessment_scheduler", "survivorship_analyzer"):
        spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        modules[name] = module
    analyzer = modules["survivorship_analyzer"]

    legacy = home / "health/data"
    tenant = data_dir / "data"
    expected = legacy if owner else tenant
    for directory, identifier in [(legacy, "synthetic_owner"),
                                  (tenant, "synthetic_tenant")]:
        (directory / "instruments").mkdir(parents=True)
        (directory / "instruments/example.json").write_text(
            json.dumps({"id": identifier}))
        # Conflicting flags make reading the wrong person's config observable.
        flag = analyzer_dry_run if directory == expected else not analyzer_dry_run
        (directory / "survivorship_config.yaml").write_text(
            f"pilot_flags:\n  analyzer_dry_run: {str(flag).lower()}\n")

    assert analyzer.INSTRUMENTS_DIR == expected / "instruments"
    assert analyzer.CONFIG == expected / "survivorship_config.yaml"
    assert analyzer.INSTRUMENTS_DIR == modules["assessment_scheduler"].INSTRUMENTS_DIR
    assert analyzer.tenant_data_path is modules["assessment_scheduler"].tenant_data_path
    if not owner:
        assert analyzer.INSTRUMENTS_DIR.is_relative_to(data_dir)
        assert analyzer.CONFIG.is_relative_to(data_dir)
    identifier = "synthetic_owner" if owner else "synthetic_tenant"
    assert analyzer._load_instruments() == [{"id": identifier}]

    monkeypatch.setattr(analyzer, "_global_context_findings", lambda end: [])
    monkeypatch.setattr(analyzer, "_analyse_instrument", lambda instrument, end: [])
    assert analyzer.run() == {"per_instrument": {identifier: []}, "global": []}
    assert db.save_agent_report.call_count == (0 if analyzer_dry_run else 1)

    # Missing inputs keep the existing empty/default behavior within this tenant.
    (expected / "instruments/example.json").unlink()
    (expected / "instruments").rmdir()
    analyzer.CONFIG.unlink()
    db.save_agent_report.reset_mock()
    assert analyzer._load_instruments() == []
    assert analyzer.run() == {"per_instrument": {}, "global": []}
    db.save_agent_report.assert_called_once()


def test_pipeline_selects_its_checkout_before_starting_jobs(tmp_path):
    checkout = tmp_path / "checkout with spaces"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    home = tmp_path / "fake_home"
    (home / "health_scripts").mkdir(parents=True)
    data_dir = tmp_path / "health_example"
    source = (ROOT / "scripts/run_survivorship_pipeline.sh").read_text()
    # Execute only the actual prologue, stopping before the first Python job.
    # Neither proposal expiry nor analysis/curation can run in this check.
    prologue = source.split("/opt/homebrew/bin/python3.11", 1)[0]
    script = scripts / "run_survivorship_pipeline.sh"
    script.write_text(prologue + '\nprintf "%s\\n" "$PWD" "$HEALTH_DATA_DIR"\n')
    result = subprocess.run(
        ["/bin/bash", str(script)], cwd=tmp_path,
        env={"HOME": str(home), "HEALTH_DATA_DIR": str(data_dir),
             "PATH": "/usr/bin:/bin"},
        text=True, capture_output=True, check=True,
    )
    cwd, inherited_data_dir = result.stdout.splitlines()
    assert Path(cwd).resolve() == checkout.resolve()
    assert inherited_data_dir == str(data_dir)
