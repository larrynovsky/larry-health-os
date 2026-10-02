"""secrets_paths.py — ЕДИНЫЙ источник каталога секретов тенанта.

После кросс-тенант утечки (2026-07-01): все per-tenant читатели секретов (telegram
и пр.) берут каталог ОТСЮДА, а не хардкодят ~/.health_secrets. env HEALTH_SECRETS_DIR
переопределяет (тенант партнёра = ~/.health_secrets_partner); иначе ~/.health_secrets.

Leaf-модуль (только os+pathlib) — импортируется откуда угодно без циклов.
Admin-global алерты (инфра → всегда владельцу) сюда НЕ переводятся — они намеренно
на ~/.health_secrets.
"""
# INTENT: multitenancy — многопользовательность: изоляция тенантов (данные/секреты/доставка).
#          Замысел и инварианты — subsystem_intent.yaml, раздел multitenancy.
import os
import re
from pathlib import Path


def secrets_dir() -> Path:
    """Каталог секретов текущего тенанта (env HEALTH_SECRETS_DIR или ~/.health_secrets).

    fail-closed (2026-07-03): если HEALTH_DATA_DIR указывает на ТЕНАНТА (имя каталога
    ≠ 'health'), но HEALTH_SECRETS_DIR НЕ задан — отказ. Иначе резолвер молча
    откатился бы на ~/.health_secrets (секреты владельца) и процесс тенанта взял бы
    токены владельца → доказанная кросс-тенант утечка (Oura-данные владельца в БД партнёра).
    Владелец (каталог .../health или HEALTH_DATA_DIR не задан) — дефолт легитимен.
    """
    sd = os.environ.get("HEALTH_SECRETS_DIR")
    if sd:
        return Path(sd)
    if not is_owner():   # D2 (2026-07-23): единый сигнал владельца (был inline dd и name != "health")
        dd = os.environ.get("HEALTH_DATA_DIR", "")
        raise RuntimeError(
            f"secrets_dir: HEALTH_DATA_DIR={dd} (тенант), но HEALTH_SECRETS_DIR не задан "
            "— отказ (иначе взяли бы секреты владельца → кросс-тенант утечка).")
    return Path.home() / ".health_secrets"


def owner_secrets_dir() -> Path:
    """Каталог секретов ВЛАДЕЛЬЦА — независимо от того, чей процесс спрашивает.

    Решение владельца 2026-08-01: «служебные сообщения — мне, содержательные —
    каждому свои». Служебное адресовано ОПЕРАТОРУ системы, а не тому, чьи данные
    обрабатываются. До этого `notify` брал получателя только из `secrets_dir()`,
    честно-per-tenant, и вотчер партнёра слал запросы на ревью самому партнёру —
    19278 сообщений за трое суток (01.08).

    Отличие от `ADMIN_GLOBAL_TG_ALLOW`: там модуль owner-only ПО ОБЛАСТИ (партнёрских
    джоб нет вовсе), здесь процесс ТЕНАНТСКИЙ, а канал служебный. Поэтому это не
    шестая запись в тот список, а отдельный резолвер.

    ГРАНИЦА: мед-данные тенанта в этот канал слать нельзя — он ведёт к владельцу.
    Имя файла, лейбл джобы, ссылка на ревью — можно (взаимная видимость ревью
    согласована 2026-07-18).
    """
    return Path.home() / ".health_secrets"


def is_owner() -> bool:
    """True если процесс — ВЛАДЕЛЕЦ (не тенант). Единый сигнал владельца: HEALTH_DATA_DIR
    не задан ИЛИ имя каталога == 'health' — тот же критерий тенанта, что в secrets_dir().
    Используется owner-only гейтами (напр. стратифицированная семья A валидационного гейта:
    терапевтические эпохи владельца невалидны для тенанта). Fail-closed по построению:
    любой каталог-тенант (имя ≠ 'health') → False. D2 (2026-07-23): secrets_dir() теперь
    зовёт этот хелпер — единый источник сигнала владельца (inline-долг закрыт)."""
    return is_owner_dir(os.environ.get("HEALTH_DATA_DIR", ""))


def is_owner_dir(data_dir: str) -> bool:
    """Тот же критерий владельца, но для ПРОИЗВОЛЬНОГО каталога, а не для env.

    Нужен тем, кто обходит чужих тенантов (например, считает, сколько их обслуживает
    launchd). Вынесен, чтобы у критерия остался один дом: копия правила «имя каталога
    == health» в обходчике разъехалась бы с этим молча.
    """
    return (not data_dir) or Path(data_dir).name == "health"


# Маркеры дев/стейджинг-клона в имени каталога. Дом переехал сюда из integrity_tests
# (2026-08-12): понятия «владелец» и «клон владельца» родственные, и то, что они жили в
# разных модулях, и было причиной дефекта ниже — is_owner() не умел отличить КЛОН
# владельца от ЧУЖОГО тенанта. integrity_tests читает список отсюда; secrets_paths
# импортирует только os и pathlib, поэтому зависимость дешёвая в любую сторону.
DEV_CLONE_MARKERS = ("staging", "_dev", "_test", "_clone", "_bak")


# Где у тенанта лежит БД относительно его каталога (~/health_X). Один дом раскладки для
# обходчика диска (tenant_db_paths) и для реестра ожидаемых по плистам (plist_env_liveness).
TENANT_DB_REL = Path("data", "health.db")


# Метка переезда (нить docker-install, 30.09): файл RUNTIME в корне тенанта со словом container
# значит «живой стор — в томе контейнера, нативная копия заморожена». Пишет её переезд; читают
# backup_studio.sh, деплой-хук, test_on_studio.sh и night_repair (shell/литералом). Здесь — дом
# для Python-читателей (нить tenant-run-scope, 02.10).
RUNTIME_MARK = "RUNTIME"


def moved_to_container(root) -> bool:
    """Корень тенанта переехал в контейнер: его нативная копия — замороженный снимок, не пациент."""
    mark = Path(root) / RUNTIME_MARK
    return mark.is_file() and mark.read_text(encoding="utf-8").strip() == "container"


def owner_runs_elsewhere() -> bool:
    """Этот процесс — прогон ЧУЖОГО тенанта на хосте, а владелец переехал в контейнер.

    Тогда артефакты владельца в logs/ репозитория и его плисты в LaunchAgents — следы
    до переезда: триаж, ночной цикл, колокол, ночные тесты и пробы живут и судятся в
    контейнере. Прогон тенанта, судящий их здесь, видит только заморожённое и шлёт
    оператору ложную тревогу (замер 02.10: 3 FAIL и ~10 WARN у партнёра — все про
    застывшую копию владельца). В контейнере и в прогоне на данных владельца — False."""
    return (os.environ.get("HEALTH_RUNTIME") != "container" and not is_owner_data()
            and moved_to_container(owner_root()))


OWNER_ROOT_ENV = "HEALTH_OWNER_ROOT"


def owner_root() -> Path:
    """Корень данных владельца на этой машине: ~/health. Env — шов тестов: набор на Studio
    гоняется на машине, где настоящий ~/health переехал, и без шва любой тест на чужом
    тенанте молча видел бы «владелец в контейнере» (первый полный прогон 02.10: 7 красных)."""
    return Path(os.environ.get(OWNER_ROOT_ENV) or Path.home() / "health")


def tenant_db_paths(current: "Path | None" = None) -> list:
    """Пути health.db РЕАЛЬНЫХ пациентов-тенантов (~/health*/data/health.db) минус дев/стейджинг-
    клоны (DEV_CLONE_MARKERS). Дом предиката «кто тенант» — здесь, рядом с маркерами (перенос из
    integrity_tests 2026-09-05, BL-DIGEST-1): у датчика cross-tenant и у outbox-читателя дайджеста
    множество обязано быть одним. `current` — БД текущего процесса, если её надо включить даже вне
    ~/health* (integrity передаёт db.DB_PATH); модуль сам health_db не импортирует."""
    paths = set()
    if current is not None:
        paths.add(Path(current).resolve())
    for p in Path.home().glob(f"health*/{TENANT_DB_REL.as_posix()}"):
        if not p.exists():
            continue
        name = p.parent.parent.name  # ~/health_X/data/health.db → health_X
        if any(m in name for m in DEV_CLONE_MARKERS):
            continue
        if moved_to_container(p.parent.parent):
            continue        # заморожённая копия переехавшего тенанта — его судит контейнер
        paths.add(p.resolve())
    return sorted(paths)


def nested_db_forks(canons) -> list:
    """health.db, лежащие ВНУТРИ каталога данных тенанта на 1–2 уровня ниже канона.

    Так выглядит процесс, которому дали HEALTH_DATA_DIR на уровень глубже корня тенанта:
    init_db молча заводит <data>/data/health.db с сидами (замер 27.09: лежал с 14.09, ни
    один датчик его не видел — check_single_canonical_db знал только iCloud и корень репо)."""
    out = []
    for canon in canons:
        d = Path(canon).parent
        out += [*d.glob("*/health.db"), *d.glob("*/*/health.db")]
    return sorted(out)


def is_owner_data(data_dir: "str | None" = None) -> bool:
    """Данные в этом каталоге ПРОИСХОДЯТ от владельца (включая клон канона).

    ЭТОТ ПРЕДИКАТ НЕ ДАЁТ ПРАВ. Он отвечает на вопрос о происхождении данных, а не на
    вопрос «я владелец» — секреты, доступ к файлам владельца и роль человека по ту
    сторону разговора спрашивают is_owner(), и правильный ответ там другой.

    Зачем отдельно от is_owner() (замер 2026-08-12). `is_owner()` отвечал сразу на
    четыре разных вопроса, и на вопрос о ДАННЫХ отвечал неверно: критерий — буквальное
    имя каталога, поэтому `~/health_staging` (побайтовая копия канона владельца, эпохи
    настоящие) читался как чужой тенант. Следствие: стратифицированная семья A не
    размечалась в песочнице, а проба карантина — единственная, кто гоняет весь путь от
    команды до конституций, — не могла проверить то, ради чего написана: её пары из
    семьи A не публиковались вовсе. Любой owner-only механизм был непроверяем в
    песочнице не потому, что не должен там работать, а из-за суффикса в имени папки.

    Второй дом идентичности НЕ заводится (health_db: «tenant_id не хранится: БД = тенант;
    хранить = дубль идентичности → split-brain»). Каталог остаётся единственным
    источником — меняется только вопрос к нему: имя, очищенное от маркера клона.
    Живые каталоги на 2026-08-12: health → владелец, health_partner → партнёр,
    health_staging → клон владельца. Клон партнёра (health_partner_staging) очистится в
    health_partner и владельцем НЕ станет.
    """
    dd = os.environ.get("HEALTH_DATA_DIR", "") if data_dir is None else data_dir
    if not dd:
        return True                      # env не задан — тот же дефолт, что у is_owner
    name = Path(dd).name
    for marker in DEV_CLONE_MARKERS:
        name = name.replace(marker, "")
    return name.rstrip("_") == "health"


# Admin-global отправители Telegram: инфра-алерты, намеренно шлющие ВЛАДЕЛЬЦУ
# (падение тестов, траты API, health модели, watchdog незакоммиченного, upstream-
# трекинг). Хардкодят ~/.health_secrets by design — owner-only by scope (нет
# партнёрских launchd-plist, per-tenant мед-данные НЕ шлют). ЕДИНЫЙ источник списка:
# его читают и check_contracts (per-tenant secrets-хардкод), и датчик полноты доставки
# tests/consistency/test_telegram_delivery_coverage. Новый прямой sendMessage-канал
# обязан брать получателя из secrets_dir()/get_chat_id() ЛИБО попасть сюда ОСОЗНАННЫМ
# решением человека (и не слать мед-данные тенанта).
# Извлечение имён, которые код читает КАК СЕКРЕТ. Дом один: этой функцией пользуются
# и датчик полноты реестра (tests/consistency), и check_contracts — копия разошлась бы.
# ГРАНИЦА, названная вслух: видны только ЛИТЕРАЛЫ. Имя, собранное через переменную
# ($SECRETS/telegram_token в icloud_conflict_check.sh), сюда не попадёт — такой файл
# ловится сторожем per-tenant либо лежит в ADMIN_GLOBAL_TG_ALLOW.
_SECRET_NAME = r'([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9]+)?)'
_SECRET_REF = re.compile(
    r'\.health_secrets(?:_partner|_staging)?/' + _SECRET_NAME
    + r'|\.health_secrets(?:_partner|_staging)?"\s*/\s*"' + _SECRET_NAME + '"'
    + r'|secrets_dir\(\)\s*/\s*"' + _SECRET_NAME + '"'
    + r'|owner_secrets_dir\(\)\s*/\s*"' + _SECRET_NAME + '"')


def referenced_secret_names(sources) -> set:
    """sources: итерируемое с текстами файлов → множество имён, читаемых как секрет."""
    out = set()
    for src in sources:
        for m in _SECRET_REF.finditer(src):
            name = next(g for g in m.groups() if g).rstrip(".")
            if name.isupper():          # BOT_TOKEN_FILE, MARKER — имена переменных
                continue
            out.add(name)
    return out


# ── ЧТЕНИЕ ЧЕРЕЗ ПЕРЕМЕННУЮ (16.09, BL-SECRET-READER-LITERAL-1) ──────────────
# Литеральный сканер видит `secrets_dir() / "имя"`. Форму `s = secrets or
# owner_secrets_dir(); s / "имя"` он не видит — так читает notify._email_settings,
# и WARN «секреты без читателя» вырос с одного имени до шести, где пять ложных.
# Предупреждение, где большинство строк заведомо ложные, перестают читать: тот же
# яд, что ложно-красный датчик.
#
# ДВА ОГРАНИЧЕНИЯ, и они оба нужны, чтобы форма не спрятала МЁРТВЫЙ секрет:
#   1. файл обязан работать с каталогом секретов (маркер ниже) — иначе любой
#      `path / "что-нибудь"` в проекте выглядел бы чтением секрета;
#   2. имя обязано быть ОБЪЯВЛЕНО в реестре (`scope`) — замер 16.09: без этого
#      условия форма ловит 77 пар вне реестра (`cfg / "logs"`, `root / "data"`),
#      то есть шума было бы больше, чем предмета.
# Что осталось видно после сужения: `read_token` — мёртвый секрет, найденный
# 02.09, читателя нет ни в одной форме. Ровно ради таких WARN и существует.
# 21.09 выведен из реестра решением владельца: READ_TOKEN старого VPS-сервера
# (снят в пользу Telegram auth, 15.06), файла нет ни на одной машине — на дисках
# лежали только стендовые заглушки. После этого WARN пуст, и это правда, а не слепота:
# негативный контроль держит тест на фикстуре.
_SECRETS_MARKER = re.compile(r'secrets_dir\(|owner_secrets_dir\(|\.health_secrets')
_VAR_REF = re.compile(r'\b[a-z_][a-z0-9_]*\s*/\s*"([A-Za-z_][A-Za-z0-9_.]*)"')


def referenced_via_provider_profiles(root: Path | None = None) -> set:
    """ТРЕТЬЯ ФОРМА ЧТЕНИЯ (02.10, llm-provider): ключ поставщика моделей llm_client.api_key
    читает по имени из профиля (methodology/llm_providers.json → key_file) — данные, не литерал.
    Профиля нет — и формы нет: имя без профиля остаётся «без читателя»."""
    import json
    p = (root or Path(__file__).parent) / "methodology" / "llm_providers.json"
    try:
        prof = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    return {v["key_file"] for k, v in prof.items() if not k.startswith("_")}


def referenced_via_variable(sources, scope=None) -> set:
    """Имена реестра, читаемые формой `<переменная> / "имя"` в файле-с-секретами.

    sources: итерируемое с ТЕКСТАМИ файлов (маркер ищется в том же тексте).
    scope: отображение имя→класс; по умолчанию SECRET_SCOPE.
    """
    known = SECRET_SCOPE if scope is None else scope
    out = set()
    for src in sources:
        if not _SECRETS_MARKER.search(src):
            continue
        for m in _VAR_REF.finditer(src):
            if m.group(1) in known:
                out.add(m.group(1))
    return out


# ── Ф1 (2026-09-02): ПЕРИМЕТР СТОРОЖЕЙ — ОДИН ДОМ ────────────────────────────
# Оба сторожа класса (check_contracts «per-tenant секреты» и датчик полноты доставки
# tests/consistency/test_telegram_delivery_coverage) сканировали ТОЛЬКО *.py. Канал же
# бывает на любом языке: watch_and_test.sh слал телеграм прямым curl с хардкодом и был
# невидим ОБОИМ (замер 02.09), icloud_conflict_check.sh — тоже. Периметр задают ЯЗЫКИ
# КАНАЛОВ, а не язык, на котором удобно сканировать. Список здесь, а не по копии в
# каждом стороже: два периметра разошлись бы молча — тот же класс, что два дома данных.
GUARDED_SUFFIXES = (".py", ".sh")


# ── Ф2 (2026-09-02): КЛАСС СЕКРЕТА — ДАННЫЕ, НЕ ПРЕДИКАТ ─────────────────────
# Было: сторож перечислял три имени прямо в `if` (telegram_token|telegram_chat_id|
# oura_token). Список отстал от реальности — у партнёра есть ещё календарь и
# location_ingest_token, и они не охранялись ничем.
#
# Класс объявляет ВЛАДЕЛЕЦ (решение 02.09, гибрид): объявление здесь — источник истины,
# фактическое наличие файла в каталогах — ВТОРОЙ независимый источник. Их расхождение
# («объявлено owner, а файл появился у партнёра») ловит integrity_tests и это не шум,
# а сигнал: либо ошибка раскатки, либо решение, которое забыли записать.
#   tenant — у каждого тенанта СВОЙ; читать только через secrets_dir()
#   owner  — один на систему; читать через owner_secrets_dir() (или дом-обёртку)
#   state  — НЕ секрет: состояние/кэш, оказавшееся в каталоге секретов (долг, Ф4)
SECRET_SCOPE: dict[str, str] = {
    # per-tenant: у владельца и партнёра разные значения
    "telegram_token": "tenant",
    "telegram_chat_id": "tenant",
    "oura_token": "tenant",
    "google_calendar_token.json": "tenant",
    # клиент ПРИЛОЖЕНИЯ, не пациента: client_id один на оба каталога (замер 02.09,
    # проект <gcp-project-id>). Тенантское — token.json и account: КТО вошёл
    "google_calendar_client.json": "owner",
    "google_calendar_account": "tenant",
    "location_ingest_token": "tenant",
    "hae_ingest_token": "tenant",       # 23.09: свой телефон — свой вход (было owner)
    "caldav.json": "tenant",            # 30.09: свой CalDAV-ящик напоминаний (reminders_backend, docker-install)
    # owner-level: один на систему, у партнёра отсутствует ОСОЗНАННО
    "anthropic_key": "owner",       # владелец платит за API всех тенантов
    # 02.10, llm-provider: ключ другого поставщика моделей (HEALTH_LLM_PROVIDER), тот же класс
    "openai_key": "owner",
    "gemini_key": "owner",
    "deepseek_key": "owner",
    "aqicn_token": "owner",         # общий погодный источник, не персональный
    "healthcheck_url": "owner",     # dead-man боевого монитора
    "sync_token": "owner",          # bot → Studio FastAPI
    # Почтовый канал (13–14.09, BL-STALLED-THREADS-1). `notify._email_settings`
    # читает их из `owner_secrets_dir()`: письмо шлёт СИСТЕМА владельцу, ящика у
    # тенанта нет и не заводится. Файлы появились на Studio 14.09 в 09:58–10:14 и
    # в тот же вечер датчик «реестр классов против каталогов» доложил о расхождении
    # — ровно то, ради чего он построен. `smtp_port` и `email_to` необязательны
    # (дефолты 465 и user), но читатель у них тот же, поэтому класс объявлен
    # заранее: иначе первый же положенный файл повторит этот отчёт.
    "smtp_host": "owner",
    "smtp_user": "owner",
    "smtp_password": "owner",
    "smtp_port": "owner",
    "email_to": "owner",
    # НЕ секрет: легаси-путь, который ЧИТАЕТСЯ как фолбэк. Дом heartbeat — logs/
    # (переезд 03.08); integrity держит старый путь, пока watchdog не отметится на
    # новом. Кэш погоды и state watchdog уехали из каталога секретов 02.09.
    "uncommitted_watchdog.heartbeat": "state",
}

ADMIN_GLOBAL_TG_ALLOW = frozenset({
    "test_failure_handler.py",
    "monthly_api_report.py",
    "model_health_check.py",
    "scripts/uncommitted_watchdog.py",
    "check_wellally_updates.py",
    # 2026-09-02, вместе с расширением периметра на *.sh: смотрит iCloud-каталог
    # ВЛАДЕЛЬЦА на его же MacBook, партнёрского аналога и плиста нет, шлёт только
    # basename конфликтной копии в собственный чат владельца. Owner-only по области —
    # тот же довод, что у пяти записей выше. Через notify.py не переводится осознанно:
    # это zsh под launchd с минимальным PATH, python-зависимость там дороже.
    "icloud_conflict_check.sh",
})
