"""Регрессия инцидента 2026-06-28: ложный алерт «apple_health устарели 322ч».

Причина: метка свежести двигалась только при count>0; дедуп с Oura → свежие HAE-файлы дают
0 новых записей → метка замерзала → сенсор алертил, хотя телефон ещё экспортирует.
Фикс: `_export_is_fresh()` — метку двигаем, если есть свежий HAE-файл (телефон жив),
но НЕ двигаем при отсутствии файлов (реальный обрыв детектится).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.unit


def test_export_is_fresh_recent_file(tmp_path, monkeypatch):
    import import_apple_health as iah
    monkeypatch.setattr(iah, "HAE_DAILY_DIR", tmp_path)
    monkeypatch.setattr(iah, "HAE_ARCHIVE_DIR", tmp_path / "archive")
    recent = (date.today() - timedelta(days=1)).isoformat()
    (tmp_path / f"HealthAutoExport-{recent}.json").write_text("{}")
    assert iah._export_is_fresh() is True


def test_export_is_fresh_only_old_files(tmp_path, monkeypatch):
    import import_apple_health as iah
    monkeypatch.setattr(iah, "HAE_DAILY_DIR", tmp_path)
    monkeypatch.setattr(iah, "HAE_ARCHIVE_DIR", tmp_path / "archive")
    old = (date.today() - timedelta(days=15)).isoformat()
    (tmp_path / f"HealthAutoExport-{old}.json").write_text("{}")
    assert iah._export_is_fresh() is False


def test_export_is_fresh_no_files_is_outage(tmp_path, monkeypatch):
    """Нет файлов → реальный обрыв экспорта → не свежо (сенсор должен сработать)."""
    import import_apple_health as iah
    monkeypatch.setattr(iah, "HAE_DAILY_DIR", tmp_path)
    monkeypatch.setattr(iah, "HAE_ARCHIVE_DIR", tmp_path / "archive")
    assert iah._export_is_fresh() is False
