#!/usr/bin/env python3.11
"""
test_daemon_liveness — парсер «KeepAlive-демон без PID».

Чистый тест без subprocess/launchctl: проверяем именно логику отбора.
Регресс-гард для класса «сервис тихо умер» (health.api crash-loop 14 дней).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import daemon_liveness as dl

import infra_config as _ic
import pytest
_PRIMARY = _ic.PRIMARY_HOST  # основная машина установки, не литерал владельца

# Реалистичный фрагмент `launchctl list` (PID \t STATUS \t LABEL)
SAMPLE = "\n".join([
    "PID\tStatus\tLabel",
    "1059\t0\tcom.larry.health.bot",            # живой демон
    "-\t1\tcom.larry.health.api",               # упал (KeepAlive, нет PID) ← цель
    "-\t1\tcom.larry.health.integrity-check",   # scheduled (НЕ KeepAlive) — не трогать
    "1039\t0\tcom.larry.health.dashboard",      # живой
    "-\t0\tcom.apple.something",                # чужой — игнор
])


def _ka(keepalive_labels):
    return lambda label: label in keepalive_labels


def test_down_keepalive_daemon_flagged():
    down = dl.parse_down_daemons(SAMPLE, _ka({"com.larry.health.api",
                                              "com.larry.health.bot",
                                              "com.larry.health.dashboard"}))
    assert any("com.larry.health.api" in d for d in down), f"api не пойман: {down}"
    assert not any("bot" in d for d in down), "живой bot не должен флагаться"
    assert not any("dashboard" in d for d in down)


def test_scheduled_job_not_flagged():
    """integrity-check без PID и exit=1 — это нормальный scheduled, НЕ KeepAlive."""
    down = dl.parse_down_daemons(SAMPLE, _ka({"com.larry.health.api"}))
    assert not any("integrity-check" in d for d in down), \
        "scheduled-джоб не должен считаться упавшим демоном"


def test_foreign_labels_ignored():
    down = dl.parse_down_daemons(SAMPLE, _ka(set()))  # ничего не keepalive
    assert down == []


def test_last_exit_included():
    down = dl.parse_down_daemons(SAMPLE, _ka({"com.larry.health.api"}))
    assert "last_exit=1" in down[0], f"нет кода выхода в сообщении: {down}"


@pytest.mark.parametrize("configured", [False, True])
def test_only_configured_neighbor_daemons_are_judged(monkeypatch, configured):
    monkeypatch.setattr(_ic, "NEIGHBORS", {"crm": {"label_prefix": "org.example.crm"}} if configured else {})
    sample = ("-\t9\torg.example.crm.api\n123\t0\torg.example.crm.bot\n"
              "-\t0\torg.example.other.api\n-\t1\tcom.larry.health.api")
    down = dl.parse_down_daemons(sample, lambda label: True)
    assert down == (["org.example.crm.api (last_exit=9)"] if configured else []) + [
        "com.larry.health.api (last_exit=1)"]


@pytest.mark.host_only
def test_find_down_daemons_studio_orchestration(monkeypatch):
    """Studio-путь: gethostname=Studio + замоканный launchctl → ловит api, не scheduled."""
    import socket
    import subprocess
    import time as _t
    monkeypatch.setattr(socket, "gethostname", lambda: _PRIMARY)

    class _R:
        stdout = SAMPLE
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
    monkeypatch.setattr(_t, "sleep", lambda s: None)  # без 2с задержки
    monkeypatch.setattr(dl, "_is_keepalive", lambda label: label == "com.larry.health.api")
    down = dl.find_down_daemons()
    assert any("com.larry.health.api" in d for d in down), f"api не пойман: {down}"
    assert not any("integrity-check" in d for d in down)


def test_find_down_daemons_off_studio(monkeypatch):
    """Вне Studio — пусто (демоны живут на Studio), без обращения к launchctl."""
    import socket
    monkeypatch.setattr(socket, "gethostname", lambda: "MacBook-Pro.local")
    assert dl.find_down_daemons() == []


if __name__ == "__main__":
    test_down_keepalive_daemon_flagged()
    test_scheduled_job_not_flagged()
    test_foreign_labels_ignored()
    test_last_exit_included()
    print("TEST PASS")
