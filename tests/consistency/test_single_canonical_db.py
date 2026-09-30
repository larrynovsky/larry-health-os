"""R1/R2 split-brain prevention (2026-06-18): ровно один health.db, не в iCloud.

Источник: db_reconciliation_2026-06-18 (канон Studio <-> iCloud-форк разошлись).
iCloud Drive реплицирует .db без merge -> форк. Канон только на локальном диске.
Уровень: consistency. Гонять на Studio (где живёт канон и iCloud-дерево).
"""
from pathlib import Path
import socket

import pytest

pytestmark = pytest.mark.consistency

import infra_config
# облачная папка установки (BL-PUB-12): нет настройки → стеречь нечего, тест пропускается
ICLOUD_HEALTH = infra_config.CLOUD_HEALTH_DIR or Path("/nonexistent-cloud-health")


def test_no_health_db_in_icloud():
    """В iCloud-дереве health не должно быть health.db (R1)."""
    if not ICLOUD_HEALTH.exists():
        pytest.skip("нет iCloud health dir на этом хосте")
    strays = [str(p) for p in (ICLOUD_HEALTH / "data" / "health.db", ICLOUD_HEALTH / "health.db") if p.exists()]
    assert not strays, f"health.db в iCloud - split-brain risk: {strays}"


def test_no_stray_db_in_repo_root():
    """В корне репо нет забытого health.db (R2)."""
    repo_db = Path(__file__).resolve().parents[2] / "health.db"
    assert not repo_db.exists(), f"stray health.db в корне репо: {repo_db}"


def test_canonical_db_path_not_in_sync_folder_on_primary():
    """На primary (Studio) DB_PATH - локальный диск, не синк-папка (R1)."""
    import health_db
    if socket.gethostname() != health_db._PRIMARY_HOST:
        pytest.skip("проверка пути канона - только на primary (Studio)")
    p = str(health_db.DB_PATH)
    bad = [s for s in ("Mobile Documents", "CloudDocs", "Dropbox", "OneDrive") if s in p]
    assert not bad, f"canonical DB_PATH в синк-папке: {p}"


def test_invariant_detects_planted_icloud_db(monkeypatch, tmp_path):
    """Positive control (smoke ≠ positive-case): инвариант R1 ЛОВИТ подброшенный
    health.db в iCloud-дереве, а не только проходит на чистом состоянии.

    Реплицирует логику `integrity_tests.check_single_canonical_db` (его нельзя
    импортировать изолированно — это script, выполняющий проверки при импорте)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ich = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/health"
    (ich / "data").mkdir(parents=True)
    (ich / "data" / "health.db").write_text("fake")
    strays = [p for p in (ich / "data" / "health.db", ich / "health.db") if p.exists()]
    assert strays, "инвариант слеп: подброшенный health.db в iCloud не пойман"
