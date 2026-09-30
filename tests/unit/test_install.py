"""Установщик (scripts/install.py): раскладывает шаблоны, ничего существующего не трогает,
каталог секретов — 700. Краснеет, если установщик начнёт затирать данные тенанта (у владельца
установка обязана быть пустым прогоном) или оставит секреты открытыми."""
import pathlib
import stat

import pytest

from scripts import install

pytestmark = pytest.mark.unit
_V = {"PRIMARY_HOST": "zz-host", "REPO": "/Users/zz/health_scripts", "HOME": "/Users/zz",
      "USER": "zz", "DATA": "/Users/zz/health", "SECRETS": "/Users/zz/.health_secrets", "TZ": "UTC"}


def test_свежая_машина_получает_шаблоны_и_закрытые_секреты(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "ROOT", tmp_path / "repo")
    steps = install.plan_install(tmp_path / "data", tmp_path / "sec", _V)
    assert {a for a, _, _ in steps} == {"write", "mkdir"}
    install.apply_install(steps, tmp_path / "sec")
    infra = (tmp_path / "repo" / "private" / "infra.yaml").read_text(encoding="utf-8")
    assert "primary_host: zz-host" in infra
    assert stat.S_IMODE((tmp_path / "sec").stat().st_mode) == 0o700
    assert (tmp_path / "repo" / "methodology" / "clinical_kb" / "_index.yaml").exists()


def test_существующее_не_затирается(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "ROOT", tmp_path / "repo")
    mine = tmp_path / "repo" / "private" / "infra.yaml"
    mine.parent.mkdir(parents=True)
    mine.write_text("primary_host: owner-box\n", encoding="utf-8")
    steps = install.plan_install(tmp_path / "data", tmp_path / "sec", _V)
    assert ("keep", mine, None) in steps
    install.apply_install(steps, tmp_path / "sec")
    assert mine.read_text(encoding="utf-8") == "primary_host: owner-box\n"
    again = install.plan_install(tmp_path / "data", tmp_path / "sec", _V)
    assert {a for a, _, _ in again} == {"keep"}          # повторный запуск — пустой прогон


@pytest.mark.parametrize("owner_copy", [True, False])
def test_food_catalog_owner_copy_kept_and_fresh_template_seeded(tmp_path, monkeypatch, owner_copy):
    """Установка бережёт личный свод; сид читает methodology, свежая копия — шаблон."""
    import json
    import sqlite3
    import yaml
    import clinical_kb

    root = tmp_path / "repo"
    kb_dir = root / "methodology/clinical_kb"
    target = kb_dir / "food.yaml"
    owner_body = "shared:\n  benefits:\n  - food: личный продукт\n    tag: owner_tag\n    why: личная причина\nby_condition: {}\n"
    if owner_copy:
        kb_dir.mkdir(parents=True)
        target.write_text(owner_body)
    monkeypatch.setattr(install, "ROOT", root)
    steps = install.plan_install(tmp_path / "data", tmp_path / "sec", _V)
    assert next(a for a, p, _ in steps if p == target) == ("keep" if owner_copy else "write")
    install.apply_install(steps, tmp_path / "sec")
    expected = owner_body if owner_copy else (install.TPL / "methodology/clinical_kb/food.yaml").read_text()
    assert target.read_text() == expected
    # Пустой синтетический индекс исключает состояния реального владельца.
    (kb_dir / "_index.yaml").write_text("conditions: []\n")
    assert clinical_kb._KB_DIR == pathlib.Path(clinical_kb.__file__).parent / "methodology/clinical_kb"
    monkeypatch.setattr(clinical_kb, "_KB_DIR", kb_dir)
    monkeypatch.setattr(clinical_kb, "_INDEX_YAML", kb_dir / "_index.yaml")
    clinical_kb._load_conditions.cache_clear()
    clinical_kb._load_domain_entries.cache_clear()
    try:
        with sqlite3.connect(":memory:") as conn:
            clinical_kb.seed_clinical_kb(conn)
            payloads = [json.loads(r[0]) for r in conn.execute("SELECT payload_json FROM clinical_kb WHERE domain='food'")]
        benefits = yaml.safe_load(expected)["shared"]["benefits"]
        assert {p["food"]: (p["tag"], p["why"]) for p in payloads} == {
            b["food"]: (b["tag"], b["why"]) for b in benefits}
    finally:
        clinical_kb._load_conditions.cache_clear()
        clinical_kb._load_domain_entries.cache_clear()


def test_пустой_словарь_шаблона_не_совпадает_со_всем(tmp_path):
    """Пустой класс в словаре давал шаблон "" — совпадение в КАЖДОЙ позиции файла: у свежей
    установки перепись нашла бы миллионы «личных слов» и заблокировала первый же коммит."""
    import shutil
    import pii_census
    (tmp_path / "private").mkdir()
    shutil.copy(install.TPL / "private" / "pii_terms.yaml", tmp_path / "private" / "pii_terms.yaml")
    assert pii_census._terms(tmp_path) == {}


def test_плисты_собираются_и_незаполненное_отказ():
    vals = {"REPO": "/Users/zz/health_scripts", "HOME": "/Users/zz", "DATA": "/Users/zz/health",
            "SECRETS": "/Users/zz/.health_secrets", "TZ": "UTC"}
    out = install.render_launchd(vals)
    assert "com.larry.health.bot.plist" in out and all("{{" not in t for t in out.values())
    with pytest.raises(ValueError):
        install.render_launchd({k: v for k, v in vals.items() if k != "DATA"})


@pytest.mark.host_only
def test_записанное_установщиком_скрыто_от_git_копии(tmp_path, monkeypatch):
    """Посторонний после установки не унесёт свой словарь и конфиг в коммит (приёмка урока 24.09)."""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.setattr(install, "ROOT", repo)
    steps = install.plan_install(tmp_path / "data", tmp_path / "sec", _V)
    install.apply_install(steps, tmp_path / "sec")
    hidden = install.exclude_from_git([p for a, p, _ in steps if a == "write"])
    assert "/private/infra.yaml" in hidden
    st = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
                        capture_output=True, text=True, check=True).stdout
    assert "private/" not in st and "methodology/" not in st, st
    assert install.exclude_from_git([p for a, p, _ in steps if a == "write"]) == []   # повтор не дублирует


def test_база_установки_только_владельцу(tmp_path):
    """security:db_perms требует 600; установщик не оставляет базу под umask (приёмка урока 24.09)."""
    db = tmp_path / "data" / "health.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"")
    db.chmod(0o644)
    install.secure_db(tmp_path)
    assert stat.S_IMODE(db.stat().st_mode) == 0o600


# ── Второй человек (--tenant, 28.09) ─────────────────────────────────────────
_T = {**_V, "DATA": "/Users/zz/health_t2", "SECRETS": "/Users/zz/.health_secrets_t2",
      "TZ": "Europe/Berlin"}


def _tenant_plists():
    import plistlib
    return {n: plistlib.loads(b) for n, b in install.render_tenant(_T, "t2", 8002).items()}


def test_человек_получает_свои_метки_данные_секреты_логи():
    """Своя метка (иначе bootstrap заменит службу владельца), свои данные и секреты
    (инцидент 07-03: секреты владельца у партнёра), свои логи, порт дашборда не 8001."""
    pl = _tenant_plists()
    owner_labels = {n.removesuffix(".plist") for n in install.render_launchd(_V)}
    ready = [s for s, st in install.tenant_services().items() if st == "ready"]
    assert len(pl) == len(ready) and "com.larry.health.bot.t2.plist" in pl
    logs = []
    for name, p in pl.items():
        assert name == p["Label"] + ".plist" and p["Label"] not in owner_labels
        env = p["EnvironmentVariables"]
        assert (env["HEALTH_DATA_DIR"], env["HEALTH_SECRETS_DIR"], env["HEALTH_TZ"]) == (
            _T["DATA"], _T["SECRETS"], "Europe/Berlin"), name
        text = repr(p)
        assert "/Users/zz/health'" not in text and ".health_secrets'" not in text, name
        logs += [v for k, v in p.items() if k.startswith("Standard")]
    assert all(v.endswith("_t2.log") for v in logs) and logs
    assert pl["com.larry.health.dashboard.t2.plist"]["EnvironmentVariables"]["DASHBOARD_PORT"] == "8002"


def test_логи_в_командной_строке_тоже_свои():
    """Логи, куда пишет bash -c (oura, конституции), тоже расходятся по людям."""
    args = " ".join(_tenant_plists()["com.larry.health.oura-import.t2.plist"]["ProgramArguments"])
    assert "health_oura_import_t2.log" in args and "health_oura_import.log" not in args


def test_служба_с_общим_артефактом_не_рендерится():
    """blocked в tenant_services.yaml — не копируется: общий файл затирал бы чужой результат."""
    states = install.tenant_services()
    blocked = [s for s, st in states.items() if st != "ready"]
    assert "triage" in blocked and "watcher" in blocked
    assert all(states[s].startswith(("blocked: ", "excluded: ")) for s in blocked), \
        "не-ready обязано назвать причину: blocked: … или excluded: …"
    names = set(_tenant_plists())
    assert not any(f"com.larry.health.{s}.t2.plist" in names for s in blocked)


def test_каждая_служба_человека_имеет_шаблон():
    have = set(install.render_launchd(_V))
    missing = [s for s in install.tenant_services() if f"com.larry.health.{s}.plist" not in have]
    assert not missing, missing


def test_проверка_видит_плист_без_своих_секретов(monkeypatch):
    """Негативный контроль: рендер, забывший секреты человека, краснеет в тесте выше."""
    real = install.render_tenant

    def broken(values, tenant, port):
        return real({**values, "SECRETS": _V["SECRETS"]}, tenant, port)
    monkeypatch.setattr(install, "render_tenant", broken)
    with pytest.raises(AssertionError):
        test_человек_получает_свои_метки_данные_секреты_логи()


def test_логи_собранных_плистов_попадают_под_ротацию(tmp_path):
    """28.09.2026: 27 логов партнёра росли без ротации — установщик собирал плисты, а
    рукописный конфиг о них не знал. Теперь cover дописывает; повтор ничего не дублирует."""
    import log_rotate
    conf = tmp_path / "rot.conf"
    conf.write_text("/Users/zz/health_bot.log  zz:staff 644 4 5000 * J\n", encoding="utf-8")
    texts = [b.decode("utf-8") for b in install.render_tenant(_T, "t2", 8002).values()]
    declared = {pathlib.Path(p) for t in texts for p in log_rotate.DECLARED.findall(t)}
    assert declared and all(str(p).endswith("_t2.log") for p in declared)
    added = log_rotate.cover(texts, conf, "zz")
    assert set(map(pathlib.Path, added)) == declared
    assert declared <= {r["path"] for r in log_rotate._parse_conf(conf)}
    assert log_rotate.cover(texts, conf, "zz") == []
