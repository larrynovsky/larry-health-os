"""scripts/install.sh: отказывает до изменений и едет в выпуск.

Полный путь (установка, повтор, поддельные ключи) исполняет .github/workflows/tutorial.yml на чистой
Ubuntu; здесь — то, что краснеет без Докера и сети на любой машине."""
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
SH = ROOT / "scripts" / "install.sh"


def _check(dir_: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SH), "--check", "--dir", str(dir_)], capture_output=True,
                          text=True, timeout=120, env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(dir_.parent), "LANG": "C"})


def test_foreign_directory_is_refused_and_untouched(tmp_path):
    """Чужой непустой каталог → код 1 и ни одного нового файла. Падение = скрипт пишет в чужое."""
    d = tmp_path / "other-app"
    d.mkdir()
    (d / "notes.txt").write_text("x")
    r = _check(d)
    assert r.returncode == 1
    assert "not empty" in r.stdout
    assert sorted(p.name for p in d.iterdir()) == ["notes.txt"]


def test_leftovers_of_interrupted_download_are_not_foreign(tmp_path):
    """Обрыв скачивания оставил compose.yaml.part → повтор не должен считать каталог чужим."""
    d = tmp_path / "health-docker"
    d.mkdir()
    (d / "compose.yaml.part").write_text("")
    assert "not empty" not in _check(d).stdout


def test_install_sh_is_a_release_asset():
    """Урок качает install.sh из выпуска: release.yml кладёт его в dist, public_mirror его требует."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("public_mirror", ROOT / "scripts" / "public_mirror.py")
    pm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pm)
    assert "install.sh" in pm.RELEASE_ASSETS
    assert re.search(r"cp scripts/install\.sh dist/install\.sh", (ROOT / ".github/workflows/release.yml").read_text())
