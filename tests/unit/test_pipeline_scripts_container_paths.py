"""Обёртки literature-read / survivorship не привязаны к раскладке Мака.

Карточка 2026-10-01: в контейнере код лежит в /app, HOME=/home/health без health_scripts,
Homebrew нет — `cd $HOME/health_scripts` и `/opt/homebrew/bin/python3.11` роняли задачу
(«cd: /home/health/health_scripts: No such file or directory»). Тест кладёт скрипт в
одноразовую копию с пустым HOME и подменяет интерпретатор через шов HEALTH_PY: заглушка
пишет, ИЗ КАКОГО каталога и с какими аргументами её позвали.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _run(tmp_path: Path, script: str) -> tuple[list, Path]:
    repo = tmp_path / "app"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / script, repo / "scripts" / script)
    log = tmp_path / "calls.log"
    stub = tmp_path / "py"
    stub.write_text(f'#!/bin/sh\necho "$PWD|$*" >> "{log}"\n')
    stub.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "HEALTH_PY": str(stub)}
    r = subprocess.run(["/bin/bash", str(repo / "scripts" / script)], cwd=tmp_path,
                       env=env, capture_output=True, text=True, timeout=30)
    assert "No such file or directory" not in r.stdout + r.stderr, r.stdout + r.stderr
    return [line.split("|", 1) for line in log.read_text().splitlines()] if log.exists() else [], repo


def test_literature_pipeline_runs_from_repo_via_health_py(tmp_path):
    calls, repo = _run(tmp_path, "run_literature_pipeline.sh")
    assert [c[1] for c in calls] == ["publication_reader.py --max 10",
                                     "literature_curator.py --max 10"]
    assert {os.path.realpath(c[0]) for c in calls} == {os.path.realpath(repo)}


def test_survivorship_pipeline_uses_health_py(tmp_path):
    calls, repo = _run(tmp_path, "run_survivorship_pipeline.sh")
    assert calls, "интерпретатор HEALTH_PY не позван ни разу"
    assert calls[0][1].startswith("-c import health_db, proposals_db")
    assert {os.path.realpath(c[0]) for c in calls} == {os.path.realpath(repo)}
