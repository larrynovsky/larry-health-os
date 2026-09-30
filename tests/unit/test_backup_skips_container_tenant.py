"""Нативный бэкап при смешанной установке (docker-install, этап 11): владелец в контейнере, партнёр
и соседний проект — на хосте. Служба бэкапа остаётся нативной (она обходит всех тенантов); тенанта с меткой
RUNTIME=container она пропускает — его замороженный нативный файл бэкапить бессмысленно, а
контрольная точка WAL на файле только для чтения давала бы ложный FAIL каждую ночь.
Краснеет, если метку перестали читать или если вместе с владельцем пропал бэкап партнёра."""
import shutil
import sqlite3
import subprocess
import sys
import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def backup_repo(tmp_path):
    """Реальные скрипты, но только синтетическая конфигурация и свой HOME."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    for name in ("backup_studio.sh", "infra_config.py", "scripts/rotate_act_snapshots.sh",
                 "scripts/rotate_backup_snapshots.sh"):
        shutil.copy(REPO / name, repo / name)
    return repo


@pytest.fixture
def backup_env(tmp_path):
    # На MacBook зависимости Python могут жить в user-site. Синтетический HOME
    # не должен их скрыть; пути пакетов сохраняем, конфигурация берётся из backup_repo.
    return {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "HEALTH_PY": sys.executable,
            "PYTHONPATH": os.pathsep.join(p for p in sys.path if Path(p).is_absolute())}


@pytest.mark.skipif(not shutil.which("sqlite3"), reason="нет sqlite3 CLI")
def test_тенант_в_контейнере_пропущен_партнёр_забэкаплен(tmp_path, backup_repo, backup_env):
    for tenant in ("health", "health_partner"):
        d = tmp_path / tenant / "data"
        d.mkdir(parents=True)
        with sqlite3.connect(d / "health.db") as c:
            c.execute("create table t(x)")
    (tmp_path / "health" / "RUNTIME").write_text("container\n")
    r = subprocess.run(["bash", str(backup_repo / "backup_studio.sh")],
                       env=backup_env,
                       capture_output=True, text=True, timeout=60)
    log = (tmp_path / "health" / "logs" / "backup.log").read_text()
    assert list((tmp_path / "health_partner" / "backups").glob("health_partner_*.db")), log
    assert not (tmp_path / "health" / "backups").exists() or not list((tmp_path / "health" / "backups").glob("*.db"))
    assert "[health] skip (живёт в контейнере" in log
    assert "не найдено ни одного health.db" not in log, r.stdout + r.stderr
    assert r.returncode == 0, log


@pytest.mark.skipif(not shutil.which("sqlite3"), reason="нет sqlite3 CLI")
@pytest.mark.parametrize("configured", [False, True])
def test_neighbor_backup_noop_or_same_data_name_permissions_and_rotation(tmp_path, backup_repo, backup_env, configured):
    import yaml
    for root, db in ((tmp_path / "health" / "data", "health.db"),
                     (tmp_path / "crm's space", "crm.db"), (tmp_path / "notes", "notes.db")):
        root.mkdir(parents=True)
        with sqlite3.connect(root / db) as c:
            c.execute("create table t(x)")
            c.execute("insert into t values(7)")
    backups = tmp_path / "crm's space" / "backups"
    backups.mkdir()
    old = backups / "crm_2000-01-01.db"
    old.write_text("old")
    os.utime(old, (time.time() - 32 * 86400,) * 2)
    other = backups / "other_2000-01-01.db"
    other.write_text("keep")
    os.utime(other, (time.time() - 32 * 86400,) * 2)
    if configured:
        (backup_repo / "private").mkdir()
        (backup_repo / "private" / "infra.yaml").write_text(yaml.safe_dump({"neighbors": {
            "crm": {"path": str(tmp_path / "crm's space"), "db": "crm.db"},
            "notes": {"path": str(tmp_path / "notes"), "db": "notes.db"},
        }}))
    r = subprocess.run(["bash", str(backup_repo / "backup_studio.sh")],
                       env=backup_env,
                       capture_output=True, text=True, timeout=60)
    log = (tmp_path / "health" / "logs" / "backup.log").read_text()
    assert r.returncode == 0, log + r.stderr
    assert list((tmp_path / "health" / "backups").glob("health_*.db")), log
    assert old.exists() is not configured and other.exists()
    if configured:
        today = time.strftime("%Y-%m-%d")
        for root, prefix in ((tmp_path / "crm's space", "crm"), (tmp_path / "notes", "notes")):
            backup = root / "backups" / f"{prefix}_{today}.db"
            assert backup.stat().st_mode & 0o777 == 0o600
            with sqlite3.connect(backup) as c:
                assert c.execute("select x from t").fetchall() == [(7,)]
    else:
        assert list(backups.glob("crm_*.db")) == [old]
        assert not (tmp_path / "notes" / "backups").exists()


@pytest.mark.skipif(not shutil.which("sqlite3"), reason="нет sqlite3 CLI")
def test_broken_neighbor_config_is_loud_but_tenant_still_backed_up(tmp_path, backup_repo, backup_env):
    d = tmp_path / "health" / "data"
    d.mkdir(parents=True)
    with sqlite3.connect(d / "health.db") as c:
        c.execute("create table t(x)")
    (backup_repo / "private").mkdir()
    (backup_repo / "private" / "infra.yaml").write_text("neighbors: []\n")
    r = subprocess.run(["bash", str(backup_repo / "backup_studio.sh")],
                       env=backup_env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1
    assert list((tmp_path / "health" / "backups").glob("health_*.db"))
    assert "ERROR: конфигурация соседних проектов" in (tmp_path / "health" / "logs" / "backup.log").read_text()
