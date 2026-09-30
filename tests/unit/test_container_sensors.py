"""Датчики в контейнере (docker-install, этап 2a): ритм, логи, тенанты и производители
читаются из плистов, отрендеренных из тех же шаблонов, что и расписание; датчик, который
зовёт launchctl, говорит «не судимо», а не «все живы». Краснеет, если датчик в контейнере
ослепнет молча или снова заведёт свой путь к ~/Library/LaunchAgents."""
import subprocess
import types
from datetime import datetime

import pytest

import daemon_liveness
import infra_config
import lab_intake_watcher
import plist_env_liveness
import producer_registry
import stderr_watch
from scripts import install

pytestmark = pytest.mark.unit


@pytest.fixture
def container(tmp_path, monkeypatch):
    """Окружение берётся из ОТРЕНДЕРЕННОГО .env, а не выставляется тестом: иначе рендер,
    забывший признак контейнера, прошёл бы зелёным (поймано мутацией 29.09)."""
    files = install.render_docker("Europe/Berlin")
    for name, text in files.items():
        if name.startswith("plists/"):
            (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / name).write_text(text, encoding="utf-8")
    env = dict(l.split("=", 1) for l in files[".env"].splitlines() if l and not l.startswith("#"))
    rendered_dir = env.get("HEALTH_LAUNCHAGENTS_DIR", "")
    assert rendered_dir == install.DOCKER_VALUES["REPO"] + "/build/docker/plists"
    monkeypatch.setenv("HEALTH_LAUNCHAGENTS_DIR", str(tmp_path / "plists"))   # тот же каталог, путь стенда
    if "HEALTH_RUNTIME" in env:
        monkeypatch.setenv("HEALTH_RUNTIME", env["HEALTH_RUNTIME"])
    else:
        monkeypatch.delenv("HEALTH_RUNTIME", raising=False)
    return tmp_path / "plists"


def test_ритм_и_логи_из_дома_расписания(container):
    assert plist_env_liveness.agents_dir() == container
    assert plist_env_liveness.last_scheduled_fire(
        "com.larry.health.night-cycle", datetime(2026, 9, 29, 12, 0)) == datetime(2026, 9, 29, 8, 0)
    assert plist_env_liveness.start_interval_s("com.larry.health.logrotate") == 1800
    errs = dict(stderr_watch._err_logs())
    assert str(errs["com.larry.health.import-watchdog"]) == "/home/health/health/logs/import_watchdog.err.log"
    assert "com.larry.health.code-watcher" not in errs          # хостовая служба в контейнере не живёт
    assert plist_env_liveness.tenants_served() == {"/home/health/health"}


def test_в_контейнере_владелец_остаётся_владельцем_и_канон_под_защитой(monkeypatch):
    """Этап 4: личность данных — имя каталога, предохранитель канона — от HOME. Раскладка
    контейнера обязана давать тот же ответ, что натив; своя (/data) ослепила бы оба."""
    from pathlib import Path
    import health_db
    import secrets_paths
    data = install.DOCKER_VALUES["DATA"]
    assert secrets_paths.is_owner_data(data)
    monkeypatch.setattr(Path, "home", lambda: Path(install.DOCKER_VALUES["HOME"]))
    canon = Path.home() / "health" / "data" / "health.db"
    assert canon == Path(data) / "data" / "health.db"
    assert health_db.CANONICAL_DB_PATH.name == canon.name   # тот же расчёт от HOME


def test_производители_и_приём_видны_в_контейнере(container):
    scheduled = producer_registry._scan_scheduled_labels(plist_env_liveness.agents_dir())
    assert "com.larry.health.night-cycle" in scheduled and "com.larry.health.bot" not in scheduled
    assert lab_intake_watcher.intake_jobs(), "вотчер приёма в контейнере есть — периметр не пуст"


def test_в_контейнере_живость_по_пульсу_служб(container, tmp_path, monkeypatch):
    """Этап 2б (⚰️ 2a: «в контейнере всегда NotJudged»). Периметр — постоянные службы из
    отрендеренных плистов (C-36); службы, ни разу не ударившей, — находка, а не тишина (C-44)."""
    data = tmp_path / "data"
    monkeypatch.setattr(daemon_liveness, "pulse_path",
                        lambda label, _d: data / "logs" / "pulse" / f"{label}.pulse")
    labels = {l for l, _ in daemon_liveness._container_services()}
    assert labels == {"com.larry.health.bot", "com.larry.health.dashboard",
                      "com.larry.health.lab-intake"}
    assert len(daemon_liveness.find_down_daemons()) == 3             # никто не ударил
    monkeypatch.setenv("HEALTH_DATA_DIR", str(data))
    for label in labels:
        monkeypatch.setenv(daemon_liveness.PULSE_LABEL_ENV, label)
        daemon_liveness.beat()
    assert daemon_liveness.find_down_daemons() == []
    import time as _t
    late = daemon_liveness.stale_pulses([(l, "") for l in labels],
                                        _t.time() + daemon_liveness.PULSE_STALE_S + 60)
    assert len(late) == 3 and all("пульс" in f and "назад" in f for f in late)


def test_compose_даёт_службе_её_метку():
    """Без метки служба в контейнере не бьёт пульс — и датчик увидел бы «ни разу» навсегда."""
    import yaml
    services = yaml.safe_load(install.render_docker("Europe/Berlin")["compose.yaml"])["services"]
    assert services["bot"]["environment"]["HEALTH_SERVICE_LABEL"] == "com.larry.health.bot"
    assert all(s.get("environment", {}).get("HEALTH_SERVICE_LABEL", "").endswith("." + n)
               for n, s in services.items() if n != "cron")


def test_натив_без_метки_пульс_не_пишет(tmp_path, monkeypatch):
    monkeypatch.delenv(daemon_liveness.PULSE_LABEL_ENV, raising=False)
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    daemon_liveness.beat()
    assert not (tmp_path / "logs").exists()


def test_в_контейнере_классов_launchd_нет(container):
    assert plist_env_liveness.find_stale_plist_env() == []      # класса нет по построению
    assert plist_env_liveness.repo_plist_drift() == {"drifted": [], "live_only": [], "repo_only": []}
    assert daemon_liveness.find_unrestarted_daemons() == []


def test_пустой_launchctl_не_значит_все_живы(monkeypatch):
    """Мина плана: вывод без наших меток (не тот домен launchd) прежде давал «все живы»."""
    monkeypatch.delenv("HEALTH_RUNTIME", raising=False)
    monkeypatch.setattr(infra_config, "is_primary", lambda: True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        stdout="PID\tStatus\tLabel\n123\t0\tcom.apple.Finder\n", returncode=0))
    with pytest.raises(daemon_liveness.NotJudged):
        daemon_liveness.find_down_daemons()
    assert daemon_liveness.has_our_labels("-\t1\tcom.larry.health.bot\n")


def test_порты_хоста_в_контейнере_не_судимы_а_не_чисты(monkeypatch, tmp_path):
    """Этап 4: из контейнера порты и Serve хоста не видны; пустой список читался бы как «чисто»."""
    import security_sensors
    monkeypatch.setenv("HEALTH_RUNTIME", "container")
    f = security_sensors.collect_security_findings(home=tmp_path, repo=tmp_path)
    for cat in ("listen_ports", "tailscale_exposure"):
        assert f[cat] and all("не судимо в контейнере" in x for x in f[cat]), (cat, f[cat])
