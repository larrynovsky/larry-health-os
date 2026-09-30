"""Облачный путь установки — один дом (infra_config), BL-PUB-12, нить publication-tails 24.09.

Оракулы: (1) нет облака → cloud_dir ведёт в каталог данных процесса, не в чужой тенант;
(2) не основная машина без HEALTH_DATA_DIR и без облака — громкий отказ, а не случайный каталог
(data_ingestion.no_silent_stale_replica); (3) проба чистого HOME: импорт модулей, трогающих
облако и домашнюю папку, не оставляет в HOME ничего, кроме каталога данных."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MODULES = ["health_db", "lab_intake_watcher", "import_medical_events", "import_all", "lab_backfill",
           "lab_extractor", "import_fitdays", "import_apple_health", "import_oura",
           "consult_prep", "hae_checker", "vcf_import_pipeline"]


def test_no_cloud_falls_back_to_own_data_dir(monkeypatch, tmp_path):
    import infra_config
    monkeypatch.setattr(infra_config, "CLOUD_HEALTH_DIR", None)
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "health_partner"))
    assert infra_config.cloud_dir("CR") == tmp_path / "health_partner" / "CR"


def test_cloud_set_is_used(monkeypatch, tmp_path):
    import infra_config
    monkeypatch.setattr(infra_config, "CLOUD_HEALTH_DIR", tmp_path / "cloud")
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path / "other"))
    assert infra_config.cloud_dir("data", "reports") == tmp_path / "cloud" / "data" / "reports"


def test_non_primary_without_cloud_is_loud(monkeypatch):
    import health_db
    monkeypatch.delenv("HEALTH_DATA_DIR", raising=False)
    monkeypatch.delenv("HEALTH_MULTITENANT", raising=False)
    monkeypatch.setattr(health_db, "_ICLOUD_DEFAULT", None)
    monkeypatch.setattr(health_db._infra, "is_primary", lambda *a: False)
    with pytest.raises(RuntimeError, match="не основная"):
        health_db._resolve_health_dir()


def test_clean_home_import_leaves_nothing_but_data(tmp_path):
    home = tmp_path / "home"
    data = home / "health"
    (data / "data").mkdir(parents=True)
    import site           # пакеты, поставленные --user, живут от НАСТОЯЩЕГО HOME — сохраняем их
    env = {**os.environ, "HOME": str(home), "HEALTH_DATA_DIR": str(data),
           "PYTHONPATH": str(REPO), "PYTHONUSERBASE": site.getuserbase()}
    code = "import importlib,logging;logging.disable(50)\n" + "".join(
        f"importlib.import_module({m!r})\n" for m in MODULES)
    r = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    # Законно в HOME: каталог данных и ОБЪЯВЛЕННЫЕ логи установки — те, что ротирует шаблон
    # newsyslog (их же пишут плисты из шаблонов). Всё прочее — мусор у постороннего.
    tmpl = (REPO / "templates/launchd/health-logs.newsyslog.conf.tmpl").read_text(encoding="utf-8")
    declared = {ln.split()[0].removeprefix("{{HOME}}/") for ln in tmpl.splitlines()
                if ln.startswith("{{HOME}}/")}
    made = sorted(str(p.relative_to(home)) for p in home.rglob("*")
                  if p.relative_to(home).parts[0] != "health"      # не startswith: ~/health_data — чужое
                  and str(p.relative_to(home)) not in declared)
    assert not made, f"импорт оставил в домашней папке постороннего: {made}"
