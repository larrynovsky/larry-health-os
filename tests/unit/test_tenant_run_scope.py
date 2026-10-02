"""Прогон тенанта на хосте не судит заморожённую копию переехавшего владельца (нить tenant-run-scope).

Замер 02.10: после переезда владельца в контейнер (30.09, метка ~/health/RUNTIME=container)
ночной прогон партнёра на хосте каждое утро слал оператору 3 FAIL и ~10 WARN про копию
владельца, которую больше никто не пишет: «рельса доставки мертва», «бэкап 52ч», «проба
красная», «календарь 50ч», «ночной цикл/колокол молчат». Живые следы владельца судит
монитор в контейнере. Здесь — предикаты, на которых держится отсечение.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import secrets_paths as sp  # noqa: E402


def _tenant(home: Path, name: str, runtime: str | None = None) -> Path:
    root = home / name
    (root / "data").mkdir(parents=True)
    (root / "data" / "health.db").write_bytes(b"")
    if runtime is not None:
        (root / sp.RUNTIME_MARK).write_text(runtime + "\n", encoding="utf-8")
    return root


def test_moved_root_is_not_a_patient(tmp_path, monkeypatch):
    monkeypatch.setattr(sp.Path, "home", staticmethod(lambda: tmp_path))
    _tenant(tmp_path, "health", "container")
    partner = _tenant(tmp_path, "health_partner")
    assert sp.tenant_db_paths() == [(partner / "data" / "health.db").resolve()]


def test_native_marker_keeps_the_tenant(tmp_path, monkeypatch):
    """Только слово container отсекает; любая другая метка — тенант на месте."""
    monkeypatch.setattr(sp.Path, "home", staticmethod(lambda: tmp_path))
    owner = _tenant(tmp_path, "health", "native")
    assert sp.tenant_db_paths() == [(owner / "data" / "health.db").resolve()]


def test_current_db_survives_its_own_marker(tmp_path, monkeypatch):
    """Явно переданная БД процесса остаётся: отсечение — для обхода диска, не для себя."""
    monkeypatch.setattr(sp.Path, "home", staticmethod(lambda: tmp_path))
    owner = _tenant(tmp_path, "health", "container")
    cur = owner / "data" / "health.db"
    assert sp.tenant_db_paths(current=cur) == [cur.resolve()]


def test_owner_runs_elsewhere_only_for_a_host_tenant_run(tmp_path, monkeypatch):
    monkeypatch.setattr(sp.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv(sp.OWNER_ROOT_ENV, raising=False)     # судим настоящий путь ~/health
    _tenant(tmp_path, "health", "container")
    monkeypatch.delenv("HEALTH_RUNTIME", raising=False)

    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health_partner"))
    assert sp.owner_runs_elsewhere() is True

    # прогон на данных владельца (и его клоне) судит свои следы сам
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health_staging"))
    assert sp.owner_runs_elsewhere() is False

    # внутри контейнера метки хоста нет смысла читать
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health_partner"))
    monkeypatch.setenv("HEALTH_RUNTIME", "container")
    assert sp.owner_runs_elsewhere() is False


def test_owner_still_native_is_judged_here(tmp_path, monkeypatch):
    monkeypatch.setattr(sp.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv(sp.OWNER_ROOT_ENV, raising=False)
    _tenant(tmp_path, "health")
    monkeypatch.delenv("HEALTH_RUNTIME", raising=False)
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health_partner"))
    assert sp.owner_runs_elsewhere() is False


def test_owner_trace_sensors_ask_the_predicate():
    """Шесть датчиков следов владельца спрашивают предикат. Импорт integrity_tests исполняет
    монитор целиком, поэтому проверка — по исходнику: удалённый вопрос краснеет здесь."""
    src = (Path(__file__).resolve().parents[2] / "integrity_tests.py").read_text(encoding="utf-8")
    import ast
    tree = ast.parse(src)
    asks = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
            and "_owner_judged_in_container" in ast.unparse(n)}
    assert {"check_triage_delivery_liveness", "check_probe_liveness",
            "check_nightly_suite_liveness", "check_night_cycle_liveness",
            "check_doorbell_liveness"} <= asks, asks
    pulse = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "check_lab_intake_pulse")
    assert "moved_to_container" in ast.unparse(pulse)
