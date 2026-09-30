"""R1 (multitenancy Phase 0, 2026-06-29): предохранитель тихого дефолта.

В мультитенантном режиме (HEALTH_MULTITENANT=1) процесс без явного HEALTH_DATA_DIR
обязан падать, а не молча брать каталог дефолтного тенанта — иначе процесс одного
пользователя возьмёт данные другого (см. ADR multitenancy §R1).

Опт-ин: без флага поведение не меняется (single-tenant default сохраняется).
Уровень: consistency. Канонический прогон — на Studio.
"""
import pytest

pytestmark = pytest.mark.consistency


def test_multitenant_flag_requires_explicit_dir(monkeypatch):
    """HEALTH_MULTITENANT=1 + нет HEALTH_DATA_DIR → громкий RuntimeError."""
    import health_db
    monkeypatch.delenv("HEALTH_DATA_DIR", raising=False)
    monkeypatch.setenv("HEALTH_MULTITENANT", "1")
    with pytest.raises(RuntimeError, match="HEALTH_DATA_DIR"):
        health_db._resolve_health_dir()


def test_explicit_dir_wins_even_in_multitenant(monkeypatch):
    """Явный HEALTH_DATA_DIR приоритетнее флага — не падает, возвращает его."""
    import health_db
    monkeypatch.setenv("HEALTH_MULTITENANT", "1")
    monkeypatch.setenv("HEALTH_DATA_DIR", "/tmp/tenant_x")
    assert health_db._resolve_health_dir() == "/tmp/tenant_x"


def test_no_flag_keeps_silent_default(monkeypatch):
    """Без флага (today) — дефолт по hostname сохраняется, без исключения."""
    import health_db
    monkeypatch.delenv("HEALTH_DATA_DIR", raising=False)
    monkeypatch.delenv("HEALTH_MULTITENANT", raising=False)
    assert health_db._resolve_health_dir()  # не бросает
