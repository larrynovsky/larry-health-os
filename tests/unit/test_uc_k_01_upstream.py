"""
UC-K-01 — Specialist prompts upstream tracking.

Источник: USE_CASES.md §4.K → UC-K-01.
Реализация: `check_wellally_updates.py`.
Status: `partial` (apply ещё не реализован, но diff-detection работает).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

import check_wellally_updates as cwu


# ── Helpers ──────────────────────────────────────────────────────────────────


def _git_blob_sha(content: bytes) -> str:
    """Эталонный git blob SHA для проверки локальной функции."""
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content).hexdigest()


# ── E (cross-check): SHA-формат ──────────────────────────────────────────────


def test_local_file_sha_matches_git_blob_format(tmp_path):
    f = tmp_path / "test.md"
    content = b"hello world\n"
    f.write_bytes(content)
    expected = _git_blob_sha(content)
    assert cwu.local_file_sha(f) == expected


def test_local_file_sha_empty_file(tmp_path):
    f = tmp_path / "empty.md"
    f.write_bytes(b"")
    expected = _git_blob_sha(b"")
    assert cwu.local_file_sha(f) == expected


# ── E: check_repo с моком GitHub API ────────────────────────────────────────


@pytest.fixture
def patched_state(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(cwu, "STATE_FILE", state_file)
    return state_file


@pytest.fixture
def patched_specs(tmp_path, monkeypatch):
    spec_dir = tmp_path / "specialists"
    spec_dir.mkdir()
    monkeypatch.setattr(cwu, "SPEC_DIR", spec_dir)
    return spec_dir


def _make_gh_responses(monkeypatch, *, commit_sha: str, files: list[dict]):
    """Подменяет _gh_get так чтобы возвращать заданные данные."""
    def fake_gh_get(url: str):
        if "/commits/main" in url or "/commits/master" in url:
            return {"sha": commit_sha,
                    "commit": {"author": {"date": "2026-05-08T10:00:00Z"},
                                "message": "fake commit"}}
        if "/contents/.claude/specialists" in url:
            return files
        return None
    monkeypatch.setattr(cwu, "_gh_get", fake_gh_get)


def test_check_repo_no_change_when_sha_unchanged(patched_state, patched_specs, monkeypatch):
    """Если last_known_sha == latest_sha → new_commits=False."""
    _make_gh_responses(monkeypatch, commit_sha="abc1234", files=[])
    state = {"huifer/WellAlly-health": {"last_commit_sha": "abc1234"}}

    result = cwu.check_repo("huifer", "WellAlly-health", state)
    assert result["new_commits"] is False
    assert result["changed_files"] == []


def test_check_repo_detects_new_commits(patched_state, patched_specs, monkeypatch):
    _make_gh_responses(monkeypatch, commit_sha="def5678",
                        files=[])
    state = {"huifer/WellAlly-health": {"last_commit_sha": "abc1234"}}

    result = cwu.check_repo("huifer", "WellAlly-health", state)
    assert result["new_commits"] is True
    assert result["latest_sha"] == "def5678"


def test_check_repo_detects_new_files(patched_state, patched_specs, monkeypatch):
    """Файл есть на upstream, нет локально → new_files."""
    _make_gh_responses(
        monkeypatch, commit_sha="def5678",
        files=[{"name": "oncology.md", "sha": "remote_sha_123"}],
    )
    # spec_dir пустой
    result = cwu.check_repo("huifer", "WellAlly-health", state={})
    assert "oncology.md" in result["new_files"]


def test_check_repo_detects_changed_files(patched_state, patched_specs, monkeypatch):
    """Локальный SHA != remote SHA → changed_files."""
    local_file = patched_specs / "oncology.md"
    local_file.write_text("local version", encoding="utf-8")

    _make_gh_responses(
        monkeypatch, commit_sha="def5678",
        files=[{"name": "oncology.md", "sha": "remote_different_sha"}],
    )
    result = cwu.check_repo("huifer", "WellAlly-health", state={})
    assert "oncology.md" in result["changed_files"]


def test_check_repo_no_change_when_sha_match(patched_state, patched_specs, monkeypatch):
    """Если local SHA == remote SHA → файл не в changed_files."""
    local_file = patched_specs / "oncology.md"
    content = b"specialist prompt content"
    local_file.write_bytes(content)
    matching_sha = _git_blob_sha(content)

    _make_gh_responses(
        monkeypatch, commit_sha="def5678",
        files=[{"name": "oncology.md", "sha": matching_sha}],
    )
    result = cwu.check_repo("huifer", "WellAlly-health", state={})
    assert "oncology.md" not in result["changed_files"]


# ── B (NOT-Then): apply не происходит ───────────────────────────────────────


def test_check_repo_does_not_modify_local_files(patched_state, patched_specs, monkeypatch):
    """
    Главный NOT-Then UC-K-01: проверка upstream НЕ должна писать в local-файлы.
    Скрипт только обнаруживает diff, не применяет.
    """
    local_file = patched_specs / "oncology.md"
    original_content = b"original local content"
    local_file.write_bytes(original_content)

    _make_gh_responses(
        monkeypatch, commit_sha="def5678",
        files=[{"name": "oncology.md", "sha": "remote_different"},
                {"name": "new_specialist.md", "sha": "remote_xyz"}],
    )

    cwu.check_repo("huifer", "WellAlly-health", state={})

    # Локальный файл не должен быть изменён
    assert local_file.read_bytes() == original_content
    # Новый файл не должен быть создан
    assert not (patched_specs / "new_specialist.md").exists()


def test_404_repo_returns_error(patched_specs, monkeypatch):
    """API 404 → graceful: error в результате, не throw."""
    monkeypatch.setattr(cwu, "_gh_get", lambda url: None)

    result = cwu.check_repo("huifer", "NonExistent", state={})
    assert result["errors"]
    assert result["new_commits"] is False


# ── State-файл ──────────────────────────────────────────────────────────────


def test_load_state_returns_empty_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cwu, "STATE_FILE", tmp_path / "missing.json")
    assert cwu.load_state() == {}


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    sf = tmp_path / "state.json"
    monkeypatch.setattr(cwu, "STATE_FILE", sf)
    cwu.save_state({"x/y": {"last_commit_sha": "abc"}})
    assert cwu.load_state() == {"x/y": {"last_commit_sha": "abc"}}
