"""Catalogue isolation without importing the database or reading real homes."""
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("data_name,owner", [
    ("health_example", False),
    ("health_second", False),
    ("health", True),
    ("health_staging", True),
])
def test_catalogue_path_and_reader(monkeypatch, tmp_path, data_name, owner):
    home = tmp_path / "fake_home"
    data_dir = tmp_path / data_name
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HEALTH_DATA_DIR", str(data_dir))
    monkeypatch.syspath_prepend(str(ROOT))

    # The database dependency is a seam, not a connection to any data directory.
    db = ModuleType("health_db")
    db._resolve_health_dir = lambda: os.environ["HEALTH_DATA_DIR"]
    monkeypatch.setitem(sys.modules, "health_db", db)
    spec = importlib.util.spec_from_file_location(
        "assessment_scheduler", ROOT / "assessment_scheduler.py")
    scheduler = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "assessment_scheduler", scheduler)
    spec.loader.exec_module(scheduler)

    legacy = home / "health/data/instruments"
    tenant = data_dir / "data/instruments"
    for directory, identifier in [(legacy, "synthetic_owner"),
                                  (tenant, "synthetic_tenant")]:
        directory.mkdir(parents=True)
        (directory / "example.json").write_text(json.dumps({"id": identifier}))

    expected = legacy if owner else tenant
    assert scheduler.INSTRUMENTS_DIR == expected
    assert scheduler.tenant_data_path("instruments") == expected
    if not owner:
        assert expected.is_relative_to(data_dir)
    assert scheduler._load_instruments() == [
        {"id": "synthetic_owner" if owner else "synthetic_tenant"}]

    # A missing tenant catalogue must not silently fall back to the owner.
    (expected / "example.json").unlink()
    expected.rmdir()
    assert scheduler._load_instruments() == []
