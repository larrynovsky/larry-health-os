"""producer_registry в контейнере владельца: каталог плистов — только службы с местом
`container` (placement.yaml), без хостовых и партнёрских. Их ключи — не «реестр протух»
(карточка warn:producer_registry, 10 ложных ключей каждую ночь). Настоящий протухший
ключ контейнерной службы обязан краснеть по-прежнему (негативный контроль)."""

import re
from pathlib import Path

import yaml

import producer_registry as pr

TPL = Path(pr.__file__).with_name("templates") / "launchd"


def _container_plists(tmp_path, drop=()):
    """Каталог, как его рендерит scripts/install.py --docker: плист на каждую службу с
    местом container; расписание — то, что объявляет шаблон."""
    d = tmp_path / "plists"
    d.mkdir()
    placement = yaml.safe_load((TPL / "placement.yaml").read_text(encoding="utf-8"))["services"]
    for label, where in placement.items():
        if where != "container" or label in drop:
            continue
        text = (TPL / f"{label}.plist.tmpl").read_text(encoding="utf-8")
        sched = re.search(r"StartCalendarInterval|StartInterval", text)
        key = sched.group(0) if sched else "KeepAlive"
        (d / f"{label}.plist").write_text(
            f"<key>Label</key><string>{label}</string><key>{key}</key>", encoding="utf-8")
    return d


def _container(monkeypatch, d):
    monkeypatch.setenv("HEALTH_RUNTIME", "container")
    monkeypatch.setenv("HEALTH_LAUNCHAGENTS_DIR", str(d))


def test_container_host_and_partner_keys_not_stale(tmp_path, monkeypatch):
    _container(monkeypatch, _container_plists(tmp_path))
    found = pr.collect_producer_findings()
    assert not [f for f in found if "протух" in f], found


def test_container_real_stale_key_still_flagged(tmp_path, monkeypatch):
    _container(monkeypatch, _container_plists(tmp_path, drop={"com.larry.health.backup"}))
    stale = [f for f in pr.collect_producer_findings() if "протух" in f]
    assert len(stale) == 1 and "com.larry.health.backup" in stale[0]
    assert "machine-check" not in stale[0] and ".partner" not in stale[0]


def test_container_only_import_watchdog_left_unclassified(tmp_path, monkeypatch):
    """Ревью 721aefad, п.2: constitutions классифицирована (датчик с 25.09), единственная
    ожидаемая находка «без датчика» в контейнере — import-watchdog (решение — следующей сессии).
    Снять constitutions из MONITORED → здесь красное, а не только в ночной строке."""
    _container(monkeypatch, _container_plists(tmp_path))
    orphans = [f for f in pr.collect_producer_findings() if "без датчика" in f]
    assert len(orphans) == 1 and "com.larry.health.import-watchdog" in orphans[0], orphans
    assert "com.larry.health.constitutions" not in orphans[0]


def test_unknown_placement_is_an_error_not_a_foreign_service():
    """Ревью 721aefad, п.1: опечатка места или null не делает службу «чужой» — иначе контроль
    протухания снимается молча. Ошибка громкая; законные host:/none: — чужие."""
    import pytest
    cls = {"a", "b"}
    for bad in ("contianer", None, "host:без пробела"):
        with pytest.raises(ValueError):
            pr.foreign_labels(cls, {"a": bad})
    assert pr.foreign_labels(cls, {"a": "host: x", "b": "container"}) == {"a"}
    assert pr.foreign_labels(cls | {"c.partner"}, {"a": "none: y"}) == {"a", "c.partner"}
