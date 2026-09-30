"""Контракт единого источника моделей (audit 2026-06-17, hai_core.get_model).
Ловит: тихий дефолт для неизвестной роли; рассинхрон значений; отказ DB-override."""
import pytest
import hai_core


def test_defaults_are_current_literals():
    assert hai_core.MODEL_DEFAULTS["sonnet"] == "claude-sonnet-4-6"
    assert hai_core.MODEL_DEFAULTS["haiku"] == "claude-haiku-4-5"
    assert hai_core.MODEL_DEFAULTS["haiku_pinned"] == "claude-haiku-4-5-20251001"
    assert hai_core.MODEL_DEFAULTS["opus"] == "claude-opus-4-7"


def test_get_model_uses_default_when_no_override(monkeypatch):
    monkeypatch.setattr(hai_core.db, "get_config", lambda k: None)
    assert hai_core.get_model("sonnet") == "claude-sonnet-4-6"
    assert hai_core.get_model("haiku") == "claude-haiku-4-5"


def test_get_model_db_override_wins(monkeypatch):
    monkeypatch.setattr(hai_core.db, "get_config", lambda k: "claude-test-override")
    assert hai_core.get_model("sonnet") == "claude-test-override"


def test_unknown_role_raises_not_silent(monkeypatch):
    monkeypatch.setattr(hai_core.db, "get_config", lambda k: None)
    with pytest.raises(KeyError):
        hai_core.get_model("does-not-exist")


def test_db_unavailable_falls_back_and_logs(monkeypatch, caplog):
    """БД недоступна (get_config бросает) → код-дефолт + log.warning (не тихо).
    Ловит: тихий неверный дефолт при отказе БД (audit 2026-06-17)."""
    import logging

    def _boom(_k):
        raise RuntimeError("db down")
    monkeypatch.setattr(hai_core.db, "get_config", _boom)

    with caplog.at_level(logging.WARNING):
        result = hai_core.get_model("sonnet")

    assert result == "claude-sonnet-4-6"  # код-дефолт MODEL_DEFAULTS
    assert "get_model" in caplog.text and ("недоступен" in caplog.text or "get_config" in caplog.text), \
        "fallback при отказе БД должен писать предупреждение, а не молчать"
