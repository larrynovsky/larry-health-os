"""Песочница получает ФАКТЫ о git, а не git (2026-08-10).

Три следствия одного корня, до сих пор выглядевшие как три разные поломки:
пять doc-тестов падали `git ls-files` rc=128; снимок pass-set писал
`head_sha: "unknown"`; проба `quarantine_exit` читала снимок без sha. Причина
одна — `test_on_studio.sh` синхронизирует дерево БЕЗ `.git`, и правильно
делает: репозиторий в песочнице означал бы, что оттуда можно коммитить.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _isolate(tmp_path, monkeypatch, manifest: dict | None):
    """Отрезаем настоящий git и подкладываем (или нет) манифест."""
    import git_facts
    monkeypatch.setattr(git_facts, "_git", lambda args: None)
    p = tmp_path / ".git_facts.json"
    if manifest is not None:
        p.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(git_facts, "MANIFEST", p)
    return git_facts


def test_real_git_is_preferred(tmp_path, monkeypatch):
    """Негативный контроль: там, где git ЕСТЬ, манифест не участвует.

    Иначе снимок момента синхронизации тихо подменил бы живой ответ — и правка,
    сделанная после синхронизации, стала бы невидимой для сторожей.

    SKIP БЕЗ GIT — И ЭТО НЕ ОТГОВОРКА. Первая редакция этого теста краснела в
    песочнице, то есть повторяла ровно тот дефект, который вся правка лечит:
    красное от СРЕДЫ. Пропуск здесь честен, потому что утверждение теста
    («живой git старше манифеста») в среде без git не имеет смысла — там
    нечему быть старше.
    """
    import git_facts
    monkeypatch.setattr(git_facts, "MANIFEST", tmp_path / "нет-такого.json")
    if git_facts._git(["rev-parse", "--git-dir"]) is None:
        pytest.skip("git недоступен (песочница) — утверждению не на чем держаться")
    assert git_facts.source() == "git"
    assert git_facts.head_sha() != "unknown"
    assert any(f.endswith("git_facts.py") for f in git_facts.tracked("*.py"))


def test_manifest_answers_when_git_is_absent(tmp_path, monkeypatch):
    """⭐ Ровно песочница: git недоступен, манифест отвечает."""
    gf = _isolate(tmp_path, monkeypatch,
                  {"head_sha": "abc1234", "files": ["a.py", "docs/how-to/x.md"]})
    assert gf.source() == "manifest"
    assert gf.head_sha() == "abc1234"
    assert gf.tracked() == ["a.py", "docs/how-to/x.md"]


def test_globs_filter_the_manifest_like_git_does(tmp_path, monkeypatch):
    """Глоб `*.py` у git совпадает на ЛЮБОЙ глубине, у fnmatch — нет.

    Без этой поправки сторож в песочнице увидел бы только корневые файлы и
    доложил бы «нарушений нет» на усечённом периметре — зелёный от пустоты.
    """
    gf = _isolate(tmp_path, monkeypatch,
                  {"head_sha": "abc1234",
                   "files": ["a.py", "pkg/deep/b.py", "docs/how-to/x.md", "c.txt"]})
    assert set(gf.tracked("*.py")) == {"a.py", "pkg/deep/b.py"}
    assert gf.tracked("docs/**/*.md") == ["docs/how-to/x.md"]


def test_no_git_no_manifest_is_named_not_faked(tmp_path, monkeypatch):
    """Нет ни git, ни манифеста — это состояние обязано быть НАЗВАНО."""
    gf = _isolate(tmp_path, monkeypatch, None)
    assert gf.source() == "none"
    assert gf.head_sha() == "unknown"
    assert gf.tracked("*.py") == []


def test_empty_manifest_is_refused_at_write(tmp_path, monkeypatch):
    """Пустой манифест опаснее его отсутствия: сторожа стали бы зелёными от пустоты.

    Поэтому запись ОТКАЗЫВАЕТ, а не пишет тишину. §20: зелёное обязано быть
    вызвано проверкой, а не тем, что проверять оказалось нечего.
    """
    import git_facts
    monkeypatch.setattr(git_facts, "_git", lambda args: None)
    monkeypatch.setattr(git_facts, "MANIFEST", tmp_path / ".git_facts.json")
    with pytest.raises(RuntimeError, match="Пустой манифест|манифест не снят"):
        git_facts.write_manifest()


def test_sync_script_ships_the_manifest():
    """Сторож доставки: манифест бесполезен, если не доехал в песочницу."""
    src = (ROOT / "scripts" / "test_on_studio.sh").read_text(encoding="utf-8")
    assert "write_manifest" in src, "синхронизация не снимает манифест"
    assert ".git_facts.json" in src and "scp" in src, "манифест не доставляется в staging"
