#!/usr/bin/env python3
"""Установка Larry Health OS на свою машину (macOS) — план онбординга, Э4.

Что делает: раскладывает данные установки из templates/ туда, где их ждёт код (private/,
methodology/), создаёт каталог данных и секретов, строит пустую базу. Эта машина становится
основной (single-primary): здесь пишется БД и бегут фоновые сервисы.

Правила, ради которых модуль такой:
  * по умолчанию — ПРОБНЫЙ прогон: печатает, что будет записано, и ничего не пишет;
  * существующий файл НИКОГДА не перезаписывается (у владельца установка — пустой прогон);
  * секреты не спрашиваются и не пишутся: только каталог с правами 700 (WSTG-CONF-09);
  * фоновые сервисы launchd не загружаются — это отдельный шаг человека (план, Э4).

Снимки EFLM и CTCAE в репозиторий не входят (лицензии, NOTICE.md): без них пороги по этим
документам не сеются, система работает и говорит об этом. Скачать — отдельными флагами.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TPL = ROOT / "templates"
DEFAULT_IMAGE = "health-os:local"
INSTALL_DOC_PAGES = ("docs/tutorials/first_install.md", "docs/how-to/install_docker.md")
INSTALL_PROVENANCE = re.compile(r"<!-- install-provenance: (sha256:[0-9a-f]{12}) -->")
# Необязательные интеграции читают файлы в разных модулях (llm_client, reminders_backend,
# google_calendar_fetcher, import_oura). Общего реестра необязательных ключей нет:
# здесь один список для установочных страниц, не перечень всех секретов владельца.
INSTALL_OPTIONAL_SECRETS = ("anthropic_key", "caldav.json", "google_calendar_account",
                            "google_calendar_token.json", "oura_token")

# шаблон → место в репо (.tmpl — подстановка {{КЛЮЧ}} значениями установки)
FILES = [
    ("private/infra.yaml.tmpl", "private/infra.yaml"),
    ("private/pii_terms.yaml", "private/pii_terms.yaml"),
    ("methodology/clinical_kb/_index.yaml", "methodology/clinical_kb/_index.yaml"),
    ("methodology/clinical_kb/food.yaml", "methodology/clinical_kb/food.yaml"),
    ("methodology/food_rules.yaml", "methodology/food_rules.yaml"),
    ("methodology/validation_gate/data_manifest.yaml", "methodology/validation_gate/data_manifest.yaml"),
    ("launchd/health-logs.newsyslog.conf.tmpl", "launchd/health-logs.newsyslog.conf"),
]


def plan_install(data_dir: Path, secrets_dir: Path, values: dict[str, str]) -> list[tuple[str, Path, str | None]]:
    """[(действие, путь, содержимое|None)]: write — новый файл; keep — есть, не трогаю; mkdir."""
    out: list[tuple[str, Path, str | None]] = []
    for src, dst in FILES:
        target = ROOT / dst
        if target.exists():
            out.append(("keep", target, None))
            continue
        body = (TPL / src).read_text(encoding="utf-8")
        if src.endswith(".tmpl"):
            body = _fill(body, values, src)
        out.append(("write", target, body))
    for d in (data_dir / "data", secrets_dir):
        out.append(("keep" if d.exists() else "mkdir", d, None))
    return out


def apply_install(steps: list[tuple[str, Path, str | None]], secrets_dir: Path) -> None:
    for action, path, body in steps:
        if action == "write":
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "x", encoding="utf-8") as f:   # "x": гонка с чужой записью — отказ, не затирание
                f.write(body)
        elif action == "mkdir":
            path.mkdir(parents=True, exist_ok=True)
    secrets_dir.chmod(0o700)


def exclude_from_git(paths: list[Path]) -> list[str]:
    """Записанное установщиком — данные ЭТОЙ установки (своя основная машина, свой словарь
    личных слов, свои своды): прячем их от git этой копии через .git/info/exclude — локальный
    файл, который не коммитится. Не .gitignore: у автора те же пути отслеживаются, и общий
    .gitignore молча прятал бы от коммита новые файлы в них. Нет git — ничего не делаем.
    Приёмка урока свежим агентом 2026-09-24: после установки private/ висел неотслеживаемым,
    и `git add -A` унёс бы личный словарь в коммит."""
    try:
        exc = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--git-path", "info/exclude"],
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return []
    exc_path = Path(exc) if Path(exc).is_absolute() else ROOT / exc
    have = exc_path.read_text(encoding="utf-8").splitlines() if exc_path.exists() else []
    new = [p.relative_to(ROOT).as_posix() for p in paths if p.is_relative_to(ROOT)]
    new = ["/" + p for p in new if "/" + p not in have]
    if new:
        exc_path.parent.mkdir(parents=True, exist_ok=True)
        with open(exc_path, "a", encoding="utf-8") as f:
            f.write("# scripts/install.py — данные этой установки, в коммит не идут\n" + "\n".join(new) + "\n")
    return new


def secure_db(data_dir: Path) -> None:
    """База здоровья — только владельцу учётной записи (600), как требует датчик
    security:db_perms. sqlite создаёт файл под umask (обычно 644); до 2026-09-24 установщик так
    его и оставлял, а датчик потом «чинил» права — у постороннего это выглядело как тест,
    тронувший его базу (третья приёмка урока свежим агентом)."""
    db = data_dir / "data" / "health.db"
    if db.exists():
        db.chmod(0o600)


def _fill(text: str, values: dict[str, str], name: str) -> str:
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    if "{{" in text:
        raise ValueError(f"{name}: не заполнено {text[text.index('{{'):text.index('{{') + 20]}")
    return text


def render_launchd(values: dict[str, str]) -> dict[str, str]:
    """templates/launchd/*.plist.tmpl → {имя плиста: текст} с путями этой установки.
    Незаполненный {{…}} — отказ: плист с чужим/пустым путём молча читал бы не тот каталог
    (для второго тенанта это чужая база). Метки сервисов com.larry.health.* — имя продукта."""
    values = {"PYTHON": NATIVE_PYTHON, **values}
    out = {}
    for tpl in sorted((TPL / "launchd").glob("*.plist.tmpl")):
        out[tpl.name.removesuffix(".tmpl")] = _fill(tpl.read_text(encoding="utf-8"), values, tpl.name)
    return out


# Интерпретатор в плистах Мака. Плейсхолдер {{PYTHON}} заведён 2026-09-29 (docker-install, этап 1):
# в контейнере путь другой, а нативный вывод обязан остаться прежним (сверка с живыми плистами).
# Переход на sys.executable — этап 4 плана, не здесь.
NATIVE_PYTHON = "/opt/homebrew/bin/python3.11"

# Пути внутри контейнера. Образ и тома — этап 5; здесь только то, что нужно рендеру расписания.
# Раскладка ПОВТОРЯЕТ нативную (~/health, ~/.health_secrets), а не заводит свою (/data): личность
# данных — ИМЯ каталога (secrets_paths.is_owner_data: health → владелец, health_partner → партнёр),
# и предохранитель боевой базы health_db.CANONICAL_DB_PATH считается от HOME. С /data в контейнере
# владелец перестал бы быть владельцем, а assert_not_canonical смотрел бы мимо канона (замер
# 29.09, этап 4: 19 мест ~/health в коде, из них 5 без HEALTH_DATA_DIR).
DOCKER_VALUES = {"REPO": "/app", "HOME": "/home/health", "DATA": "/home/health/health",
                 "SECRETS": "/home/health/.health_secrets", "USER": "health", "PYTHON": "python3"}
_CRON = (("Minute", "*"), ("Hour", "*"), ("Day", "*"), ("Month", "*"), ("Weekday", "*"))


def render_dockerignore() -> str:
    """.dockerignore из publication_zones.yaml — единственного дома «что не публикуется»: образ
    несёт ровно публичную зону (решение владельца 23.09) плюс служебный мусор сборки. Приватные
    данные владельца (методология, клиническая база) в образ не запекаются: у владельца они
    МОНТИРУЮТСЯ в контейнер (волна Б), у постороннего их создаёт установщик из шаблонов."""
    import yaml
    zones = yaml.safe_load((ROOT / "publication_zones.yaml").read_text(encoding="utf-8"))
    junk = [".git", "**/__pycache__", "build", "logs", "*.db", "*.db-*", ".venv", ".pytest_cache",
            ".dockerignore"]
    head = "# сгенерировано scripts/install.py --docker из publication_zones.yaml — руками не править\n"
    return head + "\n".join(junk + [z["glob"] for z in zones["private"]]) + "\n"


def placement() -> dict[str, str]:
    """Куда едет каждая служба (templates/launchd/placement.yaml) — единственный дом признака."""
    import yaml
    return yaml.safe_load((TPL / "launchd" / "placement.yaml").read_text(encoding="utf-8"))["services"]


# Пилот волны Б (docker-install, этап 11): база владельца в контейнере, партнёр ещё нативно. Какие
# службы владельца выгружаются с хоста — ПРОИЗВОДНАЯ placement.yaml, не второй его дом: остаются
# службы машины (host), переменная окружения launchd (её читают и службы партнёра) и машинные
# службы, обходящие ВСЕХ тенантов, — без них партнёр и соседний проект остались бы без бэкапа, ротации и
# дайджеста (замер 29.09: счёт «36 служб владельца» по префиксу метки это терял).
PILOT_KEEP_NATIVE = {
    "com.larry.health.backup": "бэкапит базы всех тенантов и соседний проект; переехавшего тенанта пропускает по метке RUNTIME",
    "com.larry.health.logrotate": "ротирует логи хоста и партнёра; логи контейнера — его собственная задача",
    "com.larry.health.weekly-digest": "дайджест из истории git всем тенантам; в образе git нет",
}
# Службы машины, которые тем не менее судят ДАННЫЕ владельца: после переезда они второй писатель
# замороженной нативной копии. Замер 30.09 12:52 (после перезагрузки Studio): code-watcher гонял
# smoke и монитор с HEALTH_DATA_DIR=$HOME/health, сиды init_db перезаписали около 100 строк нативной базы,
# а ремонт прав монитора партнёра снял с неё запрет записи (444 → 600).
PILOT_STOP_HOST = {
    "com.larry.health.code-watcher": "гоняет тесты на данных владельца ($HOME/health) — после переезда второй писатель",
}


def pilot_split() -> dict[str, str]:
    """Метка службы владельца → 'bootout' или 'keep: <причина>' для переезда его базы в контейнер."""
    out = {}
    for label, place in placement().items():
        if label in PILOT_STOP_HOST:
            out[label] = "bootout"
        elif label in PILOT_KEEP_NATIVE:
            out[label] = "keep: " + PILOT_KEEP_NATIVE[label]
        elif place == "env":
            out[label] = "keep: переменная окружения launchd — её читают и службы партнёра"
        elif place.startswith("host"):
            out[label] = "keep: " + place.split(":", 1)[1].strip()
        else:
            out[label] = "bootout"
    return out


def _cron_line(when: dict) -> str:
    """{Hour: 8, Minute: 0} → «0 8 * * *». Ключ, которого в cron нет (Year), — отказ, не пропуск."""
    extra = set(when) - {k for k, _ in _CRON}
    if extra:
        raise ValueError(f"в строку cron не выражается: {sorted(extra)}")
    return " ".join(str(when.get(k, d)) for k, d in _CRON)


def _cron_interval(sec: int) -> str:
    """StartInterval → строка cron без дрейфа. Граница: launchd считает интервал от загрузки,
    cron — от начала часа/суток; сдвиг фазы допустим, разная частота — нет."""
    if sec % 3600 == 0 and 24 % (sec // 3600) == 0:
        return f"0 */{sec // 3600} * * *"
    if sec % 60 == 0 and 60 % (sec // 60) == 0:
        return f"*/{sec // 60} * * * *"
    raise ValueError(f"StartInterval {sec} с не выражается строкой cron без дрейфа частоты")


def _shell(pl: dict) -> str:
    """Командная строка задачи: каталог, окружение плиста, логи — те же файлы, что у launchd
    (список ротации один, log_rotation.one_home_of_the_list). PATH плиста выбрасывается:
    это пути Homebrew, и в образе python3 по нему не нашёлся бы."""
    import shlex
    env = {k: v for k, v in pl.get("EnvironmentVariables", {}).items() if k != "PATH"}
    cmd = " ".join(shlex.quote(a) for a in pl["ProgramArguments"])
    if env:
        cmd = "env " + " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(env.items())) + " " + cmd
    if wd := pl.get("WorkingDirectory"):
        cmd = f"cd {shlex.quote(wd)} && {cmd}"
    out, err = pl.get("StandardOutPath"), pl.get("StandardErrorPath")
    if out:
        cmd += f" >> {shlex.quote(out)}"
    if err:
        cmd += " 2>&1" if err == out else f" 2>> {shlex.quote(err)}"
    return cmd


def _cron_shell(label: str, pl: dict) -> str:
    """Строка задачи в crontab контейнера: та же, что у launchd, плюс метка задачи в окружении —
    как её кладёт launchd (XPC_SERVICE_NAME). По ней носитель проб отличает штатный прогон от
    ручного (runner в probe_verdicts.jsonl); без неё в контейнере каждый прогон был бы «manual»."""
    return _shell({**pl, "EnvironmentVariables": {**pl.get("EnvironmentVariables", {}),
                                                  "XPC_SERVICE_NAME": label}})


def docker_plan(values: dict[str, str]) -> dict[str, tuple[str, dict]]:
    """{Label: (место, плист)}: место — cron | service | env | host: … | none: …
    Каждый шаблон обязан иметь строку в placement.yaml и каждая строка — шаблон: служба без места
    молча пропала бы из контейнера (ворота этапа 1)."""
    import plistlib
    rendered = {n.removesuffix(".plist"): plistlib.loads(t.encode("utf-8"))
                for n, t in render_launchd(values).items()}
    where = placement()
    missing, orphan = sorted(set(rendered) - set(where)), sorted(set(where) - set(rendered))
    if missing or orphan:
        raise ValueError(f"placement.yaml: шаблон без места {missing}; место без шаблона {orphan}")
    out = {}
    for label, pl in rendered.items():
        state = where[label]
        if state == "container":
            state = "service" if pl.get("KeepAlive") else "cron"
            if state == "cron" and not ("StartCalendarInterval" in pl or "StartInterval" in pl):
                raise ValueError(f"{label}: в контейнер, но ни расписания, ни KeepAlive")
        elif state != "env" and not state.startswith(("host: ", "none: ")):
            raise ValueError(f"{label}: неизвестное место {state!r}")
        out[label] = (state, pl)
    return out


def _image_ref(ref: str) -> str:
    """Ссылка на образ: пустота и пробельные символы — ошибка на границе CLI/рендера."""
    if not ref or any(c.isspace() for c in ref):
        raise ValueError("--image: ссылка должна быть непустой и без пробельных символов")
    return ref


def _docker_arguments(ap: argparse.ArgumentParser) -> None:
    """Аргументы контейнерного рендера — общий дом для CLI и install_facts."""
    ap.add_argument("--docker", action="store_true",
                    help="собрать расписание и службы для контейнера в build/docker/ (с --tz; ничего не ставит)")
    ap.add_argument("--tz", help="часовой пояс человека (IANA, напр. Europe/Berlin); с --tenant обязателен")
    ap.add_argument("--image", metavar="REF", type=_image_ref,
                    help=f"с --docker: образ всех служб (по умолчанию {DEFAULT_IMAGE})")


def docker_summary(tz: str = "UTC") -> str:
    """Детерминированная часть печатаемого итога, без пути машины рендера."""
    places = [p for p, _ in docker_plan({**DOCKER_VALUES, "PRIMARY_HOST": "health-os", "TZ": tz}).values()]
    kinds = {k: sum(p == k or p.startswith(k + ":") for p in places)
             for k in ("cron", "service", "env", "host", "none")}
    return ", ".join(f"{k} {n}" for k, n in kinds.items()) + f" (всего {len(places)})"


def render_docker(tz: str, hostname: str = "health-os", *, image: str = DEFAULT_IMAGE) -> dict[str, str]:
    """Файлы для контейнера: crontab (supercronic), at_start.sh (задачи с RunAtLoad — в cron
    запуска при старте нет), compose.yaml (постоянные службы и планировщик), .env.
    Пояс задаётся явно: часы в шаблонах местные, а контейнер по умолчанию в UTC."""
    import plistlib
    import yaml
    image = _image_ref(image)
    values = {**DOCKER_VALUES, "PRIMARY_HOST": hostname, "TZ": tz}
    plan = docker_plan(values)
    cron, at_start, services = [], [], {}
    # Плисты того, что исполняет контейнер, — с путями контейнера: их читают датчики ритма,
    # логов и тенантов через plist_env_liveness.agents_dir() (этап 2a). Тот же рендер, что
    # и crontab, — второго дома расписания нет.
    plists_dir = f"{values['REPO']}/build/docker/plists"
    # HEALTH_PY — шов run_checks.sh (по умолчанию /opt/homebrew/bin/python3.11, в образе его нет).
    env = {"TZ": tz, "HEALTH_TZ": tz, "HEALTH_DATA_DIR": values["DATA"], "HEALTH_PY": "python3",
           "HEALTH_SECRETS_DIR": values["SECRETS"],
           "HEALTH_LAUNCHAGENTS_DIR": plists_dir, "HEALTH_RUNTIME": "container"}
    def _no_path(pl: dict) -> dict:
        env_ = {k: v for k, v in pl.get("EnvironmentVariables", {}).items() if k != "PATH"}
        return {**pl, "EnvironmentVariables": env_} if "EnvironmentVariables" in pl else pl
    plists = {f"plists/{label}.plist": plistlib.dumps(_no_path(pl)).decode("utf-8")
              for label, (state, pl) in plan.items() if state in ("cron", "service")}
    nofile = 0
    for label, (state, pl) in sorted(plan.items()):
        if state == "env":
            args = pl["ProgramArguments"]
            if args[:2] != ["/bin/launchctl", "setenv"] or len(args) != 4:
                raise ValueError(f"{label}: env-служба не вида launchctl setenv K V")
            env[args[2]] = args[3]
        elif state == "cron":
            line = _cron_shell(label, pl)
            whens = pl.get("StartCalendarInterval")
            if whens is not None:
                cron += [f"{_cron_line(w)} {line}" for w in (whens if isinstance(whens, list) else [whens])]
            else:
                cron.append(f"{_cron_interval(pl['StartInterval'])} {line}")
            if pl.get("RunAtLoad"):
                at_start.append(line)
            nofile = max(nofile, pl.get("SoftResourceLimits", {}).get("NumberOfFiles", 0))
        elif state == "service":
            name = label.rsplit(".", 1)[-1]
            if name in services:
                raise ValueError(f"имя службы compose {name!r} занято дважды")
            # Метка — для пульса службы (daemon_liveness.beat, этап 2б): PID соседнего
            # контейнера датчику не виден, живость служба доказывает сама.
            services[name] = {"image": image, "hostname": hostname, "env_file": [".env"],
                              "environment": {"HEALTH_SERVICE_LABEL": label},
                              "restart": "unless-stopped", "command": ["sh", "-c", _shell(pl)]}
    head = "# сгенерировано scripts/install.py --docker из templates/launchd — руками не править\n"
    services["cron"] = {"image": image, "hostname": hostname, "env_file": [".env"],
                        "restart": "unless-stopped",
                        # Расписание рендерится ВНУТРИ контейнера при старте из тех же шаблонов
                        # (build/ в образ не входит — .dockerignore): второго дома расписания нет.
                        # Каталог логов данных создаётся ДО первой задачи: восемь шаблонов пишут в
                        # {{DATA}}/logs, образ его не несёт, а сбой перенаправления в sh отменяет саму
                        # команду — задача молча не шла бы никогда (репетиция этапа 11, 29.09).
                        "command": ["sh", "-c", 'mkdir -p "$$HEALTH_DATA_DIR/logs" '
                                    '&& python3 /app/scripts/install.py --docker --tz "$$TZ" '
                                    "&& python3 -c 'import health_db; health_db.init_db()' "
                                    "&& sh /app/build/docker/at_start.sh; exec supercronic /app/build/docker/crontab"]}
    if nofile:
        services["cron"]["ulimits"] = {"nofile": {"soft": nofile, "hard": nofile}}
    # Базу создаёт cron при старте (init_db); постоянные службы ждут её, иначе первый старт —
    # гонка «no such table» (поймано приёмкой свежим агентом 29.09 у lab-intake).
    services["cron"]["healthcheck"] = {"test": ["CMD", "test", "-f", f"{values['DATA']}/data/health.db"],
                                       "interval": "5s", "timeout": "3s", "retries": 60}
    for name, s in services.items():
        if name != "cron":
            s["depends_on"] = {"cron": {"condition": "service_healthy"}}
    # Тома (этап 5): дом пользователя целиком (данные ~/health и логи в ~), логи репозитория,
    # секреты — только на чтение из каталога хоста. Слои образа данных не держат.
    volumes = ["health-home:/home/health", "health-applogs:/app/logs",
               "${HEALTH_SECRETS_HOST_DIR:-./secrets}:/home/health/.health_secrets:ro"]
    for s in services.values():
        s["volumes"] = list(volumes)
    if "dashboard" in services:
        services["dashboard"]["environment"]["DASHBOARD_HOST"] = "0.0.0.0"
        services["dashboard"]["ports"] = ["127.0.0.1:8001:8001"]   # только loopback хоста
    return {
        "crontab": head + "\n".join(cron) + "\n",
        "at_start.sh": head + "".join(f"{c} &\n" for c in at_start) + "wait\n",
        "compose.yaml": head + yaml.safe_dump({"services": services,
                                               "volumes": {"health-home": {}, "health-applogs": {}}},
                                              allow_unicode=True, sort_keys=False),
        ".env": head + "".join(f"{k}={v}\n" for k, v in sorted(env.items())),
        **plists,
    }


# INTENT: install_path — факты установщика, метка страниц установки (subsystem_intent.yaml)
def install_facts(tz: str = "UTC", image: str = DEFAULT_IMAGE) -> dict:
    """Только видимые установщику факты; рендер общий, секреты читаются как ИМЕНА из AST.
    Импорт bot.filters резолвил бы каталог секретов машины; разбираем его код без исполнения.
    Значения .env, hostname, время и домашний каталог в результат не попадают."""
    import shlex
    import yaml
    rendered = render_docker(tz, image=image)
    compose = yaml.safe_load(rendered["compose.yaml"])
    named_volumes = sorted(compose["volumes"])
    required = []
    tree = ast.parse((ROOT / "bot/filters.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in ("TOKEN_FILE", "CHAT_ID_FILE") for t in node.targets):
            if not isinstance(node.value, ast.BinOp) or not isinstance(node.value.op, ast.Div):
                raise ValueError("bot/filters.py: изменился формат пути стартового секрета")
            required.append(ast.literal_eval(node.value.right))
    if len(required) != 2:
        raise ValueError("bot/filters.py: не удалось прочитать имена двух стартовых секретов")
    dockerfile = (ROOT / "docker/Dockerfile").read_text(encoding="utf-8")
    arg = re.search(r"^\s*ARG\s+OCR_LANGS=(.+)$", dockerfile, re.M)
    if not arg:
        raise ValueError("docker/Dockerfile: нет ARG OCR_LANGS с дефолтом")
    ocr_default = shlex.split(arg.group(1), comments=True)
    if len(ocr_default) != 1 or not ocr_default[0]:
        raise ValueError("docker/Dockerfile: неверный дефолт ARG OCR_LANGS")
    ap = argparse.ArgumentParser(add_help=False)
    _docker_arguments(ap)
    return {
        "services": [{"name": name, "image": svc["image"], "ports": sorted(svc.get("ports", [])),
                      "restart": svc["restart"],
                      "volumes": sorted(v.split(":", 1)[0] for v in svc["volumes"]
                                        if v.split(":", 1)[0] in named_volumes)}
                     for name, svc in sorted(compose["services"].items())],
        "volumes": named_volumes,
        "summary": docker_summary(tz),
        "secrets": {"required": sorted(required), "optional": sorted(INSTALL_OPTIONAL_SECRETS)},
        "env_keys": sorted(line.split("=", 1)[0] for line in rendered[".env"].splitlines()
                           if line and not line.startswith("#")),
        "ocr_langs": ocr_default[0],
        "cli_flags": sorted(ap._option_string_actions),
    }


def install_facts_hash(facts: dict) -> str:
    """Тот же принцип провенанса, что у entry_provenance: JSON с сортировкой ключей."""
    return "sha256:" + hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False)
                                      .encode("utf-8")).hexdigest()[:12]


def install_doc_problem(page: str, text: str) -> str | None:
    """Нет/повторена/устарела метка — красный с командой ремонта; свежая метка — None."""
    marks = INSTALL_PROVENANCE.findall(text)
    current = install_facts_hash(install_facts())
    if marks == [current] and text.count("<!-- install-provenance:") == 1:
        return None
    reason = "нет метки" if "<!-- install-provenance:" not in text else "метка устарела или повреждена"
    return f"{page}: {reason}; python3.11 doc_agent.py --regen-install {page}"


COLIMA_PROFILE = "health"


def render_colima_agent(home: str) -> str:
    """Агент launchd Мака-хоста, поднимающий свой профиль Colima при входе (волна Б, этап 8).
    Не шаблон templates/launchd: там службы Health OS, их ставит и нативная установка, где Colima нет.
    --activate=false обязателен: без него старт профиля переключает ОБЩИЙ контекст Докера, и
    соседний проект на той же машине (контекст Докера не указывает явно) начинает
    ходить в нашу виртуальную машину (замер 29.09 на MacBook). Размеры ВМ здесь не пишутся:
    Colima хранит их в конфиге профиля с первого старта (docs/how-to/install_docker.md)."""
    import plistlib
    start = f"colima start --profile {COLIMA_PROFILE} --activate=false"
    log = f"{home}/Library/Logs/health-colima.log"
    return plistlib.dumps({
        "Label": "com.larry.health.colima",
        "ProgramArguments": ["/bin/sh", "-c", "export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH; "
                             f"colima status --profile {COLIMA_PROFILE} >/dev/null 2>&1 || {start}"],
        "RunAtLoad": True, "KeepAlive": False,
        "StandardOutPath": log, "StandardErrorPath": log,
    }).decode("utf-8")


COMPOSE_PROJECT = "health"   # docker compose -p health → контейнер планировщика health-cron-1


RADICALE_VERSION = "3.8.1"   # та же, что у пробы этапа 3 (MacBook, 29.09); новой версии не заводим


OWNER_BACKUPS_REL = "container_backups/health"   # не «health*»: иначе ~/health*-сторожа сочли бы его тенантом


def render_owner_override(home: str, repo: Path, primary_host: str, tz: str) -> dict[str, str]:
    """Настройки контейнера ВЛАДЕЛЬЦА на Studio (волна Б, этап 11): compose.override.yaml и infra.yaml
    контейнера. Не для посторонних — у них слоя владельца нет. Что и почему (замеры этапа 9):
    - имя машины = primary_host установки (решение владельца «Имя Studio»): иначе запись запрещена;
    - приватный слой только на чтение (methodology/, data/norm_docs/, private/ поимённо): без него пересев
      при старте заменяет правила CPIC (101→98), пищевую базу, запись о CTCAE; private/infra.yaml — свой,
      с одним primary_host (в хостовом — адреса машин, контейнеру не нужные);
    - справочник LOINC ~/health_reference — только на чтение: без него safety_net падает на трендах;
    - веса PGS ~/.health_reference/pgs.db (13 ГБ, общие) — только на чтение: без них PRS = no_weights
      (первая проверка в контейнере 30.09 07:50 — FAIL «PGS reference-БД отсутствует»);
    - секреты — каталог владельца, только на чтение;
    - CalDAV (Radicale) — порт только на loopback хоста, наружу в tailnet — tailscale serve.
    Список private/ читается на машине рендера: он и есть машина владельца."""
    import yaml
    base = yaml.safe_load(render_docker(tz, primary_host)["compose.yaml"])["services"]
    priv = sorted(p.name for p in (repo / "private").iterdir()
                  if p.is_file() and p.name != "infra.yaml") if (repo / "private").is_dir() else []
    vols = [f"{repo}/methodology:/app/methodology:ro", f"{repo}/data/norm_docs:/app/data/norm_docs:ro",
            *(f"{repo}/private/{n}:/app/private/{n}:ro" for n in priv),
            f"{repo}/build/docker/host/infra.yaml:/app/private/infra.yaml:ro",
            f"{home}/health_reference:{DOCKER_VALUES['HOME']}/health_reference:ro",
            f"{home}/.health_reference:{DOCKER_VALUES['HOME']}/.health_reference:ro",
            f"{home}/.health_secrets:{DOCKER_VALUES['SECRETS']}:ro",
            # единственный пишущий том хоста: бэкапы базы — вне ВМ Colima, где лежит сама база
            # (удаление профиля или сбой образа диска не уносят и базу, и её бэкапы разом, 30.09)
            f"{home}/{OWNER_BACKUPS_REL}:{DOCKER_VALUES['DATA']}/backups"]
    services = {name: {"hostname": primary_host, "volumes": list(vols)} for name in base}
    services["caldav"] = {
        "image": "python:3.11-slim", "restart": "unless-stopped",
        "command": ["sh", "-c", f'pip install -q "radicale=={RADICALE_VERSION}" && exec python -m radicale --config /caldav/config'],
        "volumes": [f"{home}/.health_caldav:/caldav"],
        "ports": ["127.0.0.1:5232:5232"]}
    head = "# сгенерировано scripts/install.py --owner-override (этап 11 волны Б) — руками не править\n"
    return {"compose.override.yaml": head + yaml.safe_dump({"services": services}, allow_unicode=True, sort_keys=False),
            "infra.yaml": head + yaml.safe_dump({"primary_host": primary_host})}


def render_shadow_agent(home: str) -> str:
    """Агент хоста: теневой сторож срочных тревог раз в час на время пилота (волна Б, этап 10).
    Грузится на этапе 11, вместе с контейнером владельца: без контейнера сторож честно шлёт «не смог
    сравнить». PATH с Homebrew обязателен: под launchd (как и по ssh) docker иначе не находится
    (замер 29.09). Репозиторий — основная копия Studio: натив судит своим кодом, тем же коммитом, что образ."""
    import plistlib
    log = f"{home}/Library/Logs/health-shadow.log"
    return plistlib.dumps({
        "Label": "com.larry.health.pilot-shadow",
        "ProgramArguments": [NATIVE_PYTHON, f"{home}/health_scripts/pilot_shadow.py"],
        "WorkingDirectory": f"{home}/health_scripts",
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
                                 "HEALTH_DATA_DIR": f"{home}/health",
                                 "HEALTH_SHADOW_CONTAINER": f"{COMPOSE_PROJECT}-cron-1",
                                 "HEALTH_SHADOW_CONTEXT": f"colima-{COLIMA_PROFILE}"},
        "StartInterval": 3600, "RunAtLoad": False,
        "StandardOutPath": log, "StandardErrorPath": log,
    }).decode("utf-8")


TENANT_NAME = re.compile(r"^[a-z][a-z0-9_]{1,20}$")
_LOG = re.compile(r"([\w./~-]+?)\.log\b")


def tenant_services() -> dict[str, str]:
    """Службы человека и их готовность (templates/launchd/tenant_services.yaml)."""
    import yaml
    return yaml.safe_load((TPL / "launchd" / "tenant_services.yaml").read_text(encoding="utf-8"))["services"]


def _retag(value, tenant: str):
    """Каждый путь *.log в плисте — свой у человека: логи двух людей не смешиваются."""
    if isinstance(value, str):
        return _LOG.sub(lambda m: f"{m.group(1)}_{tenant}.log", value)
    if isinstance(value, list):
        return [_retag(v, tenant) for v in value]
    if isinstance(value, dict):
        return {k: _retag(v, tenant) for k, v in value.items()}
    return value


def render_tenant(values: dict[str, str], tenant: str, dashboard_port: int) -> dict[str, bytes]:
    """Плисты служб человека: метка и имя файла +.<имя>, свои данные, секреты, пояс, логи.
    Только `ready` из tenant_services.yaml. Секреты — ОБЯЗАТЕЛЬНО свои: без HEALTH_SECRETS_DIR
    процесс чужих данных отказывает (tenant_secrets_fail_closed), а плист, который это
    забыл, — бот в цикле падений (KeepAlive)."""
    import plistlib
    rendered = render_launchd(values)
    out = {}
    for svc, state in tenant_services().items():
        if state != "ready":
            continue
        pl = plistlib.loads(rendered[f"com.larry.health.{svc}.plist"].encode("utf-8"))
        pl["Label"] = f"{pl['Label']}.{tenant}"
        env = pl.setdefault("EnvironmentVariables", {})
        env.update(HEALTH_DATA_DIR=values["DATA"], HEALTH_SECRETS_DIR=values["SECRETS"],
                   HEALTH_TZ=values["TZ"])
        if svc == "dashboard":
            env["DASHBOARD_PORT"] = str(dashboard_port)
        if svc == "bot":
            # Бриф человека судится ЕГО вердиктом ночной проверки: run_checks.sh кладёт его в
            # <данные>/logs (HEALTH_TRIAGE_LOGS), бот без этой переменной читал артефакт
            # владельца и слал BRIEF_NOT_GATED каждое утро (замер 01.10, M2).
            env["HEALTH_TRIAGE_LOGS"] = f"{values['DATA']}/logs"
        out[pl["Label"] + ".plist"] = plistlib.dumps(_retag(pl, tenant))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="записать (без флага — пробный прогон)")
    ap.add_argument("--non-interactive", action="store_true", help="без вопросов (проба, CI)")
    ap.add_argument("--data-dir", default=os.environ.get("HEALTH_DATA_DIR", str(Path.home() / "health")))
    ap.add_argument("--secrets-dir", default=None,
                    help="каталог секретов (по умолчанию — secrets_paths.secrets_dir())")
    ap.add_argument("--fetch-eflm", action="store_true", help="скачать снимок EFLM из их API (нужна сеть)")
    ap.add_argument("--fetch-ctcae", action="store_true",
                    help="скачать таблицу CTCAE с сайта NCI и построить снимок порогов (нужна сеть)")
    ap.add_argument("--launchd", action="store_true",
                    help="собрать плисты фоновых сервисов в build/launchd/ (НЕ загружает их)")
    ap.add_argument("--tenant", metavar="ИМЯ",
                    help="завести ещё одного человека: ~/health_<имя>, ~/.health_secrets_<имя>, "
                         "свои плисты служб (docs/how-to/add_person.md)")
    ap.add_argument("--dashboard-port", type=int, help="порт дашборда человека; с --tenant обязателен")
    ap.add_argument("--files-only", action="store_true",
                    help="с --apply: только разложить шаблоны, без базы (сборка образа)")
    _docker_arguments(ap)
    ap.add_argument("--pilot-split", action="store_true",
                    help="пилот волны Б: какие службы владельца выгружаются с хоста, какие остаются (печать)")
    ap.add_argument("--owner-override", action="store_true",
                    help="пилот волны Б: build/docker/compose.override.yaml владельца (с --tz; на машине владельца)")
    a = ap.parse_args(argv)
    if a.image is not None and not a.docker:
        ap.error("--image используется с --docker")
    if a.pilot_split:
        for label, verdict in sorted(pilot_split().items(), key=lambda kv: (kv[1] != "bootout", kv[0])):
            print(f"{'bootout' if verdict == 'bootout' else 'keep   '} {label}"
                  + ("" if verdict == "bootout" else f"  — {verdict[6:]}"))
        print("# порядок переезда и отката: docs/how-to/pilot_switch.md (English: docs/how-to/pilot_switch.en.md)")
        return 0
    if a.owner_override:
        if not a.tz:
            ap.error("--owner-override требует --tz")
        sys.path.insert(0, str(ROOT))
        import infra_config
        if not infra_config.PRIMARY_HOST:
            ap.error("в private/infra.yaml нет primary_host — это не машина владельца")
        dest = ROOT / "build" / "docker"
        (dest / "host").mkdir(parents=True, exist_ok=True)
        out = render_owner_override(str(Path.home()), ROOT, infra_config.PRIMARY_HOST, a.tz)
        (dest / "compose.override.yaml").write_text(out["compose.override.yaml"], encoding="utf-8")
        (dest / "host" / "infra.yaml").write_text(out["infra.yaml"], encoding="utf-8")
        print(f"override владельца: {dest / 'compose.override.yaml'} (имя машины {infra_config.PRIMARY_HOST})")
        return 0
    if a.docker:
        if not a.tz:
            ap.error("--docker требует --tz: часы в шаблонах местные, контейнер по умолчанию в UTC")
        dest = ROOT / "build" / "docker"
        dest.mkdir(parents=True, exist_ok=True)
        for name, text in render_docker(a.tz, image=a.image if a.image is not None else DEFAULT_IMAGE).items():
            (dest / name).parent.mkdir(parents=True, exist_ok=True)
            (dest / name).write_text(text, encoding="utf-8")
        (ROOT / ".dockerignore").write_text(render_dockerignore(), encoding="utf-8")   # в .gitignore
        if sys.platform == "darwin":   # хост Мака; внутри контейнера (cron рендерит заново) не нужен
            (dest / "host").mkdir(exist_ok=True)
            (dest / "host" / "com.larry.health.colima.plist").write_text(
                render_colima_agent(str(Path.home())), encoding="utf-8")
            (dest / "host" / "com.larry.health.pilot-shadow.plist").write_text(
                render_shadow_agent(str(Path.home())), encoding="utf-8")
        print(f"собрано в {dest}: " + docker_summary(a.tz))
        print("дальше — сборка образа и запуск: docs/how-to/install_docker.md "
              "(English: docs/how-to/install_docker.en.md)")
        return 0
    if a.tenant:
        if not TENANT_NAME.match(a.tenant):
            ap.error("--tenant: латиница в нижнем регистре, цифры, _; 2–21 символ")
        if not a.tz or not a.dashboard_port:
            ap.error("--tenant требует --tz и --dashboard-port")
        if a.dashboard_port == 8001:
            ap.error("--dashboard-port 8001 занят дашбордом основной установки")
        a.data_dir = str(Path.home() / f"health_{a.tenant}")
        a.secrets_dir = str(Path.home() / f".health_secrets_{a.tenant}")
    if a.secrets_dir is None:
        sys.path.insert(0, str(ROOT))
        from secrets_paths import secrets_dir as _secrets_dir   # единый резолвер секретов
        a.secrets_dir = str(_secrets_dir())
    data_dir, secrets_dir = Path(a.data_dir).expanduser(), Path(a.secrets_dir).expanduser()
    import getpass
    # HEALTH_PRIMARY_HOST: при сборке образа имя машины случайное, а контейнер живёт под hostname
    # из compose (docker-install, этап 5) — основная машина задаётся явно, иначе запись запрещена.
    values = {"PRIMARY_HOST": os.environ.get("HEALTH_PRIMARY_HOST") or socket.gethostname(),
              "REPO": str(ROOT), "HOME": str(Path.home()),
              "USER": getpass.getuser(), "DATA": str(data_dir), "SECRETS": str(secrets_dir),
              "TZ": a.tz or os.environ.get("HEALTH_TZ", "UTC")}
    steps = plan_install(data_dir, secrets_dir, values)
    for action, path, _ in steps:
        print(f"{ {'write': '+ запишу', 'keep': '= есть, не трогаю', 'mkdir': '+ создам'}[action]:18} {path}")
    if not a.apply:
        print("\nпробный прогон — ничего не записано; повтори с --apply")
        return 0
    if not a.non_interactive and input("\nзаписать? [y/N] ").strip().lower() != "y":
        return 1
    apply_install(steps, secrets_dir)
    hidden = exclude_from_git([p for act, p, _ in steps if act == "write"])
    if hidden:
        print(f"скрыто от git этой копии (.git/info/exclude): {len(hidden)} — ваши данные не уйдут в коммит")
    env = {**os.environ, "HEALTH_DATA_DIR": str(data_dir), "HEALTH_SECRETS_DIR": str(secrets_dir)}
    if a.fetch_eflm:
        # Состав снимка — правило «все аналиты EFLM, которые узнаёт словарь канона» (решение
        # владельца 25.09): карты нет, скачать может любая установка. Снимок в репо не едет (NOTICE.md).
        r = subprocess.run([sys.executable, "-c", "import norm_documents; print(norm_documents.fetch_eflm())"],
                           cwd=ROOT, env=env)
        if r.returncode:
            print("EFLM: скачать не удалось — пороги лаб-трендов будут на резерве; "
                  "повторите установку с --fetch-eflm, когда будет сеть")
    if a.fetch_ctcae:
        # Документ берётся у NCI на машине установки, в репозиторий не кладётся (коды MedDRA,
        # NOTICE.md; решение владельца 2026-09-25). Сеть упала — установка не падает: safety_net
        # честно работает на резерве, а повтор — та же команда с --fetch-ctcae.
        r = subprocess.run([sys.executable, "-c", "import norm_documents; print(norm_documents.fetch_ctcae())"],
                           cwd=ROOT, env=env)
        if r.returncode:
            print("CTCAE: скачать не удалось — пороги safety_net будут на резерве; "
                  "повторите установку с --fetch-ctcae, когда будет сеть")
    if a.files_only:
        # Сборка образа (docker-install, этап 5): машина сборки не основная, и предохранитель
        # single-primary правильно запрещает базу; база создаётся при старте контейнера.
        print("\n✅ шаблоны установки разложены; база не создавалась (--files-only)")
        return 0
    subprocess.run([sys.executable, "-c", "import health_db; health_db.init_db()"],
                   cwd=ROOT, env=env, check=True)
    secure_db(data_dir)
    sys.path.insert(0, str(ROOT))
    import log_rotate   # конфиг ротации дополняется логами собранных плистов
    conf = ROOT / "launchd" / "health-logs.newsyslog.conf"
    if a.launchd and a.tenant:
        # Своя папка сборки: второй рендер не затирает первый (обзор служб 28.09).
        dest = ROOT / "build" / "launchd" / a.tenant
        dest.mkdir(parents=True, exist_ok=True)
        rendered = render_tenant(values, a.tenant, a.dashboard_port)
        for name, body in rendered.items():
            (dest / name).write_bytes(body)
        added = log_rotate.cover((b.decode("utf-8") for b in rendered.values()), conf, values["USER"])
        print(f"логи человека под ротацией: +{len(added)} строк в {conf.name}")
        waiting = [s for s, st in tenant_services().items() if st != "ready"]
        print(f"службы, которые человеку пока не ставятся ({len(waiting)}): {', '.join(waiting)} — "
              "причины в templates/launchd/tenant_services.yaml")
        print(f"плисты собраны в {dest} — загрузка вручную: docs/how-to/add_person.md")
    elif a.launchd:
        dest = ROOT / "build" / "launchd"
        dest.mkdir(parents=True, exist_ok=True)
        rendered = render_launchd(values)
        for name, text in rendered.items():
            (dest / name).write_text(text, encoding="utf-8")
        added = log_rotate.cover(rendered.values(), conf, values["USER"])
        if added:
            print(f"логи служб под ротацией: +{len(added)} строк в {conf.name}")
        print(f"плисты собраны в {dest} — загрузка вручную: cp в ~/Library/LaunchAgents, "
              "launchctl bootstrap gui/$(id -u) <плист>")
    print(f"\n✅ установка: основная машина — {socket.gethostname()}, данные — {data_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
