"""Машинный судья Studio: проверки самой машины судит один прогон, владелец видит его находки (нить machine-judge, 03.10).

До 03.10 снимки MacBook, launchd и экспозицию хоста судил только ночной прогон партнёра — случайно.
Импорт integrity_tests исполняет монитор целиком, поэтому функции берутся из исходника (приём
test_fault_journal / test_watchdog_liveness_container).
"""
from __future__ import annotations

import ast
import json
import plistlib
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
LABEL = "com.larry.health.machine-check"


def _load(names, env):
    src = (ROOT / "integrity_tests.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    tree.body = [n for n in tree.body if getattr(n, "name", None) in names]
    exec(compile(tree, "integrity_tests.py", "exec"), env)
    return env


def _judge_env(monkeypatch, in_container=True, host_judges=False):
    import sys
    import plist_env_liveness as real
    monkeypatch.setitem(sys.modules, "plist_env_liveness", SimpleNamespace(
        in_container=lambda: in_container, artifact_covers_last_fire=real.artifact_covers_last_fire))
    out = {"warns": [], "fails": []}
    env = {"MACHINE_SCOPE": False, "MACHINE_LABEL": LABEL, "MACHINE_RESULT": "machine_integrity_latest.json",
           "_not_judged_here": ["a", "b"], "_sensor_of": {}, "_warnings": [], "_failures": [],
           "_host_judged_here": lambda: host_judges, "Path": Path, "json": json,
           "get_now": lambda *a: datetime(2026, 10, 3, 9, 0)}
    env["warn"] = lambda label, detail="": (out["warns"].append(label), env["_warnings"].append((label, detail)))
    env["fail_"] = lambda label, detail="": out["fails"].append(label)
    _load({"check_machine_judge_alive"}, env)
    return env["check_machine_judge_alive"], out


def _host_logs(tmp_path, ran_at, data=None):
    live = tmp_path / "launchd_live"
    live.mkdir(parents=True)
    (live / f"{LABEL}.plist").write_bytes(plistlib.dumps(
        {"Label": LABEL, "StartCalendarInterval": {"Hour": 7, "Minute": 40}}))
    body = dict(data or {}, ran_at=ran_at)
    (tmp_path / "machine_integrity_latest.json").write_text(json.dumps(body, ensure_ascii=False))
    return tmp_path


def test_fresh_result_findings_reach_the_owner(tmp_path, monkeypatch):
    fn, out = _judge_env(monkeypatch)
    logs = _host_logs(tmp_path, "2026-10-03T07:41:00+03:00", {
        "failures": [["код MacBook доехал до Studio (по снимку)", "нет"]],
        "warnings": [["launchd: инвентарь", "разошлось"]],
        "sensor_of": {"код MacBook доехал до Studio (по снимку)": "check_macbook_head_deployed"}})
    fn(host_logs=str(logs), now=datetime(2026, 10, 3, 9, 0))
    assert out["fails"] == ["код MacBook доехал до Studio (по снимку)"]
    assert out["warns"] == ["launchd: инвентарь"], out


def test_missed_run_is_loud(tmp_path, monkeypatch):
    fn, out = _judge_env(monkeypatch)
    logs = _host_logs(tmp_path, "2026-10-01T07:41:00+03:00")
    fn(host_logs=str(logs), now=datetime(2026, 10, 3, 9, 0))
    assert "машинный судья молчит" in out["warns"]


def test_never_ran_is_loud(tmp_path, monkeypatch):
    fn, out = _judge_env(monkeypatch)
    fn(host_logs=str(tmp_path), now=datetime(2026, 10, 3, 9, 0))
    assert out["warns"] == ["машинный судья не отметился ни разу"]


def test_shared_sensor_is_not_duplicated(tmp_path, monkeypatch):
    fn, out = _judge_env(monkeypatch)
    logs = _host_logs(tmp_path, "2026-10-03T07:41:00+03:00", {"warnings": [["security:pip_audit", "x"]]})
    out["warns"].clear()
    fn.__globals__["_warnings"].append(("security:pip_audit", "x"))
    fn(host_logs=str(logs), now=datetime(2026, 10, 3, 9, 0))
    assert out["warns"] == []


def test_stranger_container_without_host_logs_is_quiet(monkeypatch):
    monkeypatch.delenv("HEALTH_HOST_LOGS", raising=False)
    fn, out = _judge_env(monkeypatch)
    assert "журналов хоста здесь нет" in fn() and out["warns"] == []


def _check_env(monkeypatch, machine_scope, host_judges):
    calls = []
    env = {"MACHINE_SCOPE": machine_scope, "_not_judged_here": [], "_current_sensor": "",
           "_host_judged_here": lambda: host_judges, "PASS": 0, "FAIL": 0, "JSON_OUTPUT": True,
           "_failures": [], "_critical": [], "_sensor_of": {}, "_code_failures": []}
    _load({"check"}, env)

    def fn():
        calls.append(1)
        return "ok"
    return env, calls, fn


def test_container_skips_host_checks_silently(monkeypatch):
    env, calls, fn = _check_env(monkeypatch, machine_scope=False, host_judges=False)
    env["check"]("снимок MacBook", fn, host_only=True)
    assert calls == [] and env["_not_judged_here"] == ["снимок MacBook"]


def test_machine_scope_runs_only_machine_checks(monkeypatch):
    env, calls, fn = _check_env(monkeypatch, machine_scope=True, host_judges=False)
    env["check"]("данные тенанта", fn)
    assert calls == []
    env["check"]("снимок MacBook", fn, host_only=True)
    env["check"]("SEC", fn, machine=True)
    assert calls == [1, 1]


def test_native_owner_still_judges_host_checks(monkeypatch):
    env, calls, fn = _check_env(monkeypatch, machine_scope=False, host_judges=True)
    env["check"]("снимок MacBook", fn, host_only=True)
    assert calls == [1]
