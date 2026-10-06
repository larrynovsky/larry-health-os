"""Список фиктивных секретов стенда живёт в одном файле: scripts/stand_secrets.sh."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_one_home_for_stand_secrets():
    src = (ROOT / "scripts/test_on_studio.sh").read_text(encoding="utf-8")
    assert "stand_secrets.sh" in src
    assert "STAGING_DUMMY" not in src, "второй дом списка секретов"


def test_stand_secrets_are_fake_and_private(tmp_path):
    d = tmp_path / "s"
    subprocess.run(["sh", str(ROOT / "scripts/stand_secrets.sh"), str(d)], check=True)
    assert (d / "telegram_token").read_text().strip() == "STAGING_DUMMY"
    assert oct((d / "telegram_token").stat().st_mode & 0o777) == "0o600"
