"""A++ (2026-06-19, ICLOUD-STUB-FIX): test _validate_db_path guard.

Покрывает R1/R2 enforcement на module-load level: DB_PATH не может
лежать в cloud-sync folder (iCloud/Dropbox/Google Drive/OneDrive/Box).

Контекст: до A++ 20 direct sqlite3.connect(DB_PATH) callers обходили
get_conn() guard. На MacBook без HEALTH_DATA_DIR DB_PATH = iCloud-путь
→ direct connect создавал empty SQLite stub → iCloud sync доставлял на
Studio → ночной test_no_health_db_in_icloud fail.

A++ закрывает класс ошибки на module import: если DB_PATH в cloud-sync
folder, health_db не импортируется (raise). Все зависящие модули падают
вместе с ним. Это желаемое поведение: MacBook не имеет DB access (по
архитектурному решению 2026-06-19).
"""
from pathlib import Path

import pytest

from health_db import _validate_db_path, _FORBIDDEN_DB_PATH_SUBSTRINGS


class TestValidDbPaths:
    """Локальные пути проходят валидацию."""

    def test_studio_canonical_path_ok(self):
        """Канонический Studio path — не raise."""
        _validate_db_path(Path("/Users/zz/health/data/health.db"))

    def test_tmp_path_ok(self, tmp_path):
        """Tmp path для тестов — не raise."""
        _validate_db_path(tmp_path / "health" / "data" / "health.db")

    def test_arbitrary_local_path_ok(self):
        """Любой локальный путь без cloud-sync substrings — не raise."""
        _validate_db_path(Path("/opt/healthdb/data.db"))
        _validate_db_path(Path("/var/lib/health.db"))


class TestForbiddenCloudPaths:
    """Пути в cloud-sync folder — raise с понятным message."""

    def test_icloud_path_raises(self):
        """iCloud Drive — основной вектор инцидента 2026-06-18."""
        bad = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/health/data/health.db"
        with pytest.raises(RuntimeError, match="cloud-sync folder"):
            _validate_db_path(bad)

    def test_dropbox_path_raises(self):
        with pytest.raises(RuntimeError, match="Dropbox"):
            _validate_db_path(Path.home() / "Dropbox/health/data/health.db")

    def test_google_drive_path_raises(self):
        with pytest.raises(RuntimeError, match="Google Drive"):
            _validate_db_path(Path.home() / "Google Drive/health/data/health.db")

    def test_onedrive_path_raises(self):
        with pytest.raises(RuntimeError, match="OneDrive"):
            _validate_db_path(Path.home() / "OneDrive/health/data/health.db")

    def test_message_mentions_R1R2(self):
        """Сообщение должно указать на CLAUDE.md §8 — куда смотреть."""
        bad = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/data.db"
        with pytest.raises(RuntimeError) as exc_info:
            _validate_db_path(bad)
        assert "R1/R2" in str(exc_info.value)
        assert "CLAUDE.md" in str(exc_info.value)


class TestForbiddenListCoverage:
    """Sanity: список запрещённых substrings содержит ожидаемые провайдеры."""

    def test_icloud_in_list(self):
        assert any("CloudDocs" in s for s in _FORBIDDEN_DB_PATH_SUBSTRINGS)

    def test_at_least_4_providers(self):
        """iCloud + Dropbox + Google Drive + OneDrive — минимум."""
        assert len(_FORBIDDEN_DB_PATH_SUBSTRINGS) >= 4
