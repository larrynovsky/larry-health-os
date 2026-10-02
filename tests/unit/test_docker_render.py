"""Рендер расписания для контейнера (scripts/install.py --docker, нить docker-install, этап 1).
Краснеет, если служба молча пропадает из контейнера (нет места в placement.yaml), если в
контейнер протекают пути Мака, если часы теряют пояс или расписание меняет частоту."""
import pytest

from scripts import install

pytestmark = pytest.mark.unit
_V = {**install.DOCKER_VALUES, "PRIMARY_HOST": "h", "TZ": "Europe/Berlin"}


@pytest.mark.parametrize("image", ["health-os:local", "ghcr.io/example/health:v1",
                                  "ghcr.io/example/health@sha256:" + "a" * 64])
def test_образ_у_каждой_службы_включая_cron(image):
    import yaml
    compose = yaml.safe_load(install.render_docker("UTC", image=image)["compose.yaml"])
    assert compose["services"] and all(s["image"] == image for s in compose["services"].values())
    default = yaml.safe_load(install.render_docker("UTC")["compose.yaml"])
    assert all(s["image"] == "health-os:local" for s in default["services"].values())


@pytest.mark.parametrize("image", ["", " ", "a b", "a\tb", "a\nb", "a\u00a0b"])
def test_пустой_образ_или_пробел_отказ_рендера_и_cli(image):
    with pytest.raises(ValueError, match="--image"):
        install.render_docker("UTC", image=image)
    with pytest.raises(SystemExit) as e:
        install.main(["--docker", "--tz", "UTC", "--image", image])
    assert e.value.code == 2


def test_cli_передаёт_образ_и_печатает_общий_итог(tmp_path, monkeypatch, capsys):
    import yaml
    (tmp_path / "publication_zones.yaml").write_text((install.ROOT / "publication_zones.yaml").read_text())
    monkeypatch.setattr(install, "ROOT", tmp_path)
    assert install.main(["--docker", "--tz", "UTC", "--image", "ghcr.io/example/health:v1"]) == 0
    compose = yaml.safe_load((tmp_path / "build/docker/compose.yaml").read_text())
    assert all(s["image"] == "ghcr.io/example/health:v1" for s in compose["services"].values())
    line = next(l for l in capsys.readouterr().out.splitlines() if l.startswith("собрано в "))
    assert line.split(": ", 1)[1] == install.docker_summary("UTC")
    with pytest.raises(SystemExit) as e:
        install.main(["--image", "health-os:local"])
    assert e.value.code == 2


def test_facts_без_окружения_и_значений_пояса(monkeypatch):
    import json
    from pathlib import Path
    facts = install.install_facts()
    def unexpected(*args):
        raise AssertionError("facts обратились к машине")
    monkeypatch.setattr(Path, "home", unexpected)
    monkeypatch.setattr(install.socket, "gethostname", unexpected)
    monkeypatch.setenv("HEALTH_TZ", "Pacific/Auckland")
    monkeypatch.setenv("HEALTH_DATA_DIR", "/Users/foreign/health")
    assert install.install_facts("Europe/Berlin") == facts == install.install_facts()
    assert [s["name"] for s in facts["services"]] == ["bot", "cron", "dashboard", "ingest", "lab-intake"]
    assert facts["secrets"]["required"] == ["telegram_chat_id", "telegram_token"]
    assert "anthropic_key" in facts["secrets"]["optional"]
    assert facts["ocr_langs"] == "eng rus"
    assert facts["cli_flags"] == ["--docker", "--image", "--tz"]
    assert "HEALTH_TZ" in facts["env_keys"]
    assert "/Users/" not in json.dumps(facts) and "Europe/Berlin" not in json.dumps(facts)
    assert install.install_facts_hash(facts) == install.install_facts_hash(dict(reversed(list(facts.items()))))
    assert install.install_facts_hash(facts) != install.install_facts_hash(install.install_facts(image="other:v1"))


def test_итог_считает_каждое_место_и_причины_исключений(monkeypatch):
    """Независимый численный оракул, чтобы общая функция не скрыла ошибку печати и facts."""
    plan = {str(i): (state, {}) for i, state in enumerate(
        ("cron", "cron", "service", "env", "host: нужен хост", "none: не ставится"))}
    monkeypatch.setattr(install, "docker_plan", lambda values: plan)
    assert install.docker_summary() == "cron 2, service 1, env 1, host 1, none 1 (всего 6)"


def test_facts_читают_источники_а_не_вторую_копию(tmp_path, monkeypatch):
    """Подменяем данные рендера и исходный код: новый порт, env, OCR и секрет попадают в facts."""
    import yaml
    baseline = install.install_facts()
    rendered = install.render_docker("UTC")
    compose = yaml.safe_load(rendered["compose.yaml"])
    compose["services"]["dashboard"]["ports"] = ["127.0.0.1:8888:8001"]
    compose["services"]["bot"]["restart"] = "always"
    rendered["compose.yaml"] = yaml.safe_dump(compose)
    rendered[".env"] += "NEW_INSTALL_KEY=value\n"
    (tmp_path / "bot").mkdir()
    (tmp_path / "docker").mkdir()
    (tmp_path / "bot/filters.py").write_text('TOKEN_FILE = directory / "new_token"\nCHAT_ID_FILE = directory / "new_chat"\n')
    (tmp_path / "docker/Dockerfile").write_text('ARG OCR_LANGS="eng deu" # комментарий\n')
    monkeypatch.setattr(install, "ROOT", tmp_path)
    monkeypatch.setattr(install, "render_docker", lambda *a, **kw: rendered)
    moved = install.install_facts()
    assert moved["ocr_langs"] == "eng deu"
    assert moved["secrets"]["required"] == ["new_chat", "new_token"]
    assert "NEW_INSTALL_KEY" in moved["env_keys"]
    svc = {s["name"]: s for s in moved["services"]}
    assert svc["dashboard"]["ports"] == ["127.0.0.1:8888:8001"]
    assert svc["bot"]["restart"] == "always"
    assert moved["volumes"] == ["health-applogs", "health-home"]
    assert all(s["volumes"] == moved["volumes"] for s in moved["services"])
    assert install.install_facts_hash(moved) != install.install_facts_hash(baseline)
    (tmp_path / "docker/Dockerfile").write_text("FROM x\n")
    with pytest.raises(ValueError, match="OCR_LANGS"):
        install.install_facts()


def test_у_каждого_шаблона_ровно_одно_место_и_сумма_сходится():
    plan = install.docker_plan(_V)
    assert set(plan) == set(install.placement())                     # в обе стороны
    files = install.render_docker("Europe/Berlin")
    cron_jobs = [l for l, (p, _) in plan.items() if p == "cron"]
    services = [l for l, (p, _) in plan.items() if p == "service"]
    lines = [l for l in files["crontab"].splitlines() if l and not l.startswith("#")]
    for label in cron_jobs:
        pl = plan[label][1]
        whens = pl.get("StartCalendarInterval", [None])
        want = len(whens) if isinstance(whens, list) else 1           # oura-import — пять раз в сутки
        got = sum(l.endswith(" " + install._cron_shell(label, pl)) for l in lines)
        assert got == want, (label, got, want)
    # метка задачи в окружении — как у launchd; без неё носитель проб пишет runner=manual (30.09)
    probes = [l for l in lines if "run_probes.sh" in l]
    assert probes and all("XPC_SERVICE_NAME=com.larry.health.probes" in l for l in probes), probes
    assert len(lines) == sum(
        len(w) if isinstance(w := plan[l][1].get("StartCalendarInterval"), list) else 1 for l in cron_jobs)
    compose = files["compose.yaml"]
    assert all(f"  {l.rsplit('.', 1)[-1]}:\n" in compose for l in services)
    rest = [p for p, _ in plan.values() if p not in ("cron", "service")]
    assert len(cron_jobs) + len(services) + len(rest) == len(plan) == len(install.render_launchd(_V))


def test_шаблон_без_места_отказ(monkeypatch):
    """Мутация ворот этапа 1: служба, забытая в placement.yaml, — отказ, а не тихий пропуск."""
    full = install.placement()
    monkeypatch.setattr(install, "placement", lambda: {k: v for k, v in full.items()
                                                       if k != "com.larry.health.night-cycle"})
    with pytest.raises(ValueError, match="night-cycle"):
        install.render_docker("Europe/Berlin")


def test_в_контейнер_не_протекают_пути_мака_и_пояс_явный():
    files = install.render_docker("Europe/Berlin")
    body = "".join(files.values())
    assert "/opt/homebrew" not in body and "/Users/" not in body and "{{" not in body
    assert "TZ=Europe/Berlin" in files[".env"] and "HEALTH_MULTITENANT=1" in files[".env"]
    assert "0 8 * * * " in files["crontab"]                           # ночной цикл в 08:00 местного


def test_расписание_без_дрейфа_частоты():
    assert install._cron_line({"Weekday": 0, "Hour": 5, "Minute": 30}) == "30 5 * * 0"
    assert install._cron_interval(300) == "*/5 * * * *"
    assert install._cron_interval(7200) == "0 */2 * * *"
    with pytest.raises(ValueError):
        install._cron_interval(420 * 60)                               # 7 ч не делит сутки
    with pytest.raises(ValueError):
        install._cron_line({"Year": 2026, "Hour": 9})                  # год cron не выражает


def test_порты_только_на_loopback_секреты_только_чтение_приватное_не_в_образе():
    """Этап 5 (WSTG-CONF): Colima публикует на все интерфейсы Мака то, что опубликовано без
    адреса (BL-COLIMA-DNS-1) — дашборд без пароля оказался бы в домашней сети. Секреты не
    пишутся из контейнера. Образ не несёт приватную зону владельца."""
    import yaml
    compose = yaml.safe_load(install.render_docker("Europe/Berlin")["compose.yaml"])
    for name, s in compose["services"].items():
        for p in s.get("ports", []):
            if name == "ingest":
                # Приём с телефона (hae-lan, 02.10): в Wi-Fi — только по явному --lan-ingest,
                # по умолчанию loopback; сам процесс несёт только запись под токеном.
                assert p == "${HEALTH_INGEST_BIND:-127.0.0.1}:8011:8011", p
                continue
            assert p.startswith("127.0.0.1:"), (name, p)
        sec = [v for v in s["volumes"] if v.split(":")[-2 if v.endswith(":ro") else -1]
               .endswith(".health_secrets")]
        assert sec and all(v.endswith(":ro") for v in sec), (name, s["volumes"])
    ignore = install.render_dockerignore().splitlines()
    assert "private/*" in ignore and "methodology/clinical_kb/*" in ignore and ".git" in ignore


def test_агент_colima_не_переключает_общий_контекст_докера():
    """Этап 8: старт профиля без --activate=false переключает ОБЩИЙ контекст Докера хоста, и соседний
    проект (соседний проект на той же машине) начинает ходить в нашу ВМ. Профиль свой, стартует, только если лежит."""
    import plistlib
    pl = plistlib.loads(install.render_colima_agent("/Users/x").encode("utf-8"))
    cmd = pl["ProgramArguments"][-1]
    assert "--activate=false" in cmd
    assert f"colima start --profile {install.COLIMA_PROFILE}" in cmd
    assert f"colima status --profile {install.COLIMA_PROFILE}" in cmd.split("||")[0]
    assert install.COLIMA_PROFILE != "default"
    assert pl["RunAtLoad"] is True and pl["StandardOutPath"].startswith("/Users/x/")


def test_агент_теневого_сторожа_находит_docker_и_свой_контейнер():
    """Этап 10: под launchd без PATH Homebrew docker не находится (замер 29.09) — сторож молча
    падал бы каждый час; контейнер и контекст — те же имена, что у профиля и compose-проекта."""
    import plistlib
    pl = plistlib.loads(install.render_shadow_agent("/Users/x").encode("utf-8"))
    env = pl["EnvironmentVariables"]
    assert "/opt/homebrew/bin" in env["PATH"].split(":")
    assert env["HEALTH_SHADOW_CONTAINER"] == f"{install.COMPOSE_PROJECT}-cron-1"
    assert env["HEALTH_SHADOW_CONTEXT"] == f"colima-{install.COLIMA_PROFILE}"
    assert env["HEALTH_DATA_DIR"] == "/Users/x/health"          # мультитенантный Studio: без него падение
    assert pl["StartInterval"] == 3600 and pl["ProgramArguments"][-1].endswith("/pilot_shadow.py")


def test_пилот_выгружает_только_службы_владельца_а_общие_оставляет():
    """Пилот волны Б (этап 11): счёт «36 служб владельца» по префиксу метки выгрузил бы бэкап
    партнёра и соседний проект, флаг мультитенантности партнёрских служб и сторожей машины (замер 29.09)."""
    split = install.pilot_split()
    assert set(split) == set(install.placement())                    # каждая служба решена, в обе стороны
    keep = {l for l, v in split.items() if v != "bootout"}
    for label in ("backup", "logrotate", "weekly-digest", "multitenant-env",
                  "uncommitted-watchdog"):
        assert f"com.larry.health.{label}" in keep, label
    assert "com.larry.healthbot.morningwake" in keep
    # code-watcher — служба машины, но судит данные владельца ($HOME/health): после переезда она
    # второй писатель замороженной копии (замер 30.09 12:52 — сиды перезаписали нативную базу)
    for label in ("bot", "dashboard", "lab-intake", "night-cycle", "triage", "import-watchdog",
                  "import-poll", "watcher", "code-watcher"):
        assert split[f"com.larry.health.{label}"] == "bootout", label
    assert all(v == "bootout" or v.startswith("keep: ") and len(v) > 12 for v in split.values())


def test_override_владельца_имя_машины_слой_только_чтение_порты_loopback(tmp_path):
    import yaml
    repo = tmp_path / "repo"
    (repo / "private").mkdir(parents=True)
    for n in ("infra.yaml", "region.yaml", "cpic_curated_ru.json"):
        (repo / "private" / n).write_text("x")
    out = install.render_owner_override("/Users/o", repo, "host-a.local", "Europe/Berlin")
    assert yaml.safe_load(out["infra.yaml"]) == {"primary_host": "host-a.local"}
    svc = yaml.safe_load(out["compose.override.yaml"])["services"]
    base = yaml.safe_load(install.render_docker("Europe/Berlin")["compose.yaml"])["services"]
    assert set(base) <= set(svc) and "caldav" in svc                 # ни одна служба не без настроек
    for name in base:
        s = svc[name]
        assert s["hostname"] == "host-a.local"                        # иначе запись запрещена
        vols = s["volumes"]
        backups = f"/Users/o/{install.OWNER_BACKUPS_REL}:{install.DOCKER_VALUES['DATA']}/backups"
        assert backups in vols                                        # бэкапы — вне ВМ Colima (30.09)
        assert all(v.endswith(":ro") for v in vols if v != backups)   # слой владельца и секреты — только чтение
        assert f"{repo}/private/region.yaml:/app/private/region.yaml:ro" in vols
        assert not any(v.startswith(f"{repo}/private/infra.yaml") for v in vols)   # адреса машин не едут
        assert f"{repo}/build/docker/host/infra.yaml:/app/private/infra.yaml:ro" in vols
        assert f"/Users/o/.health_secrets:{install.DOCKER_VALUES['SECRETS']}:ro" in vols
        assert f"/Users/o/health_reference:{install.DOCKER_VALUES['HOME']}/health_reference:ro" in vols
        # веса PGS (30.09 07:50: без них FAIL монитора «PGS reference-БД отсутствует»)
        assert f"/Users/o/.health_reference:{install.DOCKER_VALUES['HOME']}/.health_reference:ro" in vols
        assert "ports" not in s                                        # порты — только из базового рендера
        assert f"{repo}/logs:{install.HOST_LOGS}:ro" in vols            # журнал сбоев хоста (partner-faults)
    assert svc["cron"]["environment"] == {"HEALTH_FAULTS_EXTRA": f"{install.HOST_LOGS}/faults.jsonl"}
    assert svc["caldav"]["ports"] == ["127.0.0.1:5232:5232"]           # наружу — только tailscale serve
    assert f"radicale=={install.RADICALE_VERSION}" in svc["caldav"]["command"][-1]


def test_каталог_логов_данных_создаётся_до_первой_задачи():
    """Репетиция этапа 11 (29.09): `at_start.sh: cannot create …/health/logs/import_watchdog.log` —
    каталога нет в образе, и sh не запускает команду с несостоявшимся перенаправлением. Каждая задача,
    пишущая в {{DATA}}/logs, молча не шла бы никогда."""
    import yaml
    files = install.render_docker("Europe/Berlin")
    cmd = yaml.safe_load(files["compose.yaml"])["services"]["cron"]["command"][-1]
    assert cmd.startswith('mkdir -p "$$HEALTH_DATA_DIR/logs" &&'), cmd
    writers = [l for l in (install.TPL / "launchd").glob("*.tmpl") if "{{DATA}}/logs" in l.read_text()]
    assert writers, "никто не пишет в {{DATA}}/logs — тогда и проверка не нужна"
    assert f"HEALTH_DATA_DIR={install.DOCKER_VALUES['DATA']}" in files[".env"]
