"""
UC-J-01 — Diff кода → proposal UC, без автозаписи в USE_CASES.md.

Источник: USE_CASES.md §4.J → UC-J-01.
Реализация: `propose_uc.py`.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

import propose_uc as pu


def test_classify_changes_groups_known_files():
    files = ["gp_agent.py", "wellally_consult.py", "telegram_bot.py"]
    by_group = pu.classify_changes(files)
    assert "B" in by_group  # gp_agent
    assert "H" in by_group  # wellally_consult
    assert "I" in by_group  # telegram_bot


def test_classify_changes_ignores_tests_and_docs():
    files = ["tests/unit/test_x.py", "USE_CASES.md", "ROADMAP.md",
             "pytest.ini", "deploy.sh"]
    by_group = pu.classify_changes(files)
    # Все эти файлы — не B-скоуп. Не должны быть ни в одной группе.
    assert all(g == "unknown" or not files_in
                for g, files_in in by_group.items() if files_in is not files)
    # Известные: ничего не классифицировано
    relevant = {g: fs for g, fs in by_group.items() if g != "unknown"}
    assert relevant == {}, f"тесты/docs не должны быть в B-скоупе: {relevant}"


def test_classify_changes_unknown_files_in_unknown_bucket():
    files = ["random_new_file.py"]
    by_group = pu.classify_changes(files)
    assert by_group.get("unknown") == ["random_new_file.py"]


def test_build_proposal_returns_none_for_no_changes(monkeypatch):
    """Если git diff пустой — proposal=None."""
    monkeypatch.setattr(pu, "get_diff_files", lambda commit="HEAD": [])
    assert pu.build_proposal() is None


def test_build_proposal_returns_none_for_irrelevant_changes(monkeypatch):
    """Если изменены только tests/ или docs — proposal=None."""
    monkeypatch.setattr(pu, "get_diff_files",
                         lambda commit="HEAD": ["tests/unit/test_x.py", "README.md"])
    assert pu.build_proposal() is None


def test_build_proposal_returns_data_for_b_scope_changes(monkeypatch):
    """Изменён gp_agent.py → proposal с группой B."""
    monkeypatch.setattr(pu, "get_diff_files",
                         lambda commit="HEAD": ["gp_agent.py"])
    monkeypatch.setattr(pu, "_git", lambda *a: "abc1234567890" if "rev-parse" in a else "test commit")
    monkeypatch.setattr(pu, "get_diff_text", lambda commit="HEAD", max_lines=200: "stat")

    p = pu.build_proposal()
    assert p is not None
    assert "B" in p["groups_affected"]
    assert "gp_agent.py" in p["files"]


def test_write_proposal_does_not_modify_use_cases_md(tmp_path, monkeypatch):
    """
    Главный инвариант UC-J-01: пишем только в proposed/, в USE_CASES.md
    не лезем.
    """
    monkeypatch.setattr(pu, "PROPOSED_DIR", tmp_path / "proposed")
    (tmp_path / "proposed").mkdir()

    proposal = {
        "commit": "abc12345",
        "message": "test",
        "timestamp": "2026-05-08T10:00:00",
        "files": ["gp_agent.py"],
        "groups_affected": ["B"],
        "by_group": {"B": ["gp_agent.py"]},
        "stat": "1 file changed",
    }
    path = pu.write_proposal_md(proposal)

    assert path.exists()
    assert path.parent == tmp_path / "proposed"
    content = path.read_text()
    assert "Группа B" in content
    assert "gp_agent.py" in content
    assert "USE_CASES.md" in content  # упоминается в инструкции «что делать»
    assert "автоматически" in content.lower()  # фраза «не записывается автоматически»


def test_proposal_md_contains_action_instructions(tmp_path, monkeypatch):
    """Proposal должен явно говорить как с ним работать (UC-J-01 §Что делать)."""
    monkeypatch.setattr(pu, "PROPOSED_DIR", tmp_path / "proposed")
    (tmp_path / "proposed").mkdir()
    proposal = {
        "commit": "abc", "message": "x", "timestamp": "2026-01-01T00:00:00",
        "files": ["telegram_bot.py"], "groups_affected": ["I"],
        "by_group": {"I": ["telegram_bot.py"]}, "stat": "",
    }
    path = pu.write_proposal_md(proposal)
    content = path.read_text()
    assert "## Что делать" in content
    assert "Ревью" in content
