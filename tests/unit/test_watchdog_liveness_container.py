"""Сторож незакоммиченного живёт на хосте — контейнер судит его по журналам хоста (нить host-container-split).

До 03.10 монитор в контейнере владельца искал отметку сторожа в своём /app/logs и каждое утро
писал «heartbeat отсутствует»: сторож при этом жил и отмечался на хосте. Импорт integrity_tests
исполняет монитор целиком, поэтому функция берётся из исходника (тот же приём, что в
test_fault_journal).
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]


def _check(in_container: bool, warns: list, monkeypatch):
    tree = ast.parse((ROOT / "integrity_tests.py").read_text(encoding="utf-8"))
    tree.body = [n for n in tree.body if getattr(n, "name", None) == "check_watchdog_liveness"]
    import sys
    monkeypatch.setitem(sys.modules, "plist_env_liveness",
                        SimpleNamespace(in_container=lambda: in_container))
    env = {"infra_config": SimpleNamespace(is_primary=lambda *a: True),
           "warn": lambda title, detail="": warns.append(title),
           "get_now": lambda tz=None: datetime.now(timezone.utc),
           "__file__": str(ROOT / "integrity_tests.py")}
    exec(compile(tree, "integrity_tests.py", "exec"), env)
    return env["check_watchdog_liveness"]


def _beat(d: Path, age_min: int) -> None:
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc) - timedelta(minutes=age_min)
    (d / "uncommitted_watchdog.heartbeat").write_text(ts.isoformat(), encoding="utf-8")


def test_container_reads_host_journal_fresh_is_quiet(tmp_path, monkeypatch):
    _beat(tmp_path / "host_logs", 10)
    monkeypatch.setenv("HEALTH_HOST_LOGS", str(tmp_path / "host_logs"))
    warns = []
    _check(True, warns, monkeypatch)()
    assert warns == []


def test_container_reads_host_journal_stale_warns(tmp_path, monkeypatch):
    _beat(tmp_path / "host_logs", 300)
    monkeypatch.setenv("HEALTH_HOST_LOGS", str(tmp_path / "host_logs"))
    warns = []
    _check(True, warns, monkeypatch)()
    assert warns == ["watchdog heartbeat устарел"]


def test_container_without_host_journal_is_unjudged_not_alarm(monkeypatch):
    """У постороннего сторожа на хосте нет: «не судимо» строкой, без вечного предупреждения."""
    monkeypatch.delenv("HEALTH_HOST_LOGS", raising=False)
    warns = []
    out = _check(True, warns, monkeypatch)()
    assert warns == [] and "не судимо" in out
