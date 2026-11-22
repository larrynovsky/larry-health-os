"""Карточка warn:сбой: oura_refresh (17.11): журнал сбоев нёс только «import failed (exit 1)» —
stderr импортёра задача выбрасывала, и ночной разбор не мог отличить сеть от протухшей авторизации.
Теперь класс сбоя уходит отдельным токеном detail (не текстом: в тексте URL и ответы провайдера),
и исходы без класса различимы: signal_N / no_traceback / unparsed (ревью 46973d29).
Основа — отклонённая починка 2026-11-17-46973d29; значения выдуманы."""
from __future__ import annotations

import asyncio
import subprocess
from types import SimpleNamespace as NS

import pytest

pytestmark = pytest.mark.unit
TB = b'Traceback (most recent call last):\n  File "import_oura.py", line 75, in main\n'


def _run_job(monkeypatch, returncode, stderr):
    import notify
    from jobs import scheduled
    faults = []
    monkeypatch.setattr(notify, "fault", lambda tech, *a, **k: faults.append((tech, k.get("detail"))))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], returncode, stdout=b"", stderr=stderr))
    asyncio.run(scheduled.oura_refresh_job(NS()))
    return faults


@pytest.mark.parametrize("rc, stderr, cls", [
    (1, TB + b"urllib.error.URLError: <urlopen error [Errno 8] nodename nor servname provided>\n", "URLError"),
    (1, TB + b"RuntimeError: Oura OAuth exchange failed (HTTP 401); secret-ish\n", "RuntimeError"),
    # заметка add_note печатается ПОСЛЕ итога — итог всё равно найден
    (1, TB + b"ValueError: \xd0\xb2\xd1\x8b\xd0\xb4\xd1\x83\xd0\xbc\xd0\xba\xd0\xb0\nnote from add_note\n", "ValueError"),
    # группа исключений 3.11+: все строки с отступом
    (1, b"  + Exception Group Traceback (most recent call last):\n  |   File \"x.py\", line 1\n"
        b"  | ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)\n", "ExceptionGroup"),
    (-9, b"2031-01-01 INFO token source: OAuth2\n", "signal_9"),
    (1, b"", "no_traceback"),
    (1, TB, "unparsed"),
])
def test_fault_detail_names_class_not_text(monkeypatch, rc, stderr, cls):
    faults = _run_job(monkeypatch, rc, stderr)
    assert faults == [(f"oura_refresh: import failed (exit {rc})", [cls])]


def test_detail_token_passes_bug_report_grammar():
    import bug_report
    for cls in ("URLError", "ExceptionGroup", "signal_9", "no_traceback", "unparsed"):
        assert bug_report._detail_token_ok(f"oura_refresh:{cls}"), cls
    assert not bug_report._detail_token_ok("oura_refresh:https://api.ouraring.com/x")


def test_success_is_silent(monkeypatch):
    assert _run_job(monkeypatch, 0, b"") == []
