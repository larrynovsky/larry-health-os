"""D++ (2026-06-19, ICLOUD-STUB-FIX Sprint 3): regression tests catch-all.

Три distinct test'a для разных axis защиты от iCloud-stub creation:

D1 class-based: rglob *.db в iCloud-tree → assert empty (кроме whitelist).
  Защита от Pesticide paradox (RST): test_no_health_db_in_icloud ловит только
  два конкретных path'а — кто-то завтра создаст stub в другой sub-folder, и
  тест пропустит. D1 ловит **класс** ошибки, не конкретный path.

D3 HEALTH_DATA_DIR injection: monkeypatch=iCloud-path → get_conn raise.
  Защита от ситуации «кто-то set HEALTH_DATA_DIR=iCloud по ошибке» —
  должна сработать A++ path-content validation на module load.

(D2 import-and-check для 20 callers перенесён в Sprint 2 followup task #206
вместе с миграцией callers — без B++ migrate callers падают по другой
причине, тест становится noise.)

Контекст: инцидент 2026-06-18 (см. db_reconciliation_2026-06-18). iCloud Drive
реплицирует .db без merge-protocol → split-brain. Canonical health.db живёт
только на локальном диске Studio (~/health/data/). A++ guard в health_db.py
(commit a2414bd) + C++ disable launchd-jobs на MacBook (Sprint 1) закрыли
два главных вектора. D++ catch-all для регрессий.
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.consistency

import infra_config
# облачная папка установки (BL-PUB-12): нет настройки → стеречь нечего, тест пропускается
ICLOUD_HEALTH = infra_config.CLOUD_HEALTH_DIR or Path("/nonexistent-cloud-health")

# Whitelist: эти .db файлы в iCloud-tree разрешены (legacy off-site backups
# + Obsidian state files, не связаны с canonical health.db). Backlog item:
# rm если они тоже не нужны.
ALLOWED_DB_BASENAMES_IN_ICLOUD = {
    # Obsidian local wiki state (методология CBCR, не health DB)
    "state.db",
}

# Whitelist по path-pattern для legacy backups (Apr 2026, off-site).
ALLOWED_DB_PATH_PATTERNS = (
    "backups/health_",   # ~/health/backups/health_2026-04-*.db (legacy off-site)
)


def _is_allowed(path: Path) -> bool:
    """True если файл в whitelist (legacy backups или Obsidian state)."""
    if path.name in ALLOWED_DB_BASENAMES_IN_ICLOUD:
        return True
    for pattern in ALLOWED_DB_PATH_PATTERNS:
        if pattern in str(path):
            return True
    return False


def test_no_unauthorized_db_files_in_icloud_tree():
    """D1: catch-all class-based detection.

    rglob *.db в iCloud-tree → assert все либо в whitelist, либо отсутствуют.
    Защищает от Pesticide paradox: даже если кто-то создаст stub в новом
    sub-folder (не в /data/health.db), test ловит."""
    if not ICLOUD_HEALTH.exists():
        pytest.skip("нет iCloud health dir на этом хосте (skip на не-Studio)")
    found = list(ICLOUD_HEALTH.rglob("*.db"))
    unauthorized = [str(p) for p in found if not _is_allowed(p)]
    assert not unauthorized, (
        f"Найдены неразрешённые .db файлы в iCloud-tree: {unauthorized}. "
        f"R1/R2 нарушено — какой-то caller создал stub. См. A++ guard "
        f"(health_db._validate_db_path) и CLAUDE.md §8."
    )


def test_get_conn_rejects_icloud_path_when_explicit(monkeypatch, tmp_path):
    """D3: A++ guard срабатывает даже когда HEALTH_DATA_DIR explicit указывает в iCloud.

    Это защита от ситуации: разработчик/launchd plist по ошибке ставит
    HEALTH_DATA_DIR=iCloud-path думая что это «обходной» путь. A++ должен
    блокировать любой путь в cloud-sync folder независимо от того хост это
    primary или нет.

    Тест: monkeypatch HEALTH_DATA_DIR=fake-iCloud-path, перезагрузить
    health_db (через importlib), assert ImportError/RuntimeError на module
    load (вызов _validate_db_path).
    """
    import importlib
    import sys

    # Fake iCloud-path: substring "Mobile Documents/com~apple~CloudDocs" — точно
    # в forbidden list health_db._FORBIDDEN_DB_PATH_SUBSTRINGS.
    fake_icloud = tmp_path / "Library/Mobile Documents/com~apple~CloudDocs/health"
    fake_icloud.mkdir(parents=True)
    monkeypatch.setenv("HEALTH_DATA_DIR", str(fake_icloud))

    # Сохранить ИСХОДНЫЙ объект модуля health_db, чтобы вернуть именно его (не
    # свежий!) — уже импортировавшие health_db модули (treatment_*, survivorship)
    # держат старый объект; если sys.modules получит новый → split-brain и потеря
    # изоляции в полном прогоне tests/ (тот же класс бага, что фикс 138ba20).
    _orig_health_db = sys.modules.get("health_db")
    sys.modules.pop("health_db", None)

    try:
        with pytest.raises(RuntimeError, match="cloud-sync folder"):
            importlib.import_module("health_db")
    finally:
        if _orig_health_db is not None:
            sys.modules["health_db"] = _orig_health_db
        else:
            sys.modules.pop("health_db", None)


def test_validate_db_path_helper_directly():
    """Sanity: A++ helper _validate_db_path сам raise на iCloud-path.

    Этот тест дублирует test_db_path_validation.py для уверенности что
    consistency layer прошёл (defense-in-depth: unit + consistency)."""
    # health_db должен быть импортируем на Studio (без iCloud в DB_PATH).
    import health_db
    bad = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/health/data/health.db"
    with pytest.raises(RuntimeError, match="cloud-sync folder"):
        health_db._validate_db_path(bad)
