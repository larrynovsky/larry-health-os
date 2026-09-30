"""Tenant routing with fake DB/osascript boundaries and a temporary home."""

import importlib.util
import logging
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def sync(monkeypatch, tmp_path, request):
    data_dir = tmp_path / getattr(request, "param", "health_demo_alpha")
    monkeypatch.setenv("HEALTH_DATA_DIR", str(data_dir))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # The service changes sys.path on import; keep that change local to this test.
    monkeypatch.setattr(sys, "path", list(sys.path))

    db = ModuleType("health_db")
    db._resolve_health_dir = Mock(side_effect=lambda: os.environ["HEALTH_DATA_DIR"])
    db.init_db = Mock()
    db.get_open_tasks = Mock(return_value=[])
    db.resolve_task = Mock(return_value=True)
    monkeypatch.setitem(sys.modules, "health_db", db)

    # Intercept every external boundary before importing the service.
    run = Mock(side_effect=AssertionError("Real AppleScript must never run"))
    monkeypatch.setattr(subprocess, "run", run)
    file_handler = Mock(return_value=logging.NullHandler())
    monkeypatch.setattr(logging, "FileHandler", file_handler)
    monkeypatch.setattr(logging, "basicConfig", Mock())

    source = Path(__file__).resolve().parents[2] / "reminders_sync.py"
    spec = importlib.util.spec_from_file_location("_tenant_reminders_sync", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return SimpleNamespace(module=module, db=db, home=tmp_path,
                           data_dir=data_dir, file_handler=file_handler, run=run)


@pytest.mark.parametrize("sync", ["health", "health_staging"], indirect=True)
def test_owner_keeps_list_and_log(sync):
    assert sync.module.reminders_list_name() == "Health"
    expected = sync.home / "health_reminders_sync.log"
    assert sync.module._log_path() == expected
    sync.file_handler.assert_called_once_with(expected)


@pytest.mark.parametrize(
    "sync, expected_list",
    [("health_demo_alpha", "Health (demo_alpha)"),
     ("health_demo_beta", "Health (demo_beta)"),
     ("sandbox", "Health (sandbox)")],
    indirect=["sync"],
)
def test_tenant_list_and_log(sync, expected_list):
    assert sync.module.reminders_list_name() == expected_list
    expected = sync.data_dir / "logs" / "health_reminders_sync.log"
    assert sync.module._log_path() == expected
    assert expected.is_relative_to(sync.data_dir)
    assert expected.parent.is_dir()
    sync.file_handler.assert_called_once_with(expected)
    sync.db._resolve_health_dir.assert_called()


def test_two_tenants_have_distinct_artifacts(sync, monkeypatch):
    first_list = sync.module.reminders_list_name()
    first_log = sync.module._log_path()
    monkeypatch.setenv("HEALTH_DATA_DIR", str(sync.home / "health_demo_beta"))
    assert sync.module.reminders_list_name() == "Health (demo_beta)"
    assert sync.module.reminders_list_name() != first_list
    assert sync.module._log_path() != first_log


@pytest.mark.parametrize(
    "sync, expected_list, completed_id",
    [("health", "Health", 41),
     ("health_demo_alpha", "Health (demo_alpha)", 17),
     ("health_demo_beta", "Health (demo_beta)", 23)],
    indirect=["sync"],
)
def test_sync_reads_only_selected_list(sync, expected_list, completed_id):
    # IDs overlap between databases; only the list establishes ownership.
    sync.db.get_open_tasks.return_value = [
        {"id": task_id, "type": "action"} for task_id in (41, 17, 23)
    ]

    def run(args, **kwargs):
        assert args[:2] == ["osascript", "-e"]
        assert f'set rl to list "{expected_list}"' in args[2]
        return subprocess.CompletedProcess(
            args, 0, stdout=f"[task_id:{completed_id}]", stderr=""
        )

    sync.run.side_effect = run
    sync.module.sync_completed_reminders()
    sync.run.assert_called_once()
    sync.db.resolve_task.assert_called_once_with(
        completed_id, "completed via Reminders.app", "completed"
    )


def test_missing_tenant_list_never_falls_back_to_owner(sync):
    sync.run.side_effect = None
    sync.run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    sync.module.sync_completed_reminders()
    sync.run.assert_called_once()
    script = sync.run.call_args.args[0][2]
    assert 'set rl to list "Health (demo_alpha)"' in script
    assert 'list "Health"' not in script
    sync.db.get_open_tasks.assert_not_called()
    sync.db.resolve_task.assert_not_called()


@pytest.mark.parametrize("sync", ['health_demo_"quoted"\\folder'], indirect=True)
def test_list_name_is_escaped_in_applescript(sync):
    sync.run.side_effect = None
    sync.run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    sync.module.get_completed_task_ids()
    script = sync.run.call_args.args[0][2]
    assert r'set rl to list "Health (demo_\"quoted\"\\folder)"' in script


def test_writer_puts_tenant_task_into_tenant_list(monkeypatch):
    """Писатель (task_agent) и читатель (reminders_sync) — один список: иначе задача
    человека уходит в список владельца, а синк человека её никогда не увидит."""
    import task_agent
    import reminders_sync
    monkeypatch.setattr(reminders_sync, "reminders_list_name", lambda: "Health (demo_alpha)")
    seen = []

    def run(args, **kwargs):
        seen.append(args[2])
        return subprocess.CompletedProcess(args, 0, stdout="OK", stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    task_agent.create_macos_reminder({"id": 7, "content": "t", "priority": "low", "type": "action"})
    task_agent.complete_macos_reminder(7)
    assert seen and all('list "Health (demo_alpha)"' in s for s in seen)
    assert not any('list "Health"' in s for s in seen)


def test_непрочитанный_ящик_это_сбой_а_не_пустота(sync, monkeypatch):
    """Замер 30.09: нативный синк с 08.08 по 30.09 падал по таймауту osascript (все 422 прогона) и отвечал
    «No completed reminders» — галочки на iPhone восемь недель не закрывали задач, знал только лог.
    Сбой чтения обязан лечь в журнал сбоев (его читает ночной монитор), а не притвориться пустотой."""
    faults = []
    monkeypatch.setitem(sys.modules, "notify",
                        SimpleNamespace(fault=lambda tech, person_key="x": faults.append((tech, person_key))))
    sync.run.side_effect = subprocess.TimeoutExpired(["osascript"], 30)
    assert sync.module.get_completed_task_ids() == []
    assert len(faults) == 1 and "ящик напоминаний не прочитан" in faults[0][0] and faults[0][1] is None
    faults.clear()
    sync.run.side_effect = None
    sync.run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    assert sync.module.get_completed_task_ids() == [] and faults == []    # честно пусто — тишина
