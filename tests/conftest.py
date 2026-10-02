"""
tests/conftest.py — корневой conftest.

Делает:
1. Добавляет родительскую директорию (`health_scripts/`) в `sys.path`,
   чтобы тесты могли делать `import health_db`, `import gp_agent` и т.д.
2. Импортирует общие fixtures из `tests/fixtures/` через plugin-механизм pytest.
3. Авто-сбрасывает `_time_inject._TEST_CLOCK` после каждого теста, чтобы
   замороженное время одного теста не утекало в следующий.
4. Устанавливает `ALLOW_WRITE_NONPRIMARY=1` для тестов на MacBook —
   тесты могут писать в свою (in-memory) БД, не задеваая single-primary guard
   реальной health.db.

См. TEST_ARCHITECTURE.md §4 для перечня фикстур.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ── Path setup ──────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── Override single-primary guard для тестов ─────────────────────────────────
# Тесты используют либо in-memory SQLite (через fixture `db`), либо tmp-файл.
# Для прохождения через health_db.get_conn() WRITE-guard на MacBook нужен override.
# (На Studio — без эффекта, там и так primary.)
#
# ВНИМАНИЕ (2026-06-28): ALLOW_WRITE_NONPRIMARY обходит только WRITE-guard внутри
# get_conn(). Он НЕ обходит _validate_db_path(), который срабатывает РАНЬШЕ — на
# импорте health_db. Поэтому off-Studio без HEALTH_DATA_DIR ~65 тест-модулей с
# `import health_db` падают уже на КОЛЛЕКЦИИ (DB_PATH резолвится в iCloud → raise).
# Чтобы реально прогнать сьют на MacBook:
#     HEALTH_DATA_DIR=/tmp/health_test ALLOW_WRITE_NONPRIMARY=1 python3.11 -m pytest
# (+ установить numpy/scipy/pandas/fastapi/markdown). Канонический зелёный прогон —
# на Studio (primary host), здесь — лишь вспомогательный.
os.environ.setdefault("ALLOW_WRITE_NONPRIMARY", "1")

# ── BL-TESTFORK-1 (2026-07-10): HEALTH_DATA_DIR не задан → увести в tmp ─────
# Без этого тест без `db`-фикстуры, дёрнувший health_db.get_conn(), пишет в
# РЕАЛЬНЫЙ каталог данных машины. Явный HEALTH_DATA_DIR уважается: ночной прогон
# владельца (run_checks.sh --scheduled) задаёт канон ЯВНО и читает его, как раньше.
# До 2026-09-24 здесь было ещё условие «и машина не основная»: на основной машине без
# переменной тесты шли по боевому ~/health. У владельца так не бывает (переменная
# всегда задана), а у постороннего по уроку установки — ровно так: машина основная,
# переменной нет, и тесты пачкали его только что поставленную базу (приёмка урока
# свежим агентом 24.09: documents/*_probe.json, бэкапы, строки в daily_metrics).
import socket as _socket
import tempfile as _tempfile
# Облако прогона — ДО импорта infra_config (объяснение ниже): переменная доезжает и до
# дочерних процессов, которые тесты запускают.
_cloud_run = Path(_tempfile.mkdtemp(prefix="health_test_cloud_"))
os.environ["HEALTH_CLOUD_DIR"] = str(_cloud_run / "cloud")
import infra_config as _infra  # основная машина — private/infra.yaml

# ── Облако установки — не настоящее (28.09.2026, нить icloud-test-guard, §20) ─────────
# private/infra.yaml у владельца ведёт cloud_dir() в настоящий iCloud Drive, и модули
# считают от него константы при импорте (lab_extractor.HEALTH_DIR, import_all.HEALTH, …).
# На копии канона в базе лежат ОТНОСИТЕЛЬНЫЕ пути документов («CR/<файл>.pdf» в шести
# таблицах) — тест, склеивший их с облаком, открывает настоящий документ человека. Замер
# 28.09: полный прогон повис на 49% — pytest читал (fread из C-библиотеки PDF) файл в CR/,
# который iCloud выгрузил в dataless и не отдавал; висел и соседский прогон. Уводим облако
# и папку Health Auto Export в каталог прогона ДО импорта любого модуля. Облако — двумя
# путями: HEALTH_CLOUD_DIR выше (доезжает до дочерних процессов) и атрибут здесь (на случай,
# когда infra_config уже импортирован до conftest — так его запускает
# test_conftest_isolates_data_dir). Что переменная работает в дочернем процессе, судит
# tests/unit/test_icloud_guard.py. Тест, которому нужно своё облако, подменяет
# CLOUD_HEALTH_DIR сам (test_cloud_home).
_infra.CLOUD_HEALTH_DIR = _cloud_run / "cloud"
_infra.HAE_APP_DIR = _cloud_run / "hae_app"

# Страж на то, что подмена выше не закрывает: путь в настоящий iCloud, собранный мимо
# cloud_dir() (литерал, Path.home() до подмены). Открытие/листинг под НАСТОЯЩИМ домом
# (pwd, не Path.home: тесты его подменяют и строят «iCloud» во временной папке) роняет тест
# громко — тот же класс, что _no_real_telegram. Граница вслух: хук видит открытия из
# Python; C-библиотека, получившая путь строкой, его минует — её закрывает подмена выше.
import pwd as _pwd
_REAL_ICLOUD = os.path.join(_pwd.getpwuid(os.getuid()).pw_dir, "Library", "Mobile Documents")


def _icloud_guard(event, args):
    if event in ("open", "os.listdir", "os.scandir") and args:
        p = args[0]
        if isinstance(p, (str, os.PathLike)):
            p = os.fspath(p)
            if isinstance(p, str) and p.startswith(_REAL_ICLOUD):
                raise PermissionError(
                    f"тест дотянулся до НАСТОЯЩЕГО iCloud: {p[len(_REAL_ICLOUD):][:80]} — "
                    "это данные человека и файл может быть не скачан (§20); подмени путь")


sys.addaudithook(_icloud_guard)
if not os.environ.get("HEALTH_DATA_DIR"):
    # not .get() (а не setdefault): ловим и unset, и пустую строку HEALTH_DATA_DIR=
    # Каталог — СВОЙ на каждый прогон (до 24.09 был общий /tmp/health_test_data и переживал
    # прогоны: состояние прошлого прогона делало тесты зависимыми от истории машины).
    _run = _tempfile.mkdtemp(prefix="health_test_")
    os.environ["HEALTH_DATA_DIR"] = os.path.join(_run, "data_tenant")
    os.makedirs(os.path.join(os.environ["HEALTH_DATA_DIR"], "data"))
    # ── Тенанту — свой каталог секретов (12.09.2026, BL-AFFECTED-COLLECT-1) ──
    # Строка выше объявляет окружение ТЕНАНТОМ (HEALTH_DATA_DIR ≠ канон).
    # С 2026-09-05 `secrets_paths.secrets_dir()` в таком окружении отказывает,
    # если HEALTH_SECRETS_DIR не задан явно — и правильно делает: иначе тенант
    # взял бы секреты владельца, а это кросс-тенант утечка.
    # Но отказ происходит НА ИМПОРТЕ (bot/filters.py:35 зовёт secrets_dir()
    # на уровне модуля), поэтому 11 тестовых файлов перестали СОБИРАТЬСЯ, а
    # ошибка сборки обрывала весь прогон затронутых тестов. Замер 12.09:
    # 29 файлов не собиралось, из них 11 по этой причине.
    # Даём тенанту временный каталог с ФИКТИВНЫМИ секретами — ровно как у прогона-судьи
    # (scripts/test_on_studio.sh, шаг 3/4): до 24.09 он был пустым, и тесты бота, которым нужен
    # хоть какой-то chat_id, были красными везде, кроме судьи (3 на MacBook — снимок
    # publication ace6ab8 §6.2; 5 у постороннего по уроку — третья приёмка урока 24.09).
    # Настоящих значений здесь нет: реальная отправка перехвачена _no_real_telegram.
    # Каталог per-run, а не общий: два параллельных прогона не делят секреты.
    if not os.environ.get("HEALTH_SECRETS_DIR"):
        _sec = os.path.join(_run, "secrets")
        os.makedirs(_sec, mode=0o700)
        # Только то, без чего не собирается бот (владелец = chat_id, токен). Токенов внешних
        # сервисов (Oura, календарь) НЕТ намеренно: с фиктивным токеном импорт идёт в настоящий
        # API — четвёртая приёмка урока 24.09 поймала 401 от Oura из теста утреннего отчёта.
        for _name, _val in (("telegram_token", "STAGING_DUMMY"), ("telegram_chat_id", "999999999")):
            with open(os.path.join(_sec, _name), "w") as _fh:
                _fh.write(_val)
        os.environ["HEALTH_SECRETS_DIR"] = _sec
    # База прогона — такая же, как у свежей установки (тот же init_db, что зовёт установщик):
    # часть тестов читает засеянные справочники (шаблоны документов и т.п.), и на пустом
    # каталоге они краснели (третья приёмка урока 24.09). Отдельным процессом — чтобы не
    # импортировать health_db в conftest раньше, чем тесты расставят свои подмены.
    import subprocess as _sp, sys as _sys
    _sp.run([_sys.executable, "-c", "import health_db; health_db.init_db()"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=dict(os.environ), capture_output=True, timeout=180)

# ── Изоляция reference-БД PGS (2026-07-02) ───────────────────────────────────
# pgs_catalog/pgs_weights вынесены в общую reference-БД (pgs_reference.ref_db_path()).
# Без изоляции тесты «нет весов» приаттачили бы РЕАЛЬНУЮ 2.4GB reference и увидели
# 104 шкалы вместо пустоты. Направляем на изолированный пустой temp-файл; тесты с
# ЛОКАЛЬНЫМИ весами (создают pgs_weights в своей БД) идут local-путём без attach.
import tempfile as _tf
_pgs_test_db = os.path.join(_tf.gettempdir(), "health_test_pgs_ref.db")
try:
    if "HEALTH_PGS_DB" not in os.environ and os.path.exists(_pgs_test_db):
        os.remove(_pgs_test_db)   # свежий пустой reference на старте сессии
except OSError:
    pass
os.environ.setdefault("HEALTH_PGS_DB", _pgs_test_db)

# ── Плисты launchd для датчиков живости (нить schedule-liveness, 23.09) ──────
# Датчики живости судят свежесть по расписанию ЖИВОГО плиста (plist_env_liveness).
# Без шва тест судил бы по ~/Library/LaunchAgents машины: на Studio плисты есть, на
# MacBook нет — вердикт зависел бы от машины (§20). Расписания ниже повторяют живые на
# 23.09; тест, которому нужен «плиста нет», подменяет env на пустой каталог.
import plistlib as _plistlib
_LA_TEST = os.path.join(_tf.gettempdir(), "health_test_launchagents")
os.makedirs(_LA_TEST, exist_ok=True)
for _label, _cal in {
    "com.larry.health.test-suite": {"Hour": 0, "Minute": 0},
    "com.larry.health.night-cycle": {"Hour": 8, "Minute": 0},
    "com.larry.health.owner-nag": [{"Hour": h, "Minute": 0} for h in (8, 11, 14, 17, 20)],
    "com.larry.health.longitudinal": {"Weekday": 0, "Hour": 3, "Minute": 0},
    "com.larry.health.literature-search": {"Weekday": 0, "Hour": 4, "Minute": 0},
    "com.larry.health.consilium": {"Day": 1, "Hour": 4, "Minute": 0},
    "com.larry.health.triage": {"Hour": 8, "Minute": 0},
    "com.larry.health.backup": {"Hour": 3, "Minute": 0},
    "com.larry.health.integrity-check": {"Hour": 7, "Minute": 50},
    "com.larry.health.logrotate": 1800,          # StartInterval, секунды (не календарь)
}.items():
    _key = "StartInterval" if isinstance(_cal, int) else "StartCalendarInterval"
    with open(os.path.join(_LA_TEST, f"{_label}.plist"), "wb") as _fh:
        _plistlib.dump({"Label": _label, _key: _cal}, _fh)
os.environ.setdefault("HEALTH_LAUNCHAGENTS_DIR", _LA_TEST)

# ── Регистрация fixture-плагинов ─────────────────────────────────────────────
# Каждый модуль в `tests/fixtures/*.py` автоматически подключается как plugin.
# По мере добавления fixtures (T-0.3..T-0.8) — расширять список.
pytest_plugins: list[str] = [
    "tests.fixtures.time_travel",     # T-0.4 — Clock wrapper над _time_inject
    "tests.fixtures.db",              # T-0.3 — in-tmp-file SQLite со схемой
    "tests.fixtures.telegram",        # T-0.5 — capture/inject TG mock
    "tests.fixtures.anthropic",       # T-0.6 — scripted Anthropic mock
    "tests.fixtures.clinvar",         # T-0.7 — ClinVar/MyVariant mock
    "tests.fixtures.oura",            # T-0.7 — Oura API mock
    "tests.fixtures.hae",             # T-0.7 — HAE JSON mock
    "tests.fixtures.pubmed",          # T-0.7 — PubMed E-utilities mock
    "tests.fixtures.pending_labs",    # T-0.8 — tentative-write pending_labs/
    "tests.fixtures.dashboard",       # Wave 9 — TestClient(dashboard.app)
    # "tests.fixtures.anthropic",     # T-0.6
    # "tests.fixtures.clinvar",       # T-0.7
    # "tests.fixtures.oura",          # T-0.7
    # "tests.fixtures.hae",           # T-0.7
    # "tests.fixtures.pubmed",        # T-0.7
    # "tests.fixtures.pending_labs",  # T-0.8
]


# ── Схема канона для временных БД: ОДИН дом ─────────────────────────────────
import pytest


@pytest.fixture(autouse=True)
def _no_real_neighbors(monkeypatch):
    """Датчики и ремонт теста не обходят соседние проекты машины (§20).
    Проверки настроенного соседа явно подставляют синтетическую конфигурацию."""
    monkeypatch.setattr(_infra, "NEIGHBORS", {})


@pytest.fixture(autouse=True)
def fault_journal(tmp_path_factory, monkeypatch):
    """Сбои тестов не попадают в журнал, который читает настоящий integrity.

    Рядом — свежая отметка ночного ремонта: по умолчанию тест живёт в установке владельца, где
    чинящий есть, и fault отвечает «передал на починку». Установку без чинящего тесты строят
    сами (tests/unit/test_first_contact.py, нить first-contact 02.10)."""
    import time
    import notify
    # Свой каталог ВНЕ tmp_path теста: тесты, считающие файлы в tmp_path, не видят ни журнала,
    # ни отметки (полный прогон 02.10: два красных от лишнего файла).
    home = tmp_path_factory.mktemp("faults")
    path = home / "faults.jsonl"
    monkeypatch.setenv("HEALTH_FAULTS_JOURNAL", str(path))
    (home / notify.REPAIR_SEEN).write_text(str(int(time.time())))
    return path


@pytest.fixture(autouse=True)
def owner_weekly_journal(tmp_path, monkeypatch):
    """Понедельничный журнал и квитанция теста никогда не попадают в рабочие logs/."""
    path = tmp_path / "owner_weekly.jsonl"
    monkeypatch.setenv("HEALTH_OWNER_WEEKLY", str(path))
    return path


# ── Тест не ходит в настоящие импортёры данных (2026-09-24) ─────────────────
# Тот же класс, что _no_real_telegram (§20): тест, дотянувшийся до внешнего сервиса, сломан.
# Замер 24.09: test_send_morning_report_rearms_with_fresh_tz через bot.helpers.refresh_data
# запускал import_oura.py и import_apple_health.py ОТДЕЛЬНЫМИ ПРОЦЕССАМИ — с фиктивным
# токеном прогона-судьи они шли в настоящий API Oura (на Studio 637 КБ журнала ошибок 401).
# Два входа — процесс импортёра и прямой HTTP к api.ouraring.com; оба перехвачены. refresh_data
# глотает исключения, поэтому сторож не бросает, а КОПИТ попытки и роняет тест в teardown.
# Тест, который сам подменяет urlopen/subprocess.run, перекрывает сторожа своей подменой.
# 01.10.2026 сюда же — Telegram мимо notify: food_quarterly шлёт `curl … api.telegram.org`, а
# _no_real_telegram глушит только notify. В контейнере владельца каталог секретов прогона —
# настоящий (conftest подменяет секреты, лишь если переменная пуста), и ночной pytest 01.10
# прислал владельцу пищевой профиль ≥5 раз: каждый тест утреннего брифа = одна отправка.
# Сток общий — хост api.telegram.org в argv процесса или в URL, кто бы ни звал.
_IMPORTERS = ("import_oura.py", "import_apple_health.py")
_TELEGRAM_HOST = "api.telegram.org"


@pytest.fixture(autouse=True)
def _no_real_importers(monkeypatch):
    import subprocess as _sp
    import urllib.request as _ur
    attempts = []
    real_urlopen, real_run = _ur.urlopen, _sp.run

    def _urlopen(req, *a, **kw):
        url = str(getattr(req, "full_url", req))
        if "api.ouraring.com" in url or _TELEGRAM_HOST in url:
            attempts.append(url.rsplit("/", 1)[-1][:40] if _TELEGRAM_HOST in url else url[:80])
            raise OSError("тест не ходит в настоящий API Oura (conftest._no_real_importers)")
        return real_urlopen(req, *a, **kw)

    def _run(args, *a, **kw):
        argv = [str(x) for x in (args if isinstance(args, (list, tuple)) else [args])]
        if any(_TELEGRAM_HOST in x for x in argv):     # в URL токен бота — в журнал не пишем (§19)
            attempts.append("telegram: " + next(x for x in argv if _TELEGRAM_HOST in x).rsplit("/", 1)[-1])
            return _sp.CompletedProcess(args, 1, b"", b"blocked by conftest._no_real_importers")
        if any(x.endswith(_IMPORTERS) for x in argv):
            attempts.append(" ".join(argv)[-80:])
            return _sp.CompletedProcess(args, 1, b"", b"blocked by conftest._no_real_importers")
        return real_run(args, *a, **kw)
    monkeypatch.setattr(_ur, "urlopen", _urlopen)
    monkeypatch.setattr(_sp, "run", _run)
    yield
    assert not attempts, f"тест пошёл наружу (импортёр/Oura/Telegram): {attempts[:3]}"


@pytest.fixture(autouse=True)
def _no_quarterly_food_delivery(monkeypatch):
    """Квартальный пищевой профиль едет хвостом утреннего брифа (jobs/scheduled →
    food_quarterly.maybe_deliver). В тестах брифа он не предмет проверки, а в первые 7 дней
    квартала маркер у каждого прогона свежий → «пора слать» (замер 01.10: 7 тестов брифа).
    Сток уже перекрыт _no_real_importers; здесь — чтобы бриф-тесты не краснели о чужом.
    Тест самого food_quarterly подменяет deliver своим monkeypatch — его подмена сверху."""
    try:
        import food_quarterly as _fq
    except Exception:  # noqa: BLE001 — модуля нет в этом окружении: глушить нечего
        yield
        return
    monkeypatch.setattr(_fq, "deliver", lambda *a, **k: "")
    yield


# ── Тест не ходит в настоящий CalDAV (2026-09-30, docker-install) ─────────────────────────────
# Тот же класс, что _no_real_importers. Замер 30.09: полный прогон в контейнере владельца (каталог
# секретов прогона — настоящий, в нём caldav.json) завёл в ящике владельца список «Health (demo_alpha)»
# с задачей 📋 t — test_writer_puts_tenant_task_into_tenant_list пошёл мимо AppleScript в CalDAV.
# Два шага: caldav.json из каталога секретов, с которым стартовал прогон, для тестов не существует
# (тест со своим каталогом секретов видит свой файл), а HTTP-сессия CalDAV перехвачена всегда.
os.environ["HEALTH_TEST_BOOT_SECRETS"] = os.environ.get("HEALTH_SECRETS_DIR", "")


@pytest.fixture(autouse=True)
def _no_real_caldav(monkeypatch):
    import reminders_backend as _rb
    real_conf, attempts = _rb._conf, []

    def _conf():
        if os.environ.get("HEALTH_SECRETS_DIR", "") == os.environ.get("HEALTH_TEST_BOOT_SECRETS"):
            return None
        return real_conf()

    def _session(conf):
        attempts.append(str(conf.get("url", "?"))[:60])
        raise OSError("тест не ходит в настоящий CalDAV (conftest._no_real_caldav)")
    monkeypatch.setattr(_rb, "_conf", _conf)
    monkeypatch.setattr(_rb, "_session", _session)
    yield
    assert not attempts, f"тест пошёл в CalDAV: {attempts[:3]}"




def canon_schema(health_db) -> None:
    """Построить `lab_results` в текущей (временной) БД ПРОДАКШН-функциями.

    ЗАЧЕМ ЭТО ЗДЕСЬ, А НЕ CREATE TABLE В КАЖДОМ ТЕСТЕ. `init_db()` таблицу канона
    не создаёт (проверено замером: PRAGMA после init_db пуст), поэтому каждый тест
    промоута писал свой «минимальный lab_results» руками — и это был ВТОРОЙ ДОМ
    СХЕМЫ, расходящийся с продом молча.

    Что он стоил, замерено 2026-08-08: промоут начал везти `value_text`, и пять
    тестов покраснели на «no column named value_text» — не потому что сломалась
    логика, а потому что их фикстуры описывали таблицу, которой в проде нет. До
    этого те же пять были ЗЕЛЁНЫМИ на схеме беднее боевой, то есть проверяли не ту
    таблицу, о которой отчитывались (§20: зелёный обязан быть причинён механизмом).

    Здесь схема собирается теми же функциями, что на живой БД, поэтому следующая
    колонка приедет в тесты сама.
    """
    health_db._ensure_lab_table()
    with health_db.get_conn() as c:
        for ddl in ("ALTER TABLE lab_results ADD COLUMN specimen TEXT DEFAULT 'blood'",
                    "ALTER TABLE lab_results ADD COLUMN event_id INTEGER"):
            try:
                c.execute(ddl)
            except Exception:   # noqa: BLE001 — колонка уже есть: ровно семантика миграции
                pass
        c.commit()
    health_db._migrate_lab_value_op()
    health_db._migrate_lab_method()      # уникальный индекс строится поверх specimen


# ── Авто-сброс _time_inject после каждого теста ──────────────────────────────


@pytest.fixture(autouse=True)
def _reset_test_clock_after_each_test():
    """
    Автоматически вызывается для каждого теста. После завершения теста — снимает
    подмену времени, чтобы не утекало между тестами.

    Как использовать в тесте:
        from _time_inject import set_test_clock, get_today
        def test_something():
            set_test_clock("2026-05-08")
            assert get_today() == date(2026, 5, 8)
        # после теста время автоматически разморожено
    """
    yield
    try:
        from _time_inject import clear_test_clock
        clear_test_clock()
    except ImportError:
        # _time_inject ещё не существует или не на пути
        pass


# ── Глушитель исходящего Telegram: тест НЕ ИМЕЕТ ПРАВА отправить сообщение ──


@pytest.fixture(autouse=True)
def _no_real_guard_journal(monkeypatch, tmp_path):
    """Security-журнал LLM-гарда — в tmp у ВСЕХ тестов (2026-08-31).

    Тот же класс, что _no_real_telegram: тест, дотянувшийся до боевого
    артефакта, — не «реалистичный», а сломанный (§20). test_llm_client 42 дня
    писал мок-находки в боевой logs/llm_guard_blocks.log, и ночной датчик
    ежедневно докладывал владельцу о блокировках, которых не было. Глушим на
    самом низком уровне — env, который читает `_block_log_path` при вызове, —
    поэтому любой будущий тест гарда защищён без правки этого файла.
    """
    monkeypatch.setenv("HEALTH_LLM_GUARD_LOG", str(tmp_path / "llm_guard_blocks.log"))
    yield


# ПОЧЕМУ В СПИСКЕ НИЖЕ ЕСТЬ ЛИЧНОСТЬ И НЕТ ПУТИ УСТАНОВКИ GIT (13.09.2026).
# git зовёт хуки, передавая имя и почту автора/коммиттера в окружении. Песочный
# репозиторий теста, делая коммит, подписался бы ИМИ, а не собственным конфигом —
# и подпись вызывающего молча уехала бы в историю песочницы. Поэтому личность
# снимается. А вот путь установки самого git снимать нельзя: это не признак
# чужого репозитория, а то, чем git себя запускает.
# Пояснение стоит ЗДЕСЬ, а не внутри функции, намеренно: сторож списков
# (tests/unit/test_git_env_isolation.py) разбирает тело функции регулярным
# выражением по именам переменных и принял бы имена из комментария за часть
# списка — ровно тот ложно-зелёный, ради которого он и заведён. Замерено:
# первая редакция этой правки уронила сторожа именно так.
@pytest.fixture(autouse=True)
def _no_inherited_git_env(monkeypatch):
    """git-окружение вызывающего не доезжает до тестов (12.09.2026).

    Тот же класс, что _no_real_telegram и _no_real_guard_journal: тест,
    дотянувшийся до боевого артефакта, не «реалистичный», а сломанный. Здесь
    артефакт — чужая история git.

    git запускает хуки с GIT_DIR / GIT_INDEX_FILE / GIT_WORK_TREE в окружении,
    дочерний процесс их наследует, и `git -C <tmp>` молча работает НЕ с tmp, а
    с репозиторием, из которого пришёл хук. 12.09 так родился коммит `a251b93`
    в health_scripts: он удалил все отслеживаемые файлы, уехал на Studio, и
    хук поднял службы на пустом дереве.

    Первая починка накрыла ОДИН файл — тот, что сломался. Внешнее ревью
    пересчитало: git трогают девять тестовых файлов, чистка стояла в одном, а
    pre-commit гоняет затронутые тесты, не снимая окружения. Поэтому глушим
    на самом низком уровне — env у ВСЕХ тестов, включая те, которых ещё нет.

    Предел честно (переписан после третьего раунда ревью, О-15). Прежняя
    редакция обещала «вторую линию»: мол, код, который хук зовёт МИМО pytest,
    закрывает `unset` в самом хуке. Это было неверно дважды. Во-первых, `unset`
    в `scripts/git-hooks/pre-commit` стоит в подоболочке ровно вокруг вызова
    pytest — то есть страхует то же, что и фикстура, а не что-то сверх.
    Во-вторых, «вторая линия» там и не должна появляться: `affected_tests.py`
    и гейты хука читают `git diff --cached`, а это ИМЕННО тот индекс, который
    git передал через `GIT_INDEX_FILE` (частичный коммит, `git commit --only`).
    Снять переменные у них значило бы заставить гейты судить не тот индекс,
    который коммитится, — тихая подмена предмета суждения.

    Поэтому граница такая: git-окружение снимается там, где оно ВРЕДНО (тесты,
    которые создают свои репозитории), и сохраняется там, где оно НУЖНО (код
    хука, судящий staged-состояние). Двойная защита у прогона тестов — не
    избыточность: хук страхует случай, когда conftest не загрузился
    (`--noconftest`, сбор упал, прогон не из корня).
    """
    for var in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE",
                "GIT_COMMON_DIR", "GIT_PREFIX", "GIT_OBJECT_DIRECTORY",
                "GIT_NAMESPACE", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_QUARANTINE_PATH",
        "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE",
        "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE",
        "GIT_EDITOR"):
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.fixture(autouse=True)
def _no_real_parse_stats(monkeypatch):
    """Боевой счётчик разбора ответа модели тест НЕ ПИШЕТ. Механизм, не дисциплина.

    Инцидент 2026-09-14: ночной прогон доложил владельцу «3 полн. отказ(ов) за
    2026-09-14 … громче всех «__main__.py»». Продовых отказов не было ни одного —
    `telegram_bot.py` в тот же день дал 3 ok / 0 failed. Ведро `__main__.py`
    насыпал САМ pytest: `run_checks.sh` зовёт `$PY -m pytest`, и тогда
    `sys.argv[0]` = `…/pytest/__main__.py`, то есть путь ВНЕ репозитория.
    `task_agent._parse_source` отдаёт от такого пути одно имя, периметр имя
    классифицировать не может, а неизвестный источник датчик судит как ПРОД
    (fail-loud, направление выбрано осознанно 13.09 и остаётся). Воспроизведено на
    Studio: два тест-файла на tmp-БД дали
    `{"__main__.py": {"ok": 2, "partial": 5, "failed": 2}}`.

    Producer'а ДВА, и второй нашёлся во время уборки: code-watcher на Studio гоняет
    КОНСОЛЬНЫЙ `pytest` (argv[0] = …/bin/pytest → имя `pytest`), и пока шла эта
    правка, он насыпал в канон ещё одно ведро с 2 отказами. Поэтому глушитель стоит
    не на имени запускающего, а на стоке: имена запуска pytest перечислять — значит
    завести список, который разъедется с реальностью ровно так же.

    Класс не новый и уже стоил 42 дня: `test_llm_client` писал мок-находки в боевой
    `logs/llm_guard_blocks.log`, и ночной датчик ежедневно докладывал о блокировках,
    которых не было (см. `_no_real_guard_journal`). Лечение то же — глушитель на
    границе, а не правка тестов по одному: любой БУДУЩИЙ тест, дёрнувший разбор,
    закрыт без правки этого файла.

    Глушим НИЖЕ `_note_parse_outcome`, у самого стока: тест, которому счётчик нужен
    как ПРЕДМЕТ (`_capture_stats` в `test_question_tails`), кладёт свою подмену
    `config_db` поверх нашей и по-прежнему видит запись.

    Граница честно: на Studio тесты бегут на КАНОНИЧЕСКОЙ БД — строка выше уводит
    `HEALTH_DATA_DIR` в tmp только на не-Studio. Значит любой боевой ключ, который
    тронет тест, попадёт в канон. Здесь закрыт ОДИН ключ, у которого есть читатель-
    датчик и уже был ложный красный; класс целиком — долг, не эта правка.
    """
    try:
        # health_db ПЕРВЫМ, и это не стиль: `config_db` на строке 10 импортирует
        # health_db, а health_db в конце файла берёт из config_db `get_config` —
        # первый импорт config_db в свежем процессе падает круговым импортом.
        # Замерено 14.09: без этой строки глушитель молча не вставал на прогоне
        # с селектором (try/except глотал ImportError), и оракул краснел под
        # чужим именем.
        import health_db  # noqa: F401
        import config_db
        import task_agent
    except Exception:  # noqa: BLE001 — модуля нет в этом окружении: глушить нечего
        yield
        return

    _real = config_db.upsert_config
    _key = task_agent.PARSE_STATS_KEY

    def _guarded(key=None, *a, **kw):
        if (key if key is not None else kw.get("key")) == _key:
            return None      # запись боевого счётчика из-под pytest не доезжает
        return _real(key, *a, **kw)

    _guarded.muzzles_parse_stats = True     # опора оракула: см. test_question_tails
    monkeypatch.setattr(config_db, "upsert_config", _guarded)
    yield


@pytest.fixture(autouse=True)
def _no_real_telegram(monkeypatch):
    """Физически отрезает доставку у всех тестов. Не дисциплина — механизм.

    Инцидент 2026-08-01: в модуле переименовали вызов `notify.notify` →
    `notify.notify_operator`, а монкипатчи тестов патчили СТАРОЕ имя. Новое осталось
    непокрытым, и прогон `tests/unit/test_lab_intake_watcher.py` ушёл в НАСТОЯЩИЙ
    Telegram владельца — четыре сообщения про `new.pdf`/`bad.pdf`/`notatable.jpg`
    с битыми ссылками на тестовые run_id.

    Класс шире переименования: любой НОВЫЙ путь доставки по умолчанию не покрыт
    ничьим моком, и узнаёшь об этом по сообщению на телефоне. Глушим на самом низком
    уровне — `_telegram`/`_healthcheck_fail`, — поэтому любой будущий публичный вход
    в `notify` глушится автоматически, без правки этого файла.

    Тест, которому нужен ИМЕННО вызов, по-прежнему патчит что хочет своим monkeypatch:
    его подмена ложится поверх нашей.
    """
    try:
        import notify
    except Exception:  # noqa: BLE001 — модуль недоступен в этом окружении: глушить нечего
        yield
        return

    def _blocked(msg, secrets=None):
        raise AssertionError(
            "тест попытался отправить НАСТОЯЩЕЕ сообщение через notify — "
            f"замокай точку вызова. Текст: {str(msg)[:120]!r}")

    monkeypatch.setattr(notify, "_telegram", _blocked, raising=False)
    monkeypatch.setattr(notify, "_healthcheck_fail", _blocked, raising=False)
    yield


# ── Глушитель исходящего LLM: тест НЕ ИМЕЕТ ПРАВА позвать настоящий Anthropic ──


@pytest.fixture(autouse=True)
def _no_real_anthropic(monkeypatch):
    """Симметрия с _no_real_telegram, поставлена 2026-08-03.

    Класс тот же и уже реализовался на Telegram: любой НОВЫЙ путь наружу по
    умолчанию не покрыт ничьим моком, и узнаёшь об этом по счёту от вендора.
    Замер того же дня: клиент Anthropic конструируется 27 раз в 21 модуле,
    у семи из них (monthly_consilium, correlation_analysis, generate_constitutions
    и др.) нет функции-seam вроде `get_client` — то есть подменять там нечего,
    и один недосмотр уводит тестовый прогон в настоящий API с медданными в теле.

    Глушим на самом низком уровне — конструкторе клиента, — поэтому любой будущий
    вызов глушится автоматически, без правки этого файла. Тест, которому нужен
    клиент, по-прежнему кладёт свою подмену поверх (monkeypatch того же атрибута
    или модульного `get_client`).
    """
    try:
        import anthropic
    except Exception:  # noqa: BLE001 — пакета нет: глушить нечего
        yield
        return

    def _blocked(*a, **kw):
        raise AssertionError(
            "тест попытался создать НАСТОЯЩИЙ клиент Anthropic — замокай точку "
            "вызова (get_client модуля) или llm_client.guarded_client. Ключ в тесте не нужен.")

    monkeypatch.setattr(anthropic, "Anthropic", _blocked, raising=False)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _blocked, raising=False)
    yield


# ── Снимок рабочего дерева как git-репозиторий (для контролей, которым нужен репозиторий) ──
import atexit as _atexit
import shutil as _shutil
import subprocess as _subprocess

_GIT_SRC_CACHE: dict[str, str] = {}
# Замок нужен с 2026-09-14: ратчет известных обходов зовёт снимок из НЕСКОЛЬКИХ потоков, и без
# него два потока строят две копии одного дерева, а «кэш точечный по времени» перестаёт быть правдой.
import threading as _threading
_GIT_SRC_LOCK = _threading.Lock()


def git_bearing_src(root) -> str:
    """Одноразовый репозиторий, HEAD которого равен РАБОЧЕМУ ДЕРЕВУ `root`.

    Зачем. Санкционированный прогон `scripts/test_on_studio.sh` rsync'ает WIP-дерево в
    `~/health_staging` БЕЗ `.git` — намеренно: история и канон не покидают Studio. Любой
    контроль, которому нужен настоящий репозиторий, в этой среде падает на `git clone`
    с exit 128. Это ровно VG-R5-10 нити `validation-gate-repair`: постоянная краснота
    НОСИТЕЛЯ, из-за которой регрессию соседней подсистемы не отличить от поломки среды.

    Опаснее самой красноты то, что она среде-зависима: на Studio (`~/health_scripts` с
    `.git`) тот же набор зелёный. Контроль, чей вердикт зависит от того, где его запустили,
    не является оракулом — он ложно-зелёный на одном пути и ложно-красный на другом.

    ПОЧЕМУ СНИМОК СТРОИТСЯ ВСЕГДА, а не только при отсутствии `.git`. Первая версия этой
    функции отдавала `root` как есть, если `.git` на месте. Это чинило падение и заводило
    дефект потише: потребитель клонирует SRC, а `git clone` берёт HEAD. Значит на машине с
    историей контроль судил ПОСЛЕДНИЙ КОММИТ, а в staging (снимок собран из WIP) — рабочее
    дерево. Один и тот же контроль выносил вердикт о РАЗНЫХ версиях кода в зависимости от
    того, где его запустили. Воспроизведено: маркер, дописанный в `project_context/dispgate.py`
    без коммита, в клоне отсутствовал.

    Для pre-commit контроля правильный ответ ровно один — судить то, что у тебя в руках.
    Поэтому ветка снята: снимок собирается всегда, обе среды судят рабочее дерево.
    Это же соображение уже записано в `test_dispgate_hook.py::_clone` («контроль обязан
    судить ТЕКУЩИЙ гейт, а не закоммиченный») — там оно решено доп. синхронизацией поверх
    клона. Здесь решено устранением развилки.

    Кэш. Снимок точечный по времени и живёт до конца сессии pytest: два потребителя в одном
    прогоне судят одну и ту же копию. Правка дерева ПОСЛЕ первого вызова в этот прогон
    не попадёт — сознательный размен на стоимость copytree.

    Второй экземпляр приёма — `test_dispgate_hook.py::_clone`; сведение к общему дому
    заведено долгом, а не обещанием в докстроке (см. BACKLOG).
    """
    root = str(root)
    with _GIT_SRC_LOCK:
        return _git_bearing_src_locked(root)


def _git_bearing_src_locked(root: str) -> str:
    if root in _GIT_SRC_CACHE:
        return _GIT_SRC_CACHE[root]
    dst = _tempfile.mkdtemp(prefix="git-bearing-src-")
    # Уборка при выходе процесса — снимок живёт ровно столько, сколько _GIT_SRC_CACHE.
    # Замер 2026-09-29: без неё каждый прогон оставлял в $TMPDIR копию дерева ~150 МБ,
    # 234 копии за 3 дня заполнили диск Studio. Оракул — tests/unit/test_git_bearing_src_cleanup.py.
    _atexit.register(_shutil.rmtree, dst, True)
    repo = os.path.join(dst, "repo")
    _shutil.copytree(root, repo, symlinks=True,
                     ignore=_shutil.ignore_patterns(".git", "__pycache__", "logs", "outputs",
                                                    "*.pyc", ".pytest_cache"))
    _subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    # Фоновое обслуживание ВЫКЛЮЧЕНО, и это не гигиена, а починка (замер 2026-09-14).
    # git 2.54 после каждого `commit` запускает `maintenance run --auto` ОТДЕЛЬНЫМ ПРОЦЕССОМ.
    # В снимке 1710 объектов, обслуживание их перепаковывает и УДАЛЯЕТ россыпь — а потребитель
    # (`git clone` этого снимка) в это время обходит каталог объектов. Он видит файл в листинге
    # и не находит его при копировании: «fatal: failed to copy file … No such file or directory»
    # либо «unable to read tree». Гонка объясняет всё, что раньше списывали на МЕСТО прогона
    # (BL-DISPGATE-RATCHET-ENV-1): снимок цел до, цел после, и fsck в обе стороны зелёный —
    # ломается только окно между. Проверено: после падения у снимка 0 россыпи и 1 пак.
    _subprocess.run(["git", "config", "gc.auto", "0"], cwd=repo, check=True, capture_output=True)
    _subprocess.run(["git", "config", "maintenance.auto", "false"],
                    cwd=repo, check=True, capture_output=True)
    _subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    _subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                     "commit", "-qm", "worktree snapshot under test", "--no-verify"],
                    # check=True с 2026-09-14: тихий отказ коммита оставлял репозиторий БЕЗ HEAD,
                    # клон с него выходил пустым, и потребитель падал под чужим именем
                    # («оснастка не напечатала ни одной пробы»), а не под своим.
                    cwd=repo, check=True, capture_output=True)
    # Снимок обязан быть ЦЕЛЫМ в момент рождения, и это проверяется здесь, а не у потребителя.
    # 2026-09-14: потребитель (ратчет обходов §15) видел «unable to read tree» и сообщал
    # «оснастка не напечатала ни одной пробы» — то есть отказ приезжал под чужим именем через
    # два слоя. Связность считается быстро (--connectivity-only) и отвечает ровно на тот вопрос,
    # ради которого снимок делается: можно ли его клонировать. Полный fsck, не --connectivity-only:
    # пропущенный блоб клон уронит, а связность его не смотрит.
    _fsck = _subprocess.run(["git", "fsck", "--no-progress"],
                            cwd=repo, capture_output=True, text=True)
    if _fsck.returncode != 0:
        raise RuntimeError(
            f"снимок рабочего дерева родился неполным: {repo}\n"
            f"{(_fsck.stdout + _fsck.stderr).strip()[:800]}")
    _GIT_SRC_CACHE[root] = repo
    return repo


# ── owner_data: тесты правил и данных владельца (решение владельца 2026-09-24) ──
# Репетиция экспорта показала: 113+ тестов проверяют ЕГО пороги, свод, питание и
# конфиг; у постороннего их данных нет, и тесты красные при любом раскладе. Решение
# «разделить»: в публичной выгрузке такие тесты ПРОПУСКАЮТСЯ с причиной, у владельца
# бегут как раньше и краснеют, если данных нет (пропуск только по признаку выгрузки,
# не по отсутствию файла — иначе исчезнувшие данные владельца выглядели бы зелёным).
def pytest_collection_modifyitems(config, items):
    # host_only (решение владельца 30.09, вариант Б): в контейнере нет станка разработчика — git,
    # хуков, Homebrew, zsh, launchd. Замер 30.09: 180 красных полного прогона в контейнере владельца,
    # все, кроме одного файла (CalDAV — лечится _no_real_caldav), — этого рода. Судятся они на Studio
    # в каждом закрытии нити; признак — метка образа HEALTH_RUNTIME, не отсутствие git (иначе у
    # разработчика без git тесты исчезали бы молча).
    if os.environ.get("HEALTH_RUNTIME") == "container":
        host_skip = pytest.mark.skip(reason="host_only: судит машину-хост (git/хуки/Homebrew/launchd) — "
                                            "в контейнере не судим, бежит на Studio в закрытии нити")
        for item in items:
            if item.get_closest_marker("host_only"):
                item.add_marker(host_skip)
    # owner_env (окружение владельца: канон, настоящие секреты) — тот же признак, та же причина.
    # Умолчание pytest не трогаем: ночной прогон владельца гоняет owner_env через addopts,
    # и отсев по метке там молча выкинул бы их из ночи (урок 15.09 про невидимые тесты).
    marked = [i for i in items if i.get_closest_marker("owner_data") or i.get_closest_marker("owner_env")]
    if not marked:
        return
    import pii_census
    if not pii_census.is_public_export():
        return
    skip = pytest.mark.skip(reason="owner_data: правила, данные или окружение владельца — "
                                   "в публичной выгрузке их нет (pii_census.is_public_export)")
    for item in marked:
        item.add_marker(skip)


# ── Тест не ходит к настоящим OpenAI и Google (2026-10-02, нить llm-provider) ──────────────────
# Тот же класс, что _no_real_importers (C-111, C-122): новый канал приходит вместе со стражем.
# Обе родные библиотеки ходят через httpx — перехват на уровне отправки запроса закрывает и
# синхронный, и асинхронный клиент, кто бы их ни построил. Хосты — из профилей провайдеров
# (methodology/llm_providers.json), а не списком здесь: новый провайдер в профиле — сразу под стражем.
def _llm_foreign_hosts() -> set:
    import json as _json
    p = Path(__file__).resolve().parents[1] / "methodology" / "llm_providers.json"
    try:
        prof = _json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    return {h for k, v in prof.items() if not k.startswith("_") and k != "anthropic"
            for h in v.get("hosts", [])}


@pytest.fixture(autouse=True)
def _no_real_llm_providers(monkeypatch, request):
    try:
        import httpx as _hx
    except Exception:  # noqa: BLE001 — httpx нет: и чужих библиотек тоже нет
        yield
        return
    hosts, attempts = _llm_foreign_hosts(), []
    real_send, real_asend = _hx.Client.send, _hx.AsyncClient.send

    def _send(self, request, *a, **kw):
        if request.url.host in hosts:
            attempts.append(request.url.host)
            raise _hx.ConnectError("blocked by conftest._no_real_llm_providers", request=request)
        return real_send(self, request, *a, **kw)

    async def _asend(self, request, *a, **kw):
        if request.url.host in hosts:
            attempts.append(request.url.host)
            raise _hx.ConnectError("blocked by conftest._no_real_llm_providers", request=request)
        return await real_asend(self, request, *a, **kw)
    monkeypatch.setattr(_hx.Client, "send", _send)
    monkeypatch.setattr(_hx.AsyncClient, "send", _asend)
    yield
    if getattr(request.function, "llm_block_expected", False):   # позитивный контроль стража
        return
    assert not attempts, f"тест пошёл к настоящему LLM-провайдеру: {sorted(set(attempts))}"
