"""
UC-J-03 — Каждый confirmed UC связан с modules / data / tests / oracle / risk.

Источник: USE_CASES.md §4.J → UC-J-03.
Реализация: `validate_uc_index.py` + `uc_index.yaml`.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_validate_uc_index_returns_zero():
    import validate_uc_index as vi
    code, errors = vi.validate()
    assert code == 0, f"validation errors: {errors}"


def test_uc_index_yaml_loads():
    import validate_uc_index as vi
    data = vi._load_yaml_simple(vi.INDEX_FILE)
    assert data["ucs"], "uc_index.yaml пуст"


def test_first_pack_ucs_in_index():
    """Все 10 UC из первого пакета должны быть в индексе."""
    import validate_uc_index as vi
    data = vi._load_yaml_simple(vi.INDEX_FILE)
    expected = {"UC-I-01", "UC-I-02", "UC-A-04", "UC-A-03",
                "UC-B-03", "UC-B-04", "UC-D-04", "UC-H-02",
                "UC-J-01", "UC-J-02", "UC-K-01"}
    missing = expected - set(data["ucs"].keys())
    assert not missing, f"в uc_index.yaml нет: {missing}"


def test_each_confirmed_uc_has_required_fields():
    import validate_uc_index as vi
    data = vi._load_yaml_simple(vi.INDEX_FILE)
    for uc_id, rec in data["ucs"].items():
        if rec.get("confirmation") == "confirmed":
            for field in vi.REQUIRED_FIELDS:
                assert field in rec, f"{uc_id} нет {field}"


def test_validator_catches_missing_test_file(tmp_path, monkeypatch):
    """Если в записи указан test-файл которого нет — validator падает."""
    fake_index = tmp_path / "uc_index.yaml"
    fake_index.write_text(
        'version: "0.1"\n'
        'ucs:\n'
        '  UC-X-99:\n'
        '    status: implemented\n'
        '    confirmation: confirmed\n'
        '    priority: P0\n'
        '    testability: check\n'
        '    tests:\n'
        '      - tests/unit/non_existing.py\n',
        encoding="utf-8",
    )
    import validate_uc_index as vi
    monkeypatch.setattr(vi, "INDEX_FILE", fake_index)
    monkeypatch.setattr(vi, "USE_CASES", tmp_path / "no_use_cases")  # пусто
    code, errors = vi.validate()
    assert code == 1
    assert any("test file missing" in e for e in errors)
