"""Single-writer = MacBook: pre-commit hook отклоняет коммиты на Studio (e017929).

Защищает центральный механизм предотвращения git-расхождения: если hostname-guard
в `scripts/git-hooks/pre-commit` уронят при рефакторинге, коммиты на Studio снова
станут возможны ТИХО → два писателя git → расхождение вернётся. Тест гоняет хук с
подменённым `hostname` (shim на PATH), проверяя три ветки: Studio→reject,
Studio+escape→bypass, MacBook→pass.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import infra_config as _ic
_PRIMARY = _ic.PRIMARY_HOST  # основная машина установки, не литерал владельца

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[2]
HOOK = ROOT / "scripts" / "git-hooks" / "pre-commit"


def _run_hook(tmp_path, hostname, extra_env=None):
    """Гоняет pre-commit с поддельным `hostname` на PATH. cwd — не-git tmp,
    поэтому после guard'а хук безвредно доходит до пустого `git diff` → exit 0."""
    shim = tmp_path / "bin"
    shim.mkdir()
    hn = shim / "hostname"
    hn.write_text(f"#!/bin/sh\necho {hostname}\n")
    hn.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{shim}:{env.get('PATH', '')}"
    env.pop("HEALTH_ALLOW_STUDIO_COMMIT", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(HOOK)], cwd=str(tmp_path),
        env=env, capture_output=True, text=True,
    )


def test_hook_exists():
    assert HOOK.exists(), f"pre-commit hook не найден: {HOOK}"


@pytest.mark.host_only
def test_rejects_commit_on_studio(tmp_path):
    r = _run_hook(tmp_path, _PRIMARY)
    out = r.stdout + r.stderr
    assert r.returncode != 0, f"ожидали reject (exit!=0), out={out!r}"
    assert "запрещён" in out, out


def test_escape_hatch_bypasses_on_studio(tmp_path):
    r = _run_hook(tmp_path, _PRIMARY, {"HEALTH_ALLOW_STUDIO_COMMIT": "1"})
    out = r.stdout + r.stderr
    assert "запрещён" not in out, f"escape hatch не сработал: {out!r}"


def test_allows_on_macbook(tmp_path):
    r = _run_hook(tmp_path, "zz-MacBook-Pro.local")
    out = r.stdout + r.stderr
    assert "запрещён" not in out, f"guard ложно сработал на MacBook: {out!r}"
