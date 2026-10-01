"""Оракул онбординга: публичная зона поднимается на чужой машине (scripts/clean_clone_probe):
выгрузка → scripts/install.py → init_db → импорт ключевых модулей, в чистом окружении.

Судит только в каноне окружения (requirements.txt — Studio): установку зависимостей проба
не проверяет (названо в её докстринге), и на машине без канона краснела бы не установка, а
окружение (MacBook: нет python-multipart и ещё ~40 пакетов, замер 2026-09-23).
"""
import importlib.metadata as md
import re
from pathlib import Path

import pytest

from scripts import clean_clone_probe

_LOCK = Path(__file__).resolve().parents[2] / "requirements.txt"


def _lock_missing() -> list[str]:
    out = []
    for line in _LOCK.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[=<>~!\[; ]", line)[0]
        if ";" in line:   # маркер среды (pyobjc только на macOS, docker-install этап 5)
            from packaging.markers import Marker
            if not Marker(line.split(";", 1)[1].strip()).evaluate():
                continue  # пакет не для этой платформы — его отсутствие не «не по канону»
        try:
            md.version(name)
        except md.PackageNotFoundError:
            out.append(name)
    return out


def test_публичная_зона_поднимается_на_чужой_машине():
    missing = _lock_missing()
    if missing:
        pytest.skip(f"окружение не по requirements.txt ({len(missing)} пакетов нет, напр. "
                    f"{missing[:3]}) — проба судит установку только в каноне окружения")
    assert clean_clone_probe.main([]) == 0
