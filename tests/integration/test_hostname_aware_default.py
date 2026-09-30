"""
T-P1-Б — Hostname-aware default for health_db._HEALTH_DIR.

Источник: USE_CASES.md UC-I-07 (single-primary) + #57 (BUG-SPLIT-BRAIN-DEFAULT).

Проверяет:
- На Studio (`socket.gethostname()` == `_PRIMARY_HOST`) → `_resolve_health_dir()`
  возвращает `~/health` без необходимости env var.
- На не-Studio → fallback на iCloud (где `_is_primary()` сделает read-only).
- Env var `HEALTH_DATA_DIR` остаётся override независимо от hostname.

Контракт после рефакторинга (коммит 780ee6a/6330bd4):
- `_resolve_health_dir() -> str` (раньше был `-> Path`).
- `_PRIMARY_HOST = _PRIMARY` — single string (раньше был tuple
  `STUDIO_HOSTNAMES` с двумя вариантами). Поддержки короткого `Studio`
  больше нет — на этой машине hostname всегда полный.
- Path-обёртка вынесена в module-level: `_HEALTH_DIR = Path(_resolve_health_dir())`.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import health_db

import infra_config as _ic
_PRIMARY = _ic.PRIMARY_HOST  # основная машина установки, не литерал владельца

pytestmark = pytest.mark.integration


def test_studio_hostname_resolves_to_local_path(monkeypatch):
    """На основной машине без env var → ~/health (как строка)."""
    monkeypatch.delenv("HEALTH_DATA_DIR", raising=False)
    # Дефолт-fallback = НЕ-мультитенант режим. Под HEALTH_MULTITENANT=1 гард
    # раняет (см. test_multitenant_guard) — здесь тестируем legacy default.
    monkeypatch.delenv("HEALTH_MULTITENANT", raising=False)
    with patch("socket.gethostname", return_value=_PRIMARY):
        result = health_db._resolve_health_dir()
    assert Path(result) == Path.home() / "health", \
        f"основная машина должна дать local-path, получили {result!r}"


def test_resolve_returns_str_not_path(monkeypatch):
    """Контракт: возврат типа str (после рефакторинга 780ee6a/6330bd4).

    Защита от регрессии обратно к Path — если меняем контракт, надо менять
    и module-level `_HEALTH_DIR = Path(_resolve_health_dir())`.
    """
    monkeypatch.delenv("HEALTH_DATA_DIR", raising=False)
    # Дефолт-fallback = НЕ-мультитенант режим. Под HEALTH_MULTITENANT=1 гард
    # раняет (см. test_multitenant_guard) — здесь тестируем legacy default.
    monkeypatch.delenv("HEALTH_MULTITENANT", raising=False)
    with patch("socket.gethostname", return_value=_PRIMARY):
        result = health_db._resolve_health_dir()
    assert isinstance(result, str), \
        f"Ожидался str, получили {type(result).__name__} — контракт сломан"


def test_non_studio_hostname_falls_back_to_icloud(monkeypatch):
    """MacBook (или любой не-Studio) → fallback на облачную реплику установки.

    До 28.09 проверялось на НАСТОЯЩЕМ облаке владельца (метка owner_data, private/infra.yaml) —
    зелёный был причинён окружением машины (§20). Тесты теперь уводят облако в каталог прогона
    (tests/conftest.py, нить icloud-test-guard), поэтому реплика подставная: судится ПРАВИЛО
    «не основная → облачная реплика», а не то, где у владельца лежит iCloud."""
    fake = "/tmp/fake_home/Library/Mobile Documents/com~apple~CloudDocs/health"
    monkeypatch.setattr(health_db, "_ICLOUD_DEFAULT", fake)
    monkeypatch.delenv("HEALTH_DATA_DIR", raising=False)
    # Дефолт-fallback = НЕ-мультитенант режим. Под HEALTH_MULTITENANT=1 гард
    # раняет (см. test_multitenant_guard) — здесь тестируем legacy default.
    monkeypatch.delenv("HEALTH_MULTITENANT", raising=False)
    with patch("socket.gethostname", return_value="zz-MacBook-Pro.local"):
        result = health_db._resolve_health_dir()
    assert result == fake, f"Не-Studio должен дать облачную реплику, получили {result!r}"


def test_env_var_override_wins_over_hostname(monkeypatch):
    """HEALTH_DATA_DIR override работает на любой машине."""
    monkeypatch.setenv("HEALTH_DATA_DIR", "/custom/override/path")
    with patch("socket.gethostname", return_value="any-host"):
        result = health_db._resolve_health_dir()
    assert Path(result) == Path("/custom/override/path")


def test_env_var_override_works_on_studio_too(monkeypatch):
    """Env var побеждает hostname-detection даже на Studio."""
    monkeypatch.setenv("HEALTH_DATA_DIR", "/tmp/test_override")
    with patch("socket.gethostname", return_value=_PRIMARY):
        result = health_db._resolve_health_dir()
    assert Path(result) == Path("/tmp/test_override")


def test_primary_host_constant_is_studio_local():
    """_PRIMARY_HOST single-source-of-truth: основной машины (private/infra.yaml).

    Если изменится — обновить и этот тест, и `_resolve_health_dir`.
    """
    assert health_db._PRIMARY_HOST == _PRIMARY, \
        f"_PRIMARY_HOST должен быть основной машины (private/infra.yaml), получили {health_db._PRIMARY_HOST!r}"
