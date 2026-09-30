"""Тест fail-closed guard в import_oura.get_token (2026-07-03).

Тенант-каталог (HEALTH_DATA_DIR=…/health_partner) БЕЗ HEALTH_SECRETS_DIR → отказ,
иначе взяли бы токен владельца (~/.health_secrets) → кросс-тенант утечка.
Owner (…/health) с дефолтными секретами — работает.
"""
import importlib
import os
import pytest


def _reload_import_oura(monkeypatch, data_dir, secrets_dir=None):
    monkeypatch.setenv("HEALTH_DATA_DIR", data_dir)
    if secrets_dir is None:
        monkeypatch.delenv("HEALTH_SECRETS_DIR", raising=False)
    else:
        monkeypatch.setenv("HEALTH_SECRETS_DIR", secrets_dir)
    import import_oura
    return importlib.reload(import_oura)


def test_tenant_without_secrets_refuses(tmp_path, monkeypatch):
    io = _reload_import_oura(monkeypatch, str(tmp_path / "health_partner"))
    with pytest.raises(RuntimeError, match="кросс-тенант"):
        io.get_token()


def test_tenant_with_secrets_ok(tmp_path, monkeypatch):
    sd = tmp_path / ".health_secrets_partner"
    sd.mkdir()
    (sd / "oura_token").write_text("TESTTOKEN")
    io = _reload_import_oura(monkeypatch, str(tmp_path / "health_partner"), str(sd))
    assert io.get_token() == "TESTTOKEN"


def test_owner_default_secrets_ok(tmp_path, monkeypatch):
    # owner: каталог .../health, дефолтные секреты — guard не срабатывает
    home_sec = tmp_path / ".health_secrets"
    home_sec.mkdir()
    (home_sec / "oura_token").write_text("OWNERTOKEN")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    io = _reload_import_oura(monkeypatch, str(tmp_path / "health"))
    assert io.get_token() == "OWNERTOKEN"
