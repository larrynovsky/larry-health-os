"""Датчик планки моделей (hai_core.model_for / ROLE_MODELS).

Консультации (суждение врача + синтез) — opus; чек-ин — haiku. Меняется одной
строкой в ROLE_MODELS. Плюс source-датчик: движки консультаций не откатились на
прямой get_model('haiku')."""
from pathlib import Path

import pytest

import hai_core


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    # без DB-override → ROLE_MODELS defaults
    monkeypatch.setattr(hai_core.db, "get_config", lambda k: None)


def test_consultation_roles_are_opus():
    opus = hai_core.MODEL_DEFAULTS["opus"]
    for role in ("consilium_specialist", "consilium_coordinator",
                 "consult_specialist", "consult_coordinator", "lifestyle"):
        assert hai_core.model_for(role) == opus, role


def test_checkin_stays_haiku():
    assert hai_core.model_for("checkin") == hai_core.MODEL_DEFAULTS["haiku"]


def test_unknown_role_raises():
    with pytest.raises(KeyError):
        hai_core.model_for("nonexistent_role")


def test_engines_use_model_for_not_direct_haiku():
    root = Path(__file__).resolve().parents[2]
    for fn, role in [("monthly_consilium.py", "consilium_specialist"),
                     ("wellally_consult.py", "consult_specialist"),
                     ("lifestyle_agents.py", "lifestyle")]:
        src = (root / fn).read_text(encoding="utf-8")
        assert "model_for(" in src, f"{fn} не использует model_for"
        assert 'get_model("haiku")' not in src, f"{fn} откатился на get_model('haiku')"
