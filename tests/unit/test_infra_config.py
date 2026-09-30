"""Единый источник сетевых адресов (audit 2026-06-17, C3 infra_config).

Ловит возврат дубля адреса/порта в потребителях. Принцип #5: чистота + детект.
"""
import re
from pathlib import Path

import pytest

import infra_config

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent
CONSUMERS = ["dashboard.py", "jobs/scheduled.py", "handlers/meta.py", "check_contracts.py"]
# Шаблон адресов — из словаря pii_census (класс infra), не литералом: с 2026-09-23 адреса
# узлов владельца живут только в private/ (приватная зона), тест лежит в публичной.
import pii_census
ADDR = pii_census.pattern(["infra"])


@pytest.mark.owner_data
@pytest.mark.host_only
def test_constants_come_from_private_infra_file():
    import yaml
    d = yaml.safe_load((ROOT / "private" / "infra.yaml").read_text(encoding="utf-8"))
    assert infra_config.STUDIO_HOST == d["studio_host"]
    assert infra_config.STUDIO_SSH == d["studio_ssh"]
    assert infra_config.FUNNEL_URL == f"https://{d['tailnet_hostname']}"
    assert infra_config.DASHBOARD_PORT == 8001
    assert ADDR is not None and ADDR.search(infra_config.STUDIO_HOST), "словарь infra не знает адрес Studio"


def _code_lines(path: Path):
    """Строки кода без `#`-комментариев и без тройных кавычек (докстрингов)."""
    out, in_doc = [], False
    for ln in path.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if s.startswith("#"):
            continue
        if s.count('"""') == 1 or s.count("'''") == 1:
            in_doc = not in_doc
            continue
        if in_doc:
            continue
        out.append(ln)
    return out


@pytest.mark.owner_data
def test_no_address_literals_in_consumers():
    """Потребители держат адрес только через infra_config, не литералом в коде."""
    offenders = {}
    for rel in CONSUMERS:
        hits = [ln for ln in _code_lines(ROOT / rel)
                if ADDR.search(ln) and "infra_config" not in ln]
        if hits:
            offenders[rel] = hits
    assert not offenders, f"Литералы адреса вне infra_config: {offenders}"


@pytest.mark.owner_data
@pytest.mark.host_only
def test_detector_catches_violation():
    """Позитив (#5): инъекция литерала адреса обязана ловиться."""
    bad = f'    uvicorn.run(app, host="{infra_config.STUDIO_HOST}", port=8001)'
    assert ADDR.search(bad)


def test_consumers_reference_infra_config():
    for rel in CONSUMERS:
        txt = (ROOT / rel).read_text(encoding="utf-8")
        assert "infra_config" in txt, f"{rel} должен использовать infra_config"


def test_dashboard_binds_via_infra_config():
    """dashboard.py биндит через infra_config.STUDIO_HOST/DASHBOARD_PORT (проверка ссылки)."""
    txt = (ROOT / "dashboard.py").read_text(encoding="utf-8")
    assert "infra_config.STUDIO_HOST" in txt
    assert "infra_config.DASHBOARD_PORT" in txt


@pytest.mark.parametrize("config", [None, {}, {"primary_host": "example-host"}, {"neighbors": {}}])
def test_fresh_infra_has_no_neighbors(tmp_path, config):
    """Настоящий импорт из свежего корня, без приватного конфига машины."""
    import shutil
    import subprocess
    import sys
    import yaml
    shutil.copy(ROOT / "infra_config.py", tmp_path)
    if config is not None:
        (tmp_path / "private").mkdir()
        (tmp_path / "private" / "infra.yaml").write_text(yaml.safe_dump(config))
    r = subprocess.run([sys.executable, "-c", "import infra_config; assert infra_config.NEIGHBORS == {}"],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_neighbor_config_resolves_existing_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    n = infra_config._neighbors({
        "crm": {"path": "~/crm", "db": "crm.db", "label_prefix": "org.example.crm",
                "secrets": "~/.crm_secrets"},
        "lab-notes": {"path": "~/Projects/lab-notes", "lessons": "lessons.yaml"},
    })
    assert n["crm"]["path"] == tmp_path / "crm"
    assert n["crm"]["db"] == tmp_path / "crm" / "crm.db"
    assert n["crm"]["label_prefix"] == "org.example.crm"
    assert n["crm"]["secrets"] == tmp_path / ".crm_secrets"
    assert n["lab-notes"]["lessons"] == tmp_path / "Projects" / "lab-notes" / "lessons.yaml"


@pytest.mark.parametrize("bad", [[], {"crm": {}}, {"crm": {"path": "relative"}},
                                  {"crm": {"path": "/example\nother"}},
                                  {"crm": {"path": "/example", "label_prefix": ""}},
                                  {"crm": {"path": "/example", "lable_prefix": "org.example.crm"}},
                                  {"crm": {"path": "/example", "lessons": "other.yaml"}}])
def test_bad_neighbors_fail_loudly(bad):
    with pytest.raises(ValueError, match="neighbors"):
        infra_config._neighbors(bad)
