"""Шаблоны фоновых сервисов (templates/launchd) с путями владельца дают РОВНО его живые плисты
(по содержимому plist, комментарии не в счёт). Ключевой момент плана онбординга: плист из
шаблона, разошедшийся с живым, после загрузки читал бы не тот каталог — для второго тенанта
это чужая база. Судит только на машине владельца, где живые плисты есть."""
import plistlib
from pathlib import Path

import pytest

from scripts import install

LA = Path.home() / "Library" / "LaunchAgents"


def test_шаблоны_с_путями_владельца_равны_живым_плистам():
    if not (LA / "com.larry.health.bot.plist").exists():
        pytest.skip("живых плистов владельца здесь нет (не основная машина)")
    h = Path.home()
    out = install.render_launchd({"REPO": str(h / "health_scripts"), "HOME": str(h),
                                  "DATA": str(h / "health"), "SECRETS": str(h / ".health_secrets"),
                                  "TZ": "UTC"})
    diff = [n for n, text in out.items() if (LA / n).exists()
            and plistlib.loads(text.encode()) != plistlib.loads((LA / n).read_bytes())]
    absent = [n for n in out if not (LA / n).exists()]
    assert not diff and not absent, f"разошлись с живыми: {diff}; шаблона без живого: {absent}"
