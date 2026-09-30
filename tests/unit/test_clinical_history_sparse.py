"""Пустые `periods` у вымышленного нового профиля — допустимое отсутствие истории.
Секция должна деградировать без падения отчёта. Для основного профиля пустота
по-прежнему означает незапущенную миграцию и вызывает исключение.

Мокаем historical_periods → [] (тестируем ветку gp_context, не сам periods_db).
Обе ветки возвращаются/рейзят ДО обращения к БД, поэтому реальная БД не нужна.
"""
import pytest


def test_tenant_empty_periods_degrades(monkeypatch):
    import gp_context
    import health_db
    monkeypatch.setattr(health_db, "historical_periods", lambda **k: [])
    monkeypatch.setenv("HEALTH_DATA_DIR", "/x/health_partner")
    out = gp_context._build_clinical_history()
    assert "данных пока нет" in out  # деградация, без исключения


def test_owner_empty_periods_raises(monkeypatch):
    import gp_context
    import health_db
    monkeypatch.setattr(health_db, "historical_periods", lambda **k: [])
    monkeypatch.setenv("HEALTH_DATA_DIR", "/x/health")
    with pytest.raises(RuntimeError, match="migrate"):
        gp_context._build_clinical_history()
