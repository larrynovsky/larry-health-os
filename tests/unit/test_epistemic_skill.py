"""tests/unit/test_epistemic_skill.py — контракт epistemic_skill + дефолт-ON.

epistemic_skill влияет на ЖИВЫЕ конституции и синтез консилиума (с дефолтом ON).
Тесты страхуют: loader-контракт (текст + детерминированная версия, fail-fast при
отсутствии файла) и поведение флага (по умолчанию включён; EPISTEMIC_DISCIPLINE
из off-набора выключает). Хелперы тянут health_db → на не-Studio self-skip.
"""
from __future__ import annotations

import pytest

from epistemic_skill.loader import load_part, load_skill

pytestmark = pytest.mark.unit


def test_load_skill_text_and_version():
    sk = load_skill()
    assert "дисциплина" in sk.text.lower()
    assert sk.version.startswith("epi_")
    assert len(sk.version) == len("epi_") + 12


def test_load_part_coordinator():
    sk = load_part("coordinator.txt")
    assert "ДИСЦИПЛИНА СИНТЕЗА" in sk.text
    assert sk.version.startswith("epi_")


def test_version_deterministic_and_content_sensitive():
    assert load_skill().version == load_skill().version
    assert load_part("discipline.txt").version != load_part("coordinator.txt").version


def test_missing_file_fail_fast():
    with pytest.raises(RuntimeError):
        load_part("nope_does_not_exist.txt")


def _maybe_import(modname):
    try:
        return __import__(modname)
    except RuntimeError as e:  # health_db single-primary/path guard вне Studio
        pytest.skip(f"{modname}: guard вне Studio ({str(e)[:50]})")


def test_constitution_helper_default_on_and_off(monkeypatch):
    g = _maybe_import("generate_constitutions")
    monkeypatch.delenv("EPISTEMIC_DISCIPLINE", raising=False)
    assert g._load_epistemic() != "", "по умолчанию дисциплина должна быть ВКЛ"
    for off in ("off", "0", "false", "no"):
        monkeypatch.setenv("EPISTEMIC_DISCIPLINE", off)
        assert g._load_epistemic() == "", f"значение {off!r} должно выключать"


def test_coordinator_helper_default_on_and_off(monkeypatch):
    w = _maybe_import("wellally_consult")
    monkeypatch.delenv("EPISTEMIC_DISCIPLINE", raising=False)
    assert w._load_epistemic_coord() != "", "по умолчанию дисциплина должна быть ВКЛ"
    monkeypatch.setenv("EPISTEMIC_DISCIPLINE", "off")
    assert w._load_epistemic_coord() == ""


def test_diff_prompt_and_version_stamp_carry_discipline(monkeypatch):
    """27.09: промпт «Что изменилось» тоже пишет утверждения о данных — дисциплина едет и туда;
    версия дисциплины штампуется в constitutions.source_version. Мутация: убрать → краснеет."""
    import generate_constitutions as gc
    from epistemic_skill.loader import load_skill
    monkeypatch.setenv("EPISTEMIC_DISCIPLINE", "on")
    dom = next(iter(gc.DOMAINS))
    assert load_skill().text[:80] in gc._build_diff_prompt(dom, "старое", "новое")
    assert gc._epistemic_version() == load_skill().version
    monkeypatch.setenv("EPISTEMIC_DISCIPLINE", "off")
    assert gc._epistemic_version() == "off"
