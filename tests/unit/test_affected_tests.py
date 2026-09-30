"""Характеризация affected_tests.select — отбор тестов по изменённым файлам.

Главный кейс — воспроизведение инцидента 2026-08-03: правка BLUEPRINT.md сняла
ссылку на git-канон, `test_doc_git_model_invariants` покраснел, но в прогон не
попал и уехал в прод. Отбор обязан его называть.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import affected_tests as at   # noqa: E402


def test_doc_edit_selects_doc_guard():
    """Инцидент 2026-08-03: правка .md обязана выбрать сторожа, который его ЧИТАЕТ."""
    hits, refusal = at.select(["CLAUDE.md"])
    assert refusal is None
    assert any("test_doc_git_model_invariants" in h for h in hits), hits


def test_module_edit_selects_its_own_test():
    hits, refusal = at.select(["secret_guard.py"])
    assert refusal is None
    assert any("test_secret_guard" in h for h in hits), hits


def test_importer_depth_one_included():
    """Тест модуля-импортёра тоже под подозрением: правка зависимости ломает его."""
    hits, _ = at.select(["belief_contract.py"])
    assert len(hits) > 1, hits


def test_wide_edit_refuses_instead_of_truncating():
    """Широкая правка → ОТКАЗ с причиной, а не молча урезанный список.

    Тихое усечение читалось бы как «проверено всё» — тот же класс, что fp[:3]
    в preflight (исправлен в тот же день).
    """
    hits, refusal = at.select(["health_db.py"])
    assert refusal and "полный набор" in refusal
    assert len(hits) > at.SELECTION_CAP


def test_nothing_changed_selects_nothing():
    hits, refusal = at.select([])
    assert hits == [] and refusal is None


def test_negative_control_name_signal_is_load_bearing(monkeypatch):
    """НЕГАТИВНЫЙ КОНТРОЛЬ: убрать признак «упоминает имя файла» — и доковый
    сторож перестаёт выбираться. Значит именно этот признак закрывает инцидент,
    а не импортный, который тут ни при чём."""
    real = at._module_imports
    monkeypatch.setattr(at, "_module_imports", lambda p: set())   # глушим импортный признак
    hits, _ = at.select(["CLAUDE.md"])
    assert any("test_doc_git_model_invariants" in h for h in hits), \
        "имя файла в исходнике теста больше не признак — инцидент 03.08 снова пройдёт"
    monkeypatch.setattr(at, "_module_imports", real)
    # и обратно: без имени в тексте (несуществующий файл) — сторож не выбирается
    hits2, _ = at.select(["nonexistent_zzz.md"])
    assert not any("test_doc_git_model_invariants" in h for h in hits2), hits2


def test_unparsable_file_is_reported_not_silent(tmp_path, capsys):
    """Неразобранный файл делает связи невидимыми — молчать нельзя (§20)."""
    bad = tmp_path / "broken.py"
    bad.write_text("def (:\n")
    assert at._module_imports(bad) == set()
    assert "не разобран" in capsys.readouterr().err


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
