"""requirements.txt — замок, который сборка образа обязана разрешить (02.10, llm-provider).

Ложный путь, который это держит: в замок добавили openai 3.19.2, а jiter остался 0.13.0;
полный прогон на Studio был зелёным (там стоял jiter 0.17.0), а сборка образа владельца упала
на ResolutionImpossible — деплой не прошёл, и узнали об этом из журнала деплоя, а не из теста.
Проверка без сети: для каждого закреплённого пакета, установленного в ТОЙ ЖЕ версии, его
зависимости (без extras) обязаны быть закреплены в замке версией, которую они принимают.
Незакреплённая транзитивная зависимость — тоже красный: сборка взяла бы свежий релиз мимо
карантина (docs/how-to/dependency_updates.md)."""
from importlib import metadata
from pathlib import Path

import pytest
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

pytestmark = pytest.mark.consistency
LOCK = Path(__file__).resolve().parents[2] / "requirements.txt"
# Сам установщик: есть в любом окружении, где замок вообще ставят (pip-api требует pip).
_INSTALLER = {"pip", "setuptools", "wheel"}


def _pins() -> dict[str, str]:
    out = {}
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        line = line.split(";")[0].strip()
        if line and not line.startswith("#") and "==" in line:
            name, ver = line.split("==", 1)
            out[canonicalize_name(name)] = ver.strip()
    return out


def lock_problems(pins: dict[str, str]) -> tuple[list[str], int]:
    env = dict(default_environment(), extra="")
    bad, judged = [], 0
    for name, ver in pins.items():
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        if dist.version != ver:
            continue                       # метаданные другой версии — не свидетель
        judged += 1
        for raw in dist.requires or []:
            req = Requirement(raw)
            if req.marker and not req.marker.evaluate(env):
                continue
            dep = canonicalize_name(req.name)
            if dep in _INSTALLER:
                continue
            if dep not in pins:
                bad.append(f"{name}=={ver} требует {req} — в замке нет")
            elif not req.specifier.contains(pins[dep], prereleases=True):
                bad.append(f"{name}=={ver} требует {req}, в замке {dep}=={pins[dep]}")
    return bad, judged


def test_lock_resolves():
    bad, judged = lock_problems(_pins())
    if judged < 20:
        pytest.skip(f"окружение не по замку: судимо {judged} пакетов")
    assert not bad, "\n".join(bad)


def test_detects_the_jiter_conflict():
    """Позитивный контроль на настоящем случае 02.10."""
    pins = _pins()
    if not _installed("openai") or metadata.version("openai") != pins.get("openai"):
        pytest.skip("openai не установлен в версии замка")
    pins["jiter"] = "0.13.0"
    bad, _ = lock_problems(pins)
    assert any("jiter" in b and "openai" in b for b in bad)


def _installed(name: str) -> bool:
    try:
        metadata.version(name)
        return True
    except metadata.PackageNotFoundError:
        return False
