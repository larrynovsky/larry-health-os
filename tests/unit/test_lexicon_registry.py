"""tests/unit/test_lexicon_registry.py — датчик §9 «лексикон-в-коде» (правило #5: clean+injected).

Guard → два acceptance-теста обязательны + границы + регресс на пойманный баг (AnnAssign).
"""
from __future__ import annotations
from pathlib import Path
import pytest

pytestmark = pytest.mark.unit

import lexicon_registry as L


def test_real_repo_clean():
    """clean → 0: реальный репо покрыт реестром (все лексиконы классифицированы).
    Если кто-то добавит незарегистрированный лексикон — этот тест падёт (живой guard)."""
    root = Path(L.__file__).resolve().parent
    assert L.collect_lexicon_findings(root) == []


def test_injected_unregistered_flagged():
    """injected → 1: новый лексикон вне реестра → находка + его имя."""
    f = L.audit_lexicons({"evil_module.SECRET_TERMS": {"count": 3, "file": "evil.py", "line": 1}})
    assert f and any("evil_module.SECRET_TERMS" in x for x in f)


def test_legacy_growth_flagged(monkeypatch):
    """Ратчет-по-вхождениям (мой точный промах — дописал в легаси): рост count → находка."""
    monkeypatch.setitem(L.REGISTERED, "m.LEGACY_TERMS", {"verdict": "legacy", "count": 5})
    grew = L.audit_lexicons({"m.LEGACY_TERMS": {"count": 6, "file": "m.py", "line": 1}})
    assert any("ВЫРОС" in x for x in grew)
    same = L.audit_lexicons({"m.LEGACY_TERMS": {"count": 5, "file": "m.py", "line": 1}})
    assert not any("ВЫРОС" in x for x in same)


def test_census_stale_registry_flagged():
    """Census-симметрия: ключ реестра без реальной константы → реестр протух."""
    assert any("протух" in x for x in L.audit_lexicons({}))


def test_scan_catches_annotated_assignment(tmp_path):
    """Регресс (пойман при первом скане): аннотированная константа (X_TERMS: tuple = ...) —
    это AnnAssign, ранний сканер её пропускал = ложный негатив на главную цель."""
    (tmp_path / "mod.py").write_text('FOO_TERMS: tuple = ("a", "b", "c")\n', encoding="utf-8")
    sc = L._scan_lexicons(tmp_path)
    assert "mod.FOO_TERMS" in sc and sc["mod.FOO_TERMS"]["count"] == 3


def test_scan_ignores_non_lexicon_names(tmp_path):
    """FP-контроль: коллекция строк с НЕ-лексиконным именем (нет суффикса) не ловится."""
    (tmp_path / "mod.py").write_text('PATHS = ("/a", "/b")\nX = ["one", "two"]\n', encoding="utf-8")
    assert L._scan_lexicons(tmp_path) == {}


def test_scan_ignores_tests_and_docs(tmp_path):
    """Скан не лезет в tests/ и docs/ (там фикстуры/примеры, не боевой код)."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "t.py").write_text('BAD_TERMS = ("a", "b")\n', encoding="utf-8")
    assert L._scan_lexicons(tmp_path) == {}
