"""pip и setuptools образа (docker/Dockerfile, ARG) не ниже версий, закрывающих известные advisory.
Краснеет, если закреплённая версия откатится под уже найденную уязвимость: пол — это квитанция
закрытых advisory, а не «последняя версия» (её тест не знает и знать не должен — сети нет).
Новый advisory сюда приносит тот, кто поднимает ARG; см. docs/how-to/dependency_updates.md."""
import re
from pathlib import Path

import pytest
from packaging.version import Version

pytestmark = pytest.mark.unit
_DOCKERFILE = Path(__file__).resolve().parents[2] / "docker" / "Dockerfile"

# пакет → (минимальная версия, advisory, которые она закрывает)
_FLOORS = {
    "PIP_VERSION": ("26.2.1", "PYSEC-2026-1795/1796/2875/2876/196/3721"),
    "SETUPTOOLS_VERSION": ("84.1.0", "PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w"),
}


def _arg(name: str) -> str:
    m = re.search(rf"^ARG {name}=(\S+)\s*$", _DOCKERFILE.read_text(), re.M)
    assert m, f"ARG {name} пропал из docker/Dockerfile"
    return m.group(1)


@pytest.mark.parametrize("name", sorted(_FLOORS))
def test_версия_установщика_образа_не_ниже_закрывающей_advisory(name):
    floor, advisories = _FLOORS[name]
    assert Version(_arg(name)) >= Version(floor), (
        f"{name}={_arg(name)} ниже {floor} — уязвим к {advisories}")
