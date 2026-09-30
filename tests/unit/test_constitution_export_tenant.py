"""
generate_constitutions._export_dir — куда пишется производный .md-экспорт конституций.

Баг (2026-07-04, найден перед генерацией конституций партнёра): _save писал
экспорт в жёстко заданный каталог iCloud владельца. Экспорт партнёра затирал бы
sleep.md и т.д. владельца (одинаковые имена) и утекал в его iCloud.

Баг (2026-09-24, приёмка урока установки свежим агентом): тот же каталог iCloud
создавался ПРИ ИМПОРТЕ модуля — у постороннего один импорт (хоть тестом) заводил
папку health/ в его iCloud Drive. Теперь зеркало — настройка установки
(private/infra.yaml, constitutions_mirror); нет настройки → каталог данных.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import generate_constitutions as gc
import infra_config

pytestmark = pytest.mark.unit


def test_export_dir_tenant_is_local(monkeypatch, tmp_path):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health_partner"))
    monkeypatch.setattr(infra_config, "CONSTITUTIONS_MIRROR", tmp_path / "mirror")
    assert gc._export_dir() == tmp_path / "health_partner" / "constitutions"  # тенант зеркало владельца не берёт


def test_export_dir_owner_uses_configured_mirror(monkeypatch, tmp_path):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setattr(infra_config, "CONSTITUTIONS_MIRROR", tmp_path / "mirror")
    assert gc._export_dir() == tmp_path / "mirror"


def test_export_dir_owner_without_mirror_is_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health"))
    monkeypatch.setattr(infra_config, "CONSTITUTIONS_MIRROR", None)
    assert gc._export_dir() == tmp_path / "health" / "constitutions"


def test_export_dir_unset_without_mirror_is_home_health(monkeypatch, tmp_path):
    monkeypatch.delenv("HEALTH_DATA_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(infra_config, "CONSTITUTIONS_MIRROR", None)
    assert gc._export_dir() == tmp_path / "health" / "constitutions"


def test_import_does_not_touch_icloud(tmp_path):
    """Импорт модуля не создаёт ничего в ~/Library (iCloud Drive того, кто запустил)."""
    import site   # другой HOME прячет пакеты из user-site — возвращаем их явно, как проба чистого клона
    pp = os.pathsep.join(p for p in (site.getusersitepackages(), os.environ.get("PYTHONPATH", "")) if p)
    env = {**os.environ, "HOME": str(tmp_path), "PYTHONPATH": pp}
    r = subprocess.run([sys.executable, "-c", "import generate_constitutions"],
                       cwd=Path(gc.__file__).parent, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    assert not (tmp_path / "Library").exists(), "импорт создал каталог в ~/Library"
