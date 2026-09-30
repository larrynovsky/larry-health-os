"""Регресс (2026-07-01, инцидент): telegram-нотификаторы per-tenant.

_notify_specialist (и родня) хардкодили ~/.health_secrets → алёрт партнёра уходил
владельцу. Тест: при HEALTH_SECRETS_DIR отправка резолвится в СЕКРЕТЫ ТЕНАНТА,
не в ~/.health_secrets. Реальный telegram не дёргается (urlopen замокан)."""
import urllib.request

import pytest


def test_notify_specialist_uses_tenant_secrets(tmp_path, monkeypatch):
    import hai_hypotheses
    sec = tmp_path / "sec"
    sec.mkdir()
    (sec / "telegram_token").write_text("TOK_TENANT")
    (sec / "telegram_chat_id").write_text("CHAT_TENANT")
    monkeypatch.setenv("HEALTH_SECRETS_DIR", str(sec))

    captured = {}

    def fake_urlopen(url, data=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        class _R:  # noqa: E306
            def read(self_):  # pragma: no cover
                return b""
        return _R()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    hai_hypotheses._notify_specialist("тест")

    assert "TOK_TENANT" in captured["url"], "токен взят не из HEALTH_SECRETS_DIR"
    assert b"CHAT_TENANT" in captured["data"], "chat_id взят не из HEALTH_SECRETS_DIR"


@pytest.mark.owner_env  # предполагает канонический HEALTH_DATA_DIR (иначе tenant-гард raise'ит); nightly на каноне
def test_notify_specialist_defaults_to_home_when_no_env(tmp_path, monkeypatch):
    """Без HEALTH_SECRETS_DIR — дефолт ~/.health_secrets (поведение владельца не меняется)."""
    import hai_hypotheses
    monkeypatch.delenv("HEALTH_SECRETS_DIR", raising=False)
    from pathlib import Path
    # подменяем home на tmp, чтобы не читать реальные секреты
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    sec = tmp_path / ".health_secrets"
    sec.mkdir()
    (sec / "telegram_token").write_text("TOK_HOME")
    (sec / "telegram_chat_id").write_text("CHAT_HOME")
    captured = {}
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda url, data=None, timeout=None: captured.update(url=url, data=data))
    hai_hypotheses._notify_specialist("тест")
    assert "TOK_HOME" in captured["url"]
