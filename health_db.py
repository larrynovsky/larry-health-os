#!/usr/bin/env python3.11
"""
SQLite-слой для health системы.
Таблицы: daily_metrics, experiments, experiment_log, checkins, patterns, recommendations
"""
from __future__ import annotations  # PEP 563: lazy annotations, совместимо с Python 3.9+

import json
import socket
import sqlite3
import logging
import i18n
import os
from datetime import date, timedelta
from pathlib import Path

from _time_inject import get_today, get_utcnow

_STUDIO_DEFAULT = str(Path.home() / "health")

# INTENT: data_ingestion — приём данных: единая каноническая база (single-primary).
#          Замысел и инварианты — subsystem_intent.yaml, раздел data_ingestion.
# ── Single-primary guard (CLAUDE.md §8, USE_CASES.md UC-I-07) ──
# Запись в health.db разрешена только на Studio. На MacBook коннект идёт в
# read-only режиме (через SQLite mode=ro): любой INSERT/UPDATE/DELETE упадёт с
# `attempt to write a readonly database`. Override для разовых миграций:
# ALLOW_WRITE_NONPRIMARY=1.
import infra_config as _infra
# Имя основной машины — данные установки (private/infra.yaml, infra_config.PRIMARY_HOST);
# имя символа сохранено для читателей и реестра замысла (data_ingestion, present).
_PRIMARY_HOST = _infra.PRIMARY_HOST
# Легаси-дефолт НЕ основной машины — облачная реплика (у владельца iCloud/health). Путь — настройка
# установки (infra_config.CLOUD_HEALTH_DIR, BL-PUB-12), не литерал; облака нет → None, и процесс без
# HEALTH_DATA_DIR на не основной машине падает в _resolve_health_dir, а не открывает случайный каталог.
_ICLOUD_DEFAULT = str(_infra.CLOUD_HEALTH_DIR) if _infra.CLOUD_HEALTH_DIR else None

# A++ (2026-06-19, ICLOUD-STUB-FIX): DB не может жить в cloud-sync folder.
# Любой `sqlite3.connect(path)` где path в одной из этих директорий создаёт
# split-brain hazard — iCloud/Dropbox/etc реплицируют .db без merge-protocol
# (инцидент 2026-06-18). Whitelist через _validate_db_path() в module-load.
_FORBIDDEN_DB_PATH_SUBSTRINGS = (
    "Library/Mobile Documents/com~apple~CloudDocs",  # iCloud Drive
    "Dropbox",
    "Google Drive",
    "OneDrive",
    "Box Sync",
)


def _resolve_health_dir() -> str:
    """Hostname-aware default для HEALTH_DATA_DIR.
    BUG-SPLIT-BRAIN-DEFAULT fix (2026-05-11): на Studio дефолт ~/health
    (локальный диск, не iCloud), на MacBook — iCloud-копия (read-only реплика).
    Явный HEALTH_DATA_DIR всегда приоритетнее."""
    if explicit := os.environ.get("HEALTH_DATA_DIR"):
        return explicit
    # MULTITENANT-GUARD (2026-06-29, multitenancy Phase 0, R1): когда система
    # обслуживает >1 пользователя, тихий дефолт = мина — процесс без
    # HEALTH_DATA_DIR молча берёт данные дефолтного тенанта (риск выдать одному
    # человеку медвывод по данным другого). Предохранитель опт-ин: пока
    # HEALTH_MULTITENANT не выставлен, поведение не меняется (single-tenant,
    # Studio green). В день добавления второго пользователя флаг включается
    # глобально → любой процесс без явного HEALTH_DATA_DIR падает громко.
    if os.environ.get("HEALTH_MULTITENANT"):
        raise RuntimeError(
            "HEALTH_MULTITENANT=1, но HEALTH_DATA_DIR не задан. В мультитенантном "
            "режиме каталог тенанта обязателен явно — тихий дефолт запрещён (R1: "
            "иначе процесс одного пользователя возьмёт данные другого). Задай "
            "HEALTH_DATA_DIR=<каталог тенанта> для этого процесса."
        )
    if _infra.is_primary():
        return _STUDIO_DEFAULT
    if _ICLOUD_DEFAULT is None:
        raise RuntimeError(
            "Эта машина не основная (private/infra.yaml: primary_host), HEALTH_DATA_DIR не задан, "
            "облачной реплики нет (cloud_health_dir). Задай HEALTH_DATA_DIR или запусти на основной "
            "машине (§8).")
    return _ICLOUD_DEFAULT


def _validate_db_path(path: Path) -> None:
    """A++ (2026-06-19, ICLOUD-STUB-FIX): запрещаем DB_PATH в cloud-sync folder.

    Закрывает класс инцидентов 2026-06-18: любой `sqlite3.connect(DB_PATH)` —
    в том числе обходящие get_conn() — создавал stub в iCloud-tree если
    DB_PATH туда указывал. iCloud Drive реплицирует .db без merge → split-brain.

    На Studio с HEALTH_DATA_DIR=~/health → ok.
    На MacBook без HEALTH_DATA_DIR → DB_PATH=iCloud → raise при импорте модуля.
    На MacBook с HEALTH_DATA_DIR=iCloud (явный override) → raise (defense-in-depth).
    """
    resolved = str(path.resolve())
    for forbidden in _FORBIDDEN_DB_PATH_SUBSTRINGS:
        if forbidden in resolved:
            raise RuntimeError(
                f"DB_PATH ({path}) лежит в cloud-sync folder ({forbidden}). "
                f"Это нарушает R1/R2 (CLAUDE.md §8: health.db ТОЛЬКО на локальном "
                f"диске Studio в ~/health/data/). Запусти на Studio, либо задай "
                f"HEALTH_DATA_DIR на локальный путь."
            )


_HEALTH_DIR = Path(_resolve_health_dir())
ICLOUD = _HEALTH_DIR  # обратная совместимость
DB_PATH = _HEALTH_DIR / "data" / "health.db"
METRICS_DIR = _HEALTH_DIR / "data" / "daily_metrics"

# A++ enforce на module load — закрывает direct sqlite3.connect(DB_PATH) callers,
# которые обходят get_conn() guard (20 каллеров до миграции Sprint 2).
# На non-primary без HEALTH_DATA_DIR это raise → import health_db падает → все
# зависящие модули падают тоже (желаемое поведение: MacBook не имеет DB access).
_validate_db_path(DB_PATH)


def _is_primary() -> bool:
    """True если этот процесс на primary-узле или есть явный override."""
    if os.environ.get("ALLOW_WRITE_NONPRIMARY"):
        return True
    return _infra.is_primary()


# Канонический путь БД владельца — тот, который держит боевое состояние. Считается
# от HOME, а не берётся из DB_PATH: DB_PATH и есть то, что мы проверяем, и сравнивать
# его с самим собой бессмысленно.
CANONICAL_DB_PATH = Path.home() / "health" / "data" / "health.db"


def _file_identity(p: "Path") -> "tuple | None":
    """(устройство, inode) — тождество ФАЙЛА, а не имени. None = не удалось узнать."""
    try:
        st = p.stat()
        return (st.st_dev, st.st_ino)
    except OSError:
        return None


def on_canonical_db() -> bool:
    """Смотрит ли ЭТОТ процесс в боевую БД владельца.

    ДВА признака, и второй появился по замеру У-2 (2026-07-29), а не по рассуждению.
    Замер: канон напрямую → отказ · снимок → пустил · СИМЛИНК на канон → отказ
    (`resolve()` разворачивает) · **ЖЁСТКАЯ ССЫЛКА на боевой файл → ПУСТИЛ**. У hardlink
    нет «настоящего» имени: оба пути равноправны, `resolve()` бессилен, а запись идёт в
    тот же inode — то есть в канон, пока гард уверяет, что это снимок.

    Я ошибался, когда назвал эту дыру закрываемой «только сверкой содержимого, то есть
    чтением канона ради проверки, что мы его не читаем». Достаточно `stat`: устройство и
    inode — метаданные, байты не читаются. Граница была названа честно, а оценена дороже,
    чем стоит, и потому осталась дырой вместо починки.

    Чего ЭТО не ловит: КОПИЮ канона под чужим именем. И не должно — копия с идентичными
    данными и есть штатный снимок (решение Р-7); запись в неё канона не касается.
    """
    try:
        if Path(DB_PATH).resolve() == CANONICAL_DB_PATH.resolve():
            return True
    except OSError:                      # путь не резолвится — идём ко второму признаку
        pass
    mine = _file_identity(Path(DB_PATH))
    return mine is not None and mine == _file_identity(CANONICAL_DB_PATH)


def assert_not_canonical(purpose: str, *, allow_env: str) -> None:
    """FAIL-CLOSED гард для операций, которым НЕЛЬЗЯ трогать боевое состояние.

    Решение владельца Р-7/Р-8 (2026-07-29): живые фазы проб исполняются в
    `~/health_staging` — настоящий код на снимке канона, все записи в копию.
    Раньше это держалось на памяти запускающего: проба не спрашивала разрешения и
    не печатала, куда пишет. За один день 2026-07-29 пробы трижды запускались по
    канону через PYTHONPATH — работало потому, что так и хотели; в следующий раз
    могло не совпасть.

    §13, ступень 2: безопасного авто-действия здесь нет (угадывать за человека,
    куда он хотел писать, нельзя), но остановить вредное можно без вопроса.
    Поэтому raise, а не warn.

    Обход существует и он ЯВНЫЙ — переменная `allow_env`. Это не люк: он именной,
    печатается в отказе, и его появление в истории команд видно. Тихого обхода нет.
    """
    if not on_canonical_db():
        return
    if os.environ.get(allow_env):
        log.warning("%s: канон разрешён явным %s=1 — решение владельца", purpose, allow_env)
        return
    raise RuntimeError(
        f"{purpose}: ОТКАЗ — процесс смотрит в КАНОНИЧЕСКУЮ БД {DB_PATH}.\n"
        f"Живые фазы проб исполняются на снимке (решение владельца Р-7, 2026-07-29):\n"
        f"  ./scripts/test_on_studio.sh   # синхронизирует дерево и снимок в ~/health_staging\n"
        f"  ssh Studio 'cd ~/health_staging && HEALTH_DATA_DIR=~/health_staging "
        f"HEALTH_SECRETS_DIR=~/health_staging/.secrets_staging python3.11 <проба> --phase live'\n"
        f"Осознанный обход (по слову владельца): {allow_env}=1"
    )


log = logging.getLogger(__name__)


class _ClosingConn(sqlite3.Connection):
    """B-1 fix (2026-05-22, roadmap / BUG-FD-LEAK-INTEGRATION):
    подкласс sqlite3.Connection с close() в __exit__.

    Стандартный sqlite3.Connection.__exit__ делает commit/rollback но
    **НЕ close**. Это вызывало накопление fd: каждый `with get_conn() as c:`
    оставлял открытое соединение пока GC не подберёт. На pytest суите
    из 700+ тестов это пробивало ulimit -n 256 → OSError: Too many open files.

    Backward-compat: __enter__ наследуется (возвращает self), `conn.close()`
    работает как обычно (наследуется), `conn.execute(...)` работает.
    Меняется только поведение `with` — теперь закрывает соединение.
    """
    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()


# ───────────── справочники: ОБЩИЙ файл, а не копия в каждом тенанте ─────────────
# LOINC (60 009 терминов, 2.5 млн синонимов, ≈190 МБ) жил внутри health.db и
# унаследовал всю его дисциплину: снапшот на каждый прогон набора, бэкапы,
# единственный писатель. Ни одна из этих гарантий справочнику не нужна — он не
# правится по строке, а перезаливается целиком из релиза за 7 секунд, и версия
# записана в каждой строке (`loinc_version`). Это read-only-кэш в терминах
# Таненбаума §7.5.4: обновления делает только источник, читатели только читают.
#
# Второй довод сильнее места: у справочника СВОЙ цикл жизни. LOINC выходит дважды
# в год и переносит устаревшие коды через MapTo.csv, а `lab_name_loinc` хранит
# клинические решения владельца, ссылающиеся на коды. Пока обе таблицы в одном
# файле, вопрос «а совпадают ли они по версии» даже не формулируется. Разделив,
# мы делаем разъезд видимым — под него есть датчик.
#
# Файл ОДИН на все тенанты: справочник не содержит данных пациента, и держать его
# копию в каждом каталоге значило бы платить 190 МБ за человека.
_REFERENCE_DEFAULT = str(Path.home() / "health_reference")
REFERENCE_DIR = Path(os.environ.get("HEALTH_REFERENCE_DIR") or _REFERENCE_DEFAULT)
LOINC_DB_PATH = REFERENCE_DIR / "loinc.db"

# Таблицы справочника. Список закрыт намеренно: он же используется датчиком,
# который следит, что эти таблицы НЕ завелись обратно в каноне. Незакрытый список
# означал бы, что датчик проверяет не то, что мы разделили.
REFERENCE_TABLES = ("loinc_terms", "loinc_synonyms", "loinc_ru")


def attach_reference(conn: sqlite3.Connection) -> None:
    """Подключить общий справочник к соединению как схему `ref`.

    Имена таблиц НЕ меняются для читателей: SQLite ищет неквалифицированное имя
    сначала в main, потом в присоединённых. Это удобно и одновременно опасно —
    если старые таблицы останутся в каноне, читатель молча возьмёт их, и мы
    получим зелёный тест на устаревшем справочнике (§12). Поэтому разделение
    обязано ДРОПНУТЬ их из канона, а датчик — следить, что они не вернулись.

    ОГРАНИЧЕНИЕ, которое нельзя потерять: health.db в режиме WAL, а в SQLite
    транзакция через несколько присоединённых БД в WAL НЕ атомарна. Значит писать
    в `lab_name_loinc` (канон) и в `loinc_terms` (справочник) одной транзакцией
    нельзя. Сейчас мы этого и не делаем — записи независимы.
    """
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    conn.execute("ATTACH DATABASE ? AS ref", (str(LOINC_DB_PATH),))


def _test_run_on_live_db() -> bool:
    """Прогон тестов смотрит в ЖИВУЮ базу тенанта → коннект только на чтение.

    conftest кладёт в HEALTH_TEST_LIVE_DB путь живой базы, когда HEALTH_DATA_DIR задан явно
    (утренний прогон владельца так и делает: часть тестов читает настоящие данные). Писать
    туда тестам нельзя ни при каких условиях. Замер 2026-10-04: в medications владельца
    нашлись строки фикстур tests/unit/test_treatment.py, записанные прогоном тестов 2026-06-22;
    контекст консилиума с тех пор показывал режимы дважды.
    Сравнение по устройству+inode (как on_canonical_db): фикстура `db` с tmp-базой не задета.
    Граница: прямые sqlite3.connect мимо get_conn этим не закрыты.
    """
    live = os.environ.get("HEALTH_TEST_LIVE_DB")
    if not live:
        return False
    mine = _file_identity(Path(DB_PATH))
    return mine is not None and mine == _file_identity(Path(live))


def _frozen_copy() -> bool:
    """Нативная копия тенанта, переехавшего в контейнер, заморожена: вне контейнера — только чтение.

    Решение владельца 29.09: документы владельца — только через бота в контейнер; его нативные
    приёмы из папки iCloud сняты (placement.yaml: import-poll, watcher — none). Но get_conn на
    хосте открывал ~/health/data/health.db на запись без вопроса (замер 06.10): ручной или
    случайно загруженный старый импорт молча писал бы документы в мёртвую копию, которую никто
    не читает. Чтение не запрещено: хостовый weekly_digest читает из неё настройки модели, и
    глухой отказ сломал бы дайджест. Запись падает громко (SQLITE_READONLY). Метка —
    secrets_paths.moved_to_container; в контейнере (HEALTH_RUNTIME=container) стор живой."""
    if os.environ.get("HEALTH_RUNTIME") == "container":
        return False
    import secrets_paths
    if secrets_paths.moved_to_container(Path(DB_PATH).parent.parent):
        log.warning("%s: frozen copy (tenant runs in the container) — opened read-only", DB_PATH)
        return True
    return False


def get_conn(read_only: bool = False) -> sqlite3.Connection:
    """
    Возвращает SQLite-коннект.
    На primary (Studio) — read+write. На прочих узлах — read-only через SQLite URI.
    Для override write на non-primary: env ALLOW_WRITE_NONPRIMARY=1.

    read_only=True — аддитивно, дефолт False = прежнее поведение байт-в-байт.
    На primary отдаёт обычный RW-открытый коннект с `PRAGMA query_only=ON`: читает
    как всегда, но ЛЮБАЯ запись на этом коннекте отклоняется SQLite (SQLITE_READONLY).
    Файловая семантика открытия НЕ меняется (это НЕ `mode=ro`), поэтому живая WAL-БД
    на Studio открывается без -shm/-wal сюрпризов. На non-primary путь и так read-only.

    Использует _ClosingConn factory чтобы `with get_conn() as c:` корректно
    закрывал соединение (B-1 fix 2026-05-22).
    """
    if _is_primary():
        conn = sqlite3.connect(DB_PATH, factory=_ClosingConn)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        if read_only or _test_run_on_live_db() or _frozen_copy():
            conn.execute("PRAGMA query_only=ON")
        return conn
    # Non-primary: НЕ открывать молчаливо устаревшую iCloud-копию (split-brain,
    # reconciliation 2026-06-18). БД живёт на Studio (single source of truth).
    # Без явного HEALTH_DATA_DIR — громко падаем, не отдаём устаревшее.
    if not os.environ.get("HEALTH_DATA_DIR"):
        raise RuntimeError(
            f"эта машина ({socket.gethostname()}) не основная: основная по private/infra.yaml — "
            f"{_PRIMARY_HOST or 'не задана (запусти scripts/install.py)'}. "
            "health.db живёт на Studio. Прямое чтение БД на не-Studio хосте "
            "отключено (убрана молчаливая iCloud-копия, db_reconciliation_2026-06-18). "
            "Запусти на Studio через ssh, либо задай HEALTH_DATA_DIR на свежий снапшот."
        )
    if not DB_PATH.exists():
        # Раньше здесь падал sqlite «unable to open database file» — без причины (проба
        # чистого клона 2026-09-23): на не-основной машине БД только читается, создать её нельзя.
        raise RuntimeError(
            f"{DB_PATH} нет, а эта машина ({socket.gethostname()}) не основная — создать БД "
            f"здесь нельзя. Основная по private/infra.yaml: "
            f"{_PRIMARY_HOST or 'не задана (запусти scripts/install.py)'}.")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, factory=_ClosingConn)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_daily_metrics_v2():
    """Добавляет расширенные колонки в daily_metrics + таблицу workouts."""
    new_cols = [
        ("sleep_inbed",             "REAL"),
        ("sleep_awake",             "REAL"),
        ("sleep_core",              "REAL"),
        ("sleep_start",             "TEXT"),
        ("sleep_end",               "TEXT"),
        ("sleep_efficiency",        "INTEGER"),
        ("readiness_hrv_balance",   "INTEGER"),
        ("readiness_body_temp",     "INTEGER"),
        ("readiness_recovery_idx",  "INTEGER"),
        ("total_kcal",              "REAL"),
        ("distance_km",             "REAL"),
        ("activity_score",          "INTEGER"),
        ("breathing_disturbance",   "REAL"),
        ("stress_summary",          "TEXT"),
        ("stress_high_min",         "INTEGER"),
        ("recovery_high_min",       "INTEGER"),
        ("resilience_level",        "TEXT"),
        ("resilience_sleep_pct",    "REAL"),
        ("resilience_daytime_pct",  "REAL"),
        ("resilience_stress_pct",   "REAL"),
    ]
    with get_conn() as conn:
        for col, dtype in new_cols:
            try:
                conn.execute(f"ALTER TABLE daily_metrics ADD COLUMN {col} {dtype}")
            except Exception:
                pass  # уже существует
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workouts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                date          TEXT NOT NULL,
                activity_type TEXT,
                start_time    TEXT,
                end_time      TEXT,
                duration_min  REAL,
                distance_km   REAL,
                calories      REAL,
                avg_hr        REAL,
                max_hr        REAL,
                source        TEXT DEFAULT 'Oura',
                raw           TEXT,
                created_at    TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_workouts_date ON workouts(date)"
        )





def _migrate_blood_pressure():
    """Добавляет колонки bp_systolic и bp_diastolic в daily_metrics."""
    with get_conn() as conn:
        for col, dtype in [("bp_systolic", "REAL"), ("bp_diastolic", "REAL")]:
            try:
                conn.execute(f"ALTER TABLE daily_metrics ADD COLUMN {col} {dtype}")
            except Exception:
                pass  # уже существует


def _migrate_bp_readings():
    """Отдельные замеры давления со временем (нить withings-bp, 04.10). Дневные колонки
    daily_metrics.bp_* остаются у HAE (один писатель); эту таблицу пишет только Withings —
    и по ней сторож видит, что замер у прибора есть, а у HAE-пути пропал.
    measured_at — момент замера, unix-время UTC."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bp_readings (
                measured_at INTEGER PRIMARY KEY,
                systolic    REAL NOT NULL,
                diastolic   REAL NOT NULL,
                pulse       REAL,
                grpid       INTEGER,
                source      TEXT NOT NULL DEFAULT 'withings',
                imported_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)


def _migrate_consultation_sessions():
    """Таблица для персистентности in-progress /consult сессий (v2.3)."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS consultation_sessions (
                chat_id     INTEGER PRIMARY KEY,
                session_json TEXT NOT NULL,
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)


def _migrate_visual_intake():
    """symptom-intake (WP0): визуальный приём симптомов — ДОМЕН-АГНОСТИЧНО.
    visual_domains — §9-данные (домен/регионы/параметры диалога; клиники в коде НЕТ).
    visual_case — эпизод + снапшот elicitation-диалога (restart-safe, как consultation_sessions).
    visual_photo — снимки серии (sha256-дедуп, exif-strip флаг). Идемпотентно."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS visual_domains (
                domain         TEXT PRIMARY KEY,
                region_options TEXT,
                dialog_params  TEXT,
                created_at     TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS visual_case (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id              INTEGER,
                tenant               TEXT,
                domain               TEXT,
                region               TEXT,
                status               TEXT DEFAULT 'open',
                hypothesis_memory_id INTEGER,
                session_json         TEXT,
                opened_at            TEXT DEFAULT (datetime('now')),
                updated_at           TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS visual_photo (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id       INTEGER,
                path          TEXT,
                sha256        TEXT UNIQUE,
                exif_stripped INTEGER DEFAULT 0,
                taken_at      TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visual_photo_case ON visual_photo(case_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visual_case_open ON visual_case(chat_id, status)")
        # этап 2 (follow-up серии): колонка на уже-развёрнутой таблице — ALTER идемпотентно.
        for _c, _t in [("followup_reminded_at", "TEXT"), ("decision_requested_at", "TEXT")]:
            try:
                conn.execute(f"ALTER TABLE visual_case ADD COLUMN {_c} {_t}")
            except Exception:  # silent-ok: колонка уже существует (идемпотентная миграция)
                pass


def _migrate_pending_doc_reviews():
    """Таблица очереди подтверждения типа документа (v2.3)."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_doc_reviews (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file    TEXT NOT NULL,
                proposed_type  TEXT,
                imported_at    TEXT DEFAULT (datetime('now')),
                status         TEXT DEFAULT 'needs_review',
                confirmed_type TEXT
            )
        """)




def _migrate_imported_docs():
    """Таблица уже импортированных документов (W1-3, v3.4)."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS imported_docs (
                source_file TEXT PRIMARY KEY,
                imported_at TEXT DEFAULT (datetime('now')),
                doc_type    TEXT
            )
        """)


def _migrate_doc_patterns():
    """Таблица паттернов классификации документов (W2-1, v3.4).
    Персональные справочники (врачи, лаборатории) — в БД, не в коде (Rule #9).
    """
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS doc_patterns (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern         TEXT NOT NULL,
                doc_type        TEXT NOT NULL,
                match_on        TEXT NOT NULL DEFAULT 'filename',
                match_type      TEXT NOT NULL DEFAULT 'literal',
                specialist_name TEXT,
                notes           TEXT
            )
        """)


# Общий сид паттернов классификатора документов ПУСТ (решение владельца 29.09, вариант «б»).
# Здесь стояли лаборатории и тесты владельца (вендоры, генетика, отдельные анализы): по ним
# посторонний читал географию и вид лечения (чтение выгрузки 29.09). Теперь всё это — в
# per-tenant файле tenant_doc_patterns.yaml (вне git, изоляция по HEALTH_DATA_DIR), у каждого
# тенанта свой список; посторонняя установка начинает с пустого и добавляет свои. Раньше
# (diagnosis-hardcode A3, 2026-07-17) туда же уехали имена врачей и фамилия пациента.
_GENERIC_DOC_PATTERNS: list = []

TENANT_DOC_PATTERNS_PATH = _HEALTH_DIR / "data" / "tenant_doc_patterns.yaml"


def _load_tenant_doc_patterns() -> list:
    """Per-tenant PII-паттерны (имена врачей, фамилия пациента) из
    $HEALTH_DATA_DIR/data/tenant_doc_patterns.yaml. Отсутствует → [] (тенант без
    личных паттернов). Формат: список dict pattern/doc_type/match_on/match_type/
    specialist_name/notes. Файл вне git (PII); изоляция по HEALTH_DATA_DIR."""
    if not TENANT_DOC_PATTERNS_PATH.exists():
        return []
    import yaml as _yaml
    try:
        data = _yaml.safe_load(TENANT_DOC_PATTERNS_PATH.read_text(encoding="utf-8")) or []
    except Exception as e:
        log.warning(f"tenant_doc_patterns.yaml parse error: {e}")
        return []
    rows = []
    for d in data:
        rows.append((d.get("pattern"), d.get("doc_type"), d.get("match_on", "filename"),
                     d.get("match_type", "literal"), d.get("specialist_name"), d.get("notes")))
    return rows


def _seed_doc_patterns():
    """Засевает паттерны классификатора. INSERT OR IGNORE — идемпотентно, каждый старт.
    Generic (не-PII) из кода + per-tenant PII из tenant_doc_patterns.yaml (A3).
    """
    rows = list(_GENERIC_DOC_PATTERNS) + _load_tenant_doc_patterns()
    with get_conn() as conn:
        for row in rows:
            if not row[0]:  # пустой pattern из битого yaml — пропускаем
                continue
            conn.execute(
                """INSERT OR IGNORE INTO doc_patterns
                   (pattern, doc_type, match_on, match_type, specialist_name, notes)
                   SELECT ?, ?, ?, ?, ?, ?
                   WHERE NOT EXISTS (
                       SELECT 1 FROM doc_patterns WHERE pattern = ? AND doc_type = ?
                   )""",
                (*row, row[0], row[1]),
            )


def _cleanup_doc_patterns_duplicates():
    """Удаляет дублирующиеся строки в doc_patterns.

    Оставляет только min(id) для каждой уникальной комбинации
    (pattern, doc_type, match_on, match_type). Идемпотентно.
    Вызывается при каждом init_db() перед seed — чистит накопленные дубли.
    """
    with get_conn() as conn:
        conn.execute("""
            DELETE FROM doc_patterns
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM doc_patterns
                GROUP BY pattern, doc_type, match_on, match_type
            )
        """)


def _migrate_absolute_thresholds():
    """Sprint 2 / Р-1 (2026-05-22): таблица для абсолютных порогов
    срабатывания (полы и потолки) для evaluate_domain_need.

    Раньше ABSOLUTE_FLOORS/ABSOLUTE_CEILINGS были захардкожены в telegram_bot.py
    (F-101). Теперь — в БД, чтобы пороги, выведенные из threshold_analysis
    (персональные p10), и клинические (ESC/AHA) хранились с источником
    и могли быть обновлены без code change.
    """
    with get_conn() as conn:
        # Финальная схема (новые установки сразу корректны).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS absolute_thresholds (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                metric          TEXT NOT NULL,
                direction       TEXT NOT NULL,
                value           REAL NOT NULL,
                reason_template TEXT NOT NULL,
                source          TEXT NOT NULL,
                source_date     TEXT,
                active          INTEGER NOT NULL DEFAULT 1,
                updated_at      TEXT DEFAULT (datetime('now')),
                kind            TEXT NOT NULL DEFAULT 'absolute',
                baseline        TEXT,
                band_label      TEXT DEFAULT '',
                variant         TEXT DEFAULT '',
                UNIQUE(metric, direction, band_label, variant)
            )
        """)
        # ── ALTER для старых таблиц (прежняя схема без новых колонок) ──
        # §9 поток F: kind=absolute|relative; baseline — базис relative (avg7_hrv, value=множитель);
        # band_label — полоса ТЯЖЕСТИ (very_low/low/...); variant — НАЗНАЧЕНИЕ (food/deep/lifestyle).
        _cols = {r[1] for r in conn.execute("PRAGMA table_info(absolute_thresholds)")}
        if "kind" not in _cols:
            conn.execute("ALTER TABLE absolute_thresholds ADD COLUMN kind TEXT NOT NULL DEFAULT 'absolute'")
        if "baseline" not in _cols:
            conn.execute("ALTER TABLE absolute_thresholds ADD COLUMN baseline TEXT")
        if "band_label" not in _cols:
            conn.execute("ALTER TABLE absolute_thresholds ADD COLUMN band_label TEXT DEFAULT ''")
        if "variant" not in _cols:
            conn.execute("ALTER TABLE absolute_thresholds ADD COLUMN variant TEXT DEFAULT ''")
        # ── Перестройка идентичности: UNIQUE(metric,direction,source) → (metric,direction,band_label,variant) ──
        # Прежний UNIQUE допускал лишь 1 порог на metric+direction. Полосы и назначения
        # (3 множителя ВСР, полосы deep<30/<50) требуют несколько. source → просто провенанс.
        # SQLite не ALTER-ит constraint → rebuild. Идемпотентно по наличию старого UNIQUE.
        _sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='absolute_thresholds'"
        ).fetchone()[0]
        if "UNIQUE(metric, direction, source)" in _sql:
            conn.execute("""
                CREATE TABLE absolute_thresholds_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    metric TEXT NOT NULL, direction TEXT NOT NULL, value REAL NOT NULL,
                    reason_template TEXT NOT NULL, source TEXT NOT NULL, source_date TEXT,
                    active INTEGER NOT NULL DEFAULT 1, updated_at TEXT DEFAULT (datetime('now')),
                    kind TEXT NOT NULL DEFAULT 'absolute', baseline TEXT,
                    band_label TEXT DEFAULT '', variant TEXT DEFAULT '',
                    UNIQUE(metric, direction, band_label, variant)
                )
            """)
            conn.execute("""
                INSERT INTO absolute_thresholds_new
                    (id, metric, direction, value, reason_template, source, source_date,
                     active, updated_at, kind, baseline, band_label, variant)
                SELECT id, metric, direction, value, reason_template, source, source_date,
                       active, updated_at, COALESCE(kind,'absolute'), baseline,
                       COALESCE(band_label,''), COALESCE(variant,'')
                FROM absolute_thresholds
            """)
            conn.execute("DROP TABLE absolute_thresholds")
            conn.execute("ALTER TABLE absolute_thresholds_new RENAME TO absolute_thresholds")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_abs_thresh_metric ON absolute_thresholds(metric, active)"
        )
        _canonize_threshold_metrics(conn, "absolute_thresholds")
        # ── Провенанс нормы (нить norm-provenance, 2026-09-02): ВИД · СРОК · СТРАТА ──
        # norm_kind — какой из четырёх объектов медицины сплющен в value: референсный
        # интервал (статистика здоровых) · порог решения (из исходов, версионируемый
        # документ) · стратифицированная цель (по типу пациента; stratum — какой факт
        # профиля её выбрал) · личная норма (перцентиль/RCV тенанта). 'unclassified' —
        # честный остаток: вердикт не вынесен, ночной датчик его считает, гасит человек.
        # next_review — срок годности: медицина обновляет норму не вычислением, а
        # именованным документом с датой пересмотра; просрочка = красное.
        _cols = {r[1] for r in conn.execute("PRAGMA table_info(absolute_thresholds)")}
        if "norm_kind" not in _cols:
            conn.execute(
                "ALTER TABLE absolute_thresholds ADD COLUMN norm_kind TEXT NOT NULL "
                "DEFAULT 'unclassified' CHECK (norm_kind IN ('reference_interval',"
                "'decision_threshold','stratified_target','personal','unclassified'))")
        if "next_review" not in _cols:
            conn.execute("ALTER TABLE absolute_thresholds ADD COLUMN next_review TEXT")
        if "stratum" not in _cols:
            conn.execute("ALTER TABLE absolute_thresholds ADD COLUMN stratum TEXT")


def _canonize_threshold_metrics(conn, table: str) -> int:
    """Имя лабораторного порога обязано быть канон-именем lab_canon (нить norm-provenance,
    2026-09-02): под вариантом написания порог не встречает данных и молчит неотличимо
    от «в норме» (CA19.9↔CA19-9, Cholesterol↔Cholesterol_Total, 39 дней). Переименовывает
    строки таблицы порогов в канон; при занятом канон-ключе старую строку НЕ трогает и
    логирует — решение о дубле принимает человек (§13), а не UPDATE. Возвращает число
    переименованных. Daily-метрики (hrv, steps…) normalize возвращает как есть."""
    import lab_canon
    n = 0
    for (m,) in conn.execute(f"SELECT DISTINCT metric FROM {table}").fetchall():
        canon = lab_canon.normalize(m) if m else m
        if not canon or canon == m:
            continue
        clash = conn.execute(f"SELECT 1 FROM {table} WHERE metric=? LIMIT 1", (canon,)).fetchone()
        if clash:
            log.error(
                f"{table}: порог '{m}' и его канон '{canon}' оба существуют — дубль, "
                f"решает человек; строка '{m}' оставлена")
            continue
        n += conn.execute(f"UPDATE {table} SET metric=? WHERE metric=?", (canon, m)).rowcount
    return n


# Машинно-однозначные виды нормы по провенансу строки (нить norm-provenance). Ключ —
# подстрока source. Всё, чего здесь нет, остаётся 'unclassified' — это не умолчание,
# а находка для человека: вид нормы из полей не выводится (§9, четвёртый дом).
_NORM_KIND_BY_SOURCE = (
    ("p10_personal", "personal"),
    ("p90_personal", "personal"),
    ("owner_personal", "personal"),
    ("threshold_analysis_", "personal"),      # личные p10 из threshold_analysis (bootstrap)
    ("ESC_AHA_", "decision_threshold"),         # гайдлайн: порог решения, версионируемый
    ("CTCAE_", "decision_threshold"),           # NCI CTCAE — грейды лабораторной токсичности
    ("EFLM_BV", "personal"),                    # RCV из биологической вариации
)


def _classify_norm_kinds() -> int:
    """Ставит norm_kind только там, где он выводится из source ОДНОЗНАЧНО, и только
    у 'unclassified' (идемпотентно; ручной вердикт человека не перетирается).
    next_review НЕ ставит: единый срок всем строкам дал бы 60+ красных в один день
    (§13, banner blindness) — срок назначает человек вместе с видом."""
    n = 0
    with get_conn() as conn:
        for pat, kind in _NORM_KIND_BY_SOURCE:
            n += conn.execute(
                "UPDATE absolute_thresholds SET norm_kind=? "
                "WHERE norm_kind='unclassified' AND source LIKE ?", (kind, f"%{pat}%")).rowcount
    return n


def _migrate_norm_documents():
    """Реестр документов нормы (нить norm-from-documents 2026-09-02): версия, дата, URL, checksum
    локального файла, каденция проверки версии. Заполняется из norm_documents.DOCUMENTS
    (данные в коде — это НЕ норма, это адреса документов; §9 о числах). next_check выставляется
    при регистрации; датчик check_norm_documents_fresh краснеет по нему и по смене checksum."""
    import norm_documents
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS norm_documents (
                id              TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                version         TEXT,
                issued          TEXT,
                url             TEXT,
                local           TEXT,
                kind            TEXT,
                applies_to      TEXT,
                sha256          TEXT,
                next_check_days INTEGER,
                registered_at   TEXT DEFAULT (datetime('now')),
                last_check      TEXT,
                next_check      TEXT,
                remote_state    TEXT
            )""")
        for d in norm_documents.documents():
            conn.execute(
                "INSERT OR IGNORE INTO norm_documents (id,name,version,issued,url,local,kind,applies_to,"
                "sha256,next_check_days,next_check,remote_state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (d["id"], d["name"], d["version"], d["issued"], d["url"], d["local"], d["kind"],
                 d["applies_to"], d["sha256"], d["next_check_days"],
                 str(get_today() + timedelta(days=int(d["next_check_days"]))), d.get("local_state")))
            conn.execute("UPDATE norm_documents SET name=?, version=?, issued=?, url=?, local=?, "
                         "kind=?, applies_to=?, next_check_days=?, "
                         "remote_state=CASE WHEN sha256 IS NOT ? THEN ? ELSE COALESCE(remote_state, ?) END, sha256=? WHERE id=?",
                         (d["name"], d["version"], d["issued"], d["url"], d["local"], d["kind"],
                          d["applies_to"], d["next_check_days"], d["sha256"], d.get("local_state"),
                          d.get("local_state"), d["sha256"], d["id"]))


def _refresh_lab_refs():
    """system_config.lab_refs ← мода референсов бланков (labs_db.compute_bank_refs). Кэш, не сид:
    перезаписывается на каждом init_db; источник локальный (lab_results), границы устаревания
    не нужно. min_docs — norm.witness_min_docs (один бланк — не свидетель, §17)."""
    import json as _json
    import labs_db
    try:
        import config_db
        min_docs = int(config_db.get_config("norm.witness_min_docs", 3))
    except Exception:  # noqa: BLE001
        min_docs = 3
    refs = labs_db.compute_bank_refs(min_docs)
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO system_config (key, value_json, category, source, updated_at) "
                     "VALUES ('lab_refs', ?, 'lab', 'bank_modal', datetime('now'))",
                     (_json.dumps(refs, ensure_ascii=False),))
        conn.execute("INSERT OR REPLACE INTO system_config (key, value_json, category, source, updated_at) "
                     "VALUES ('lab_refs_meta', ?, 'lab', 'bank_modal', datetime('now'))",
                     (_json.dumps({"computed": str(get_today()), "min_docs": min_docs, "n": len(refs)}),))
    return refs


def _seed_absolute_thresholds():
    """Seed канонических ABSOLUTE_FLOORS/CEILINGS из telegram_bot.evaluate_domain_need
    (F-101 fix). INSERT OR IGNORE по UNIQUE(metric, direction, source).
    """
    # (metric, direction, value, reason_template, source, source_date, kind, baseline, band_label, variant)
    # NEUTRAL (brief-neutralization Фаза 0): reason_template личных полов очищен от чужого
    # бэйзлайна. Раньше текст нёс личный перцентиль ВЛАДЕЛЬЦА, засеваемый в БД КАЖДОГО тенанта
    # → утекал в reason брифа партнёра. band_label ТРОГАТЬ НЕЛЬЗЯ — это часть UNIQUE-ключа и
    # ключа поиска get_threshold(band=''); провенанс несёт поле source (дата анализа).
    # Значение: личное выводит _personalize_absolute_floors (Фаза 3a, p10 ряда тенанта); здесь —
    # только стартовое для тенанта без своего ряда (27.09 снят остаток — сид больше не несёт
    # чисел ряда владельца).
    seeds = [
        # ── ABSOLUTE FLOORS/CEILINGS (evaluate_domain_need; threshold_analysis p10 / ESC-AHA) ──
        # Стартовые значения для тенанта без своего ряда (<14 дней; _personalize перезапишет).
        # Ни одно не снято с чужого ряда (BL-PUB-16 а, 27.09): у ВСР и глубокого сна общей нормы
        # нет — пол 0 = «не срабатывает, пока нет своих данных» (относительные пороги ВСР от
        # недельного среднего работают сразу); готовность и sleep_score — шкала прибора (<70 =
        # «обратить внимание»), sleep_total — консервативный минимум взрослого сна.
        ('hrv',        'floor',   0.0,   i18n.t('health_db.reason.hrv_low', "ru"),
         'bootstrap_until_own_data', '2026-09-27', 'absolute', None, '', ''),
        ('readiness',  'floor',   70.0,  i18n.t('health_db.reason.readiness_low', "ru"),
         'bootstrap_pop_norm', '2026-09-27', 'absolute', None, '', ''),
        ('sleep_deep', 'floor',   0.0,   i18n.t('health_db.reason.deep_sleep_low', "ru"),
         'bootstrap_until_own_data', '2026-09-27', 'absolute', None, '', ''),
        # sleep_score daily-пол (вынос литерала lifestyle_agents `score<70`, правило владельца «всё
        # высчитывается»). Bootstrap=70 (консерв. поп-норма для data-бедного тенанта);
        # _personalize_absolute_floors перезапишет на личный p10 при ≥14 днях. Единый primary,
        # не дубль — trend_thresholds sleep_score = ОКОННАЯ норма (др. потребитель).
        ('sleep_score', 'floor',  70.0,  i18n.t('health_db.reason.sleep_score_low', "ru"),
         'bootstrap_pop_norm', '2026-09-27', 'absolute', None, '', ''),
        # sleep_total daily-пол в ЧАСАХ (вынос литерала lifestyle_agents `total<6`). Bootstrap=6ч
        # (консерв.); _personalize перезапишет на личный p10. Единица = часы (как daily_metrics.sleep_total).
        ('sleep_total', 'floor',  6.0,   i18n.t('health_db.reason.short_sleep', "ru"),
         'bootstrap_pop_norm', '2026-09-27', 'absolute', None, '', ''),
        # Давление: порог смотрит на ПИК дня (решение владельца 26.09, вариант Б) — ключ raw
        # bp_*_max пишет парсер тонометра; среднее в колонке bp_* остаётся выводом о давлении.
        ('bp_systolic_max',  'ceiling', 140.0, i18n.t('health_db.reason.systolic_high', "ru"),
         'ESC_AHA_2023',   '2023-01-01', 'absolute', None, '', ''),
        ('bp_diastolic_max', 'ceiling',  90.0, i18n.t('health_db.reason.diastolic_high', "ru"),
         'ESC_AHA_2023',   '2023-01-01', 'absolute', None, '', ''),
        # ── ПОТОЛКИ (brief-neutralization, числовая ось; вынос литерала lifestyle_agents
        # `awake_m>60`). Bootstrap = поп-норма для data-бедного тенанта;
        # _personalize_absolute_ceilings перезапишет sleep_awake на личный p90 при ≥30 днях.
        # min_n=30 (НЕ 14 как у полов): верхний «плохой» хвост = риск ПРОПУСКА (высокий шумный
        # p90 → флаг молчит) → требуем больше точек (прецедент B2 p5-union).
        # sleep_awake хранится в ЧАСАХ (как sleep_deep/daily_metrics.sleep_awake) → код ×60.
        ('sleep_awake',    'ceiling', 1.0,  i18n.t('health_db.reason.awake_high', "ru"),
         'bootstrap_pop_norm', '2026-07-17', 'absolute', None, '', ''),
        # ── RELATIVE (value = множитель baseline; вынос литералов morning_report/lifestyle_agents, поток F §9) ──
        ('hrv',   'floor', 0.88, i18n.t('health_db.reason.hrv_food', "ru"),
         'code_migration_F_2026-06-28', '2026-06-28', 'relative', 'avg7_hrv',   '', 'food'),
        ('hrv',   'floor', 0.82, i18n.t('health_db.reason.hrv_deep', "ru"),
         'code_migration_F_2026-06-28', '2026-06-28', 'relative', 'avg7_hrv',   '', 'deep'),
        ('hrv',   'floor', 0.85, i18n.t('health_db.reason.hrv_lifestyle', "ru"),
         'code_migration_F_2026-06-28', '2026-06-28', 'relative', 'avg30_hrv',  '', 'lifestyle'),
        ('steps', 'floor', 0.5,  i18n.t('health_db.reason.steps_relative', "ru"),
         'code_migration_F_2026-06-28', '2026-06-28', 'relative', 'avg7_steps', '', ''),
        # ── ABSOLUTE singles (вынос литералов lifestyle_agents) ──
        ('steps', 'floor', 5000.0, i18n.t('health_db.reason.steps_target', "ru"),
         'code_migration_F_2026-06-28', '2026-06-28', 'absolute', None, '', 'target'),
        ('spo2',  'floor', 94.0,   i18n.t('health_db.reason.spo2_low', "ru"),
         'code_migration_F_2026-06-28', '2026-06-28', 'absolute', None, '', ''),
    ]
    with get_conn() as conn:
        # 26.09: порог давления переехал со среднего (bp_*) на пик дня (bp_*_max). INSERT OR
        # IGNORE живую строку не переименует → без этого остались бы ДВА активных порога.
        for _old in ("bp_systolic", "bp_diastolic"):
            conn.execute(
                "UPDATE OR IGNORE absolute_thresholds SET metric=?, reason_template=? "
                "WHERE metric=? AND direction='ceiling' AND source='ESC_AHA_2023'",
                (f"{_old}_max",
                 next(r[3] for r in seeds if r[0] == f"{_old}_max"), _old))
        for row in seeds:
            conn.execute(
                """INSERT OR IGNORE INTO absolute_thresholds
                   (metric, direction, value, reason_template, source, source_date,
                    kind, baseline, band_label, variant)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                row,
            )





def _migrate_checkin_scores():
    """Sprint 3 / Р-2 (2026-05-22): добавляет stress_score/mood_score/energy_score
    колонки в checkins. Раньше эти поля извлекались checkin_agent.finalize_checkin
    как JSON в `context`, но lifestyle_agents.StressAgent читал их как отдельные
    SQL-колонки (F-117) — silent fail.

    После этой миграции LLM-extract сохраняется И в context JSON И в плоские
    колонки. lifestyle_agents может делать SQL-агрегации (например, средний
    stress за неделю).

    Scoring:
      stress_score:  0 (none) .. 10 (extreme) — из day_score либо текста
      mood_score:    1 (negative) | 2 (neutral/mixed) | 3 (positive)
      energy_score:  1 (low) | 2 (medium) | 3 (high)
    """
    with get_conn() as conn:
        for col, dtype in [
            ("stress_score", "INTEGER"),
            ("mood_score",   "INTEGER"),
            ("energy_score", "INTEGER"),
        ]:
            try:
                conn.execute(f"ALTER TABLE checkins ADD COLUMN {col} {dtype}")
            except Exception:
                pass  # silent-ok: column already exists





def _migrate_routing_keywords():
    """Sprint 2 / Р-1 step 4 (2026-05-22): таблица для domain-routing
    ключевых слов hai_context._detect_domains. Раньше — hardcoded
    _DOMAIN_KEYWORDS dict (F-122). Теперь — в БД с возможностью
    add/remove без code change.

    Special domain '__full__' хранит _FULL_CONTEXT_KEYWORDS (триггер на полный
    контекст: 'что мне делать', 'в целом' и т.п.).
    """
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS routing_keywords (
                domain      TEXT NOT NULL,
                keyword     TEXT NOT NULL,
                added_at    TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (domain, keyword)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_routing_kw_domain ON routing_keywords(domain)"
        )


def _seed_routing_keywords():
    """Seed канонических _DOMAIN_KEYWORDS/_FULL_CONTEXT_KEYWORDS из
    hai_context. INSERT OR IGNORE — manual add/remove не перезаписываются.
    """
    try:
        from hai_context import _DOMAIN_KEYWORDS, _FULL_CONTEXT_KEYWORDS
    except ImportError:
        log.debug("_seed_routing_keywords: hai_context недоступен, skip seed")
        return

    with get_conn() as conn:
        for domain, kws in _DOMAIN_KEYWORDS.items():
            for kw in kws:
                conn.execute(
                    "INSERT OR IGNORE INTO routing_keywords (domain, keyword) VALUES (?, ?)",
                    (domain, kw),
                )
        for kw in _FULL_CONTEXT_KEYWORDS:
            conn.execute(
                "INSERT OR IGNORE INTO routing_keywords (domain, keyword) VALUES (?, ?)",
                ("__full__", kw),
            )





def _seed_data_freshness():
    """Sprint 2 / Р-1 (2026-05-22): seed канонических lab freshness порогов
    из labs_db.DATA_FRESHNESS в lab_monitoring_schedule.

    Использует INSERT OR IGNORE, поэтому:
      - первый init_db (пустая БД) → seed-ит все записи с source='code_seed'.
      - повторный init_db → ничего не делает (записи уже есть).
      - manual/encounter правки через upsert_monitoring_rule никогда
        не перезаписываются (записи существуют — IGNORE).

    Lazy import labs_db чтобы избежать циклической зависимости на module-level.
    """
    # diagnosis-hardcode B6: code_seed содержит только НЕЙТРАЛЬНУЮ базу (BASE_FRESHNESS).
    # У выдуманного тенанта с пустым набором применимых состояний условные правила
    # не должны появляться. Они приходят per-tenant через gp_context →
    # labs_db.effective_freshness(active_conditions тенанта).
    try:
        from labs_db import BASE_FRESHNESS as DATA_FRESHNESS
    except ImportError:
        log.debug("_seed_data_freshness: labs_db недоступен, skip seed")
        return

    with get_conn() as conn:
        for test_name, cfg in DATA_FRESHNESS.items():
            conn.execute(
                """INSERT OR IGNORE INTO lab_monitoring_schedule
                   (test_name, interval_days, priority, source, note, updated_at)
                   VALUES (?, ?, ?, 'code_seed', ?, datetime('now'))""",
                (test_name, cfg["days"], cfg["priority"], cfg.get("missing_note")),
            )


def _migrate_hypotheses_cbcr():
    """Таблица для CBCR-payload гипотез (W5A-INT-3, Q1=B).

    FK на memory.id с UNIQUE — одна богатая структура на гипотезу.
    Поля для in-SQL queryability: structural_score, confidence_level.
    payload — full JSON (~17KB).
    """
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hypotheses_cbcr (
                memory_id        INTEGER PRIMARY KEY,
                payload          TEXT NOT NULL,
                generated_at     TEXT DEFAULT (datetime('now')),
                structural_score INTEGER,
                confidence_level TEXT,
                generated_by     TEXT,
                model            TEXT,
                FOREIGN KEY (memory_id) REFERENCES memory(id) ON DELETE CASCADE
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hypotheses_cbcr_confidence "
            "ON hypotheses_cbcr(confidence_level)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hypotheses_cbcr_score "
            "ON hypotheses_cbcr(structural_score)"
        )



# ── Medical Record schema (медкарта, спека май 2026) ──────────────────────────

def _migrate_medical_record():
    """Аддитивная миграция: события, эпизоды, snapshot-таблицы медкарты.

    Не трогает существующие таблицы. Все операции идемпотентны.
    Ref: docs/medical_record_spec.md
    """
    with get_conn() as conn:
        conn.executescript("""

        -- ── Эпизоды помощи (Episodes of Care) ───────────────────────────
        -- Логическая группа событий вокруг одной клинической проблемы.
        CREATE TABLE IF NOT EXISTS episodes_of_care (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            primary_problem_id    TEXT,               -- problem_list.problem_id (slug)
            title                 TEXT NOT NULL,
            start_date            TEXT NOT NULL,       -- YYYY-MM-DD
            end_date              TEXT,                -- NULL = ongoing
            status                TEXT NOT NULL DEFAULT 'active',  -- active|finished|cancelled
            managing_organization TEXT,
            notes                 TEXT,
            created_at            TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_episodes_status   ON episodes_of_care(status);
        CREATE INDEX IF NOT EXISTS idx_episodes_problem  ON episodes_of_care(primary_problem_id);

        -- ── Клинические события (Timeline) ───────────────────────────────
        -- Атомарная запись любого клинически значимого события.
        CREATE TABLE IF NOT EXISTS events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type      TEXT NOT NULL,             -- encounter|lab|imaging|procedure|medication_change|immunization|self_observation|diagnostic_report
            effective_date  TEXT NOT NULL,             -- YYYY-MM-DD
            effective_time  TEXT,                      -- HH:MM если известно
            recorded_at     TEXT DEFAULT (datetime('now')),
            status          TEXT NOT NULL DEFAULT 'completed',  -- planned|in-progress|completed|cancelled|entered-in-error
            performer       TEXT,                      -- напр. Dr. X | Lab | self
            performer_role  TEXT,                      -- gp|specialist|lab|self|device
            location        TEXT,                      -- название клиники
            episode_id      INTEGER REFERENCES episodes_of_care(id),
            recorded_by     TEXT NOT NULL DEFAULT 'patient',  -- patient|provider
            attachments     TEXT NOT NULL DEFAULT '[]',       -- JSON array of paths
            notes           TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_events_date    ON events(effective_date);
        CREATE INDEX IF NOT EXISTS idx_events_type    ON events(event_type);
        CREATE INDEX IF NOT EXISTS idx_events_status  ON events(status);
        CREATE INDEX IF NOT EXISTS idx_events_episode ON events(episode_id);

        -- ── Encounter details (SOAP) ──────────────────────────────────────
        CREATE TABLE IF NOT EXISTS encounters (
            event_id          INTEGER PRIMARY KEY REFERENCES events(id),
            class             TEXT,                    -- ambulatory|inpatient|virtual|emergency
            specialty         TEXT,                    -- oncology|gp|surgery|endoscopy
            reason_text       TEXT,
            chief_complaint   TEXT,
            duration_minutes  INTEGER,
            subjective        TEXT,                    -- S
            objective         TEXT,                    -- O
            assessment        TEXT,                    -- A
            plan              TEXT                     -- P
        );

        -- ── Diagnostic events (lab / imaging / procedure) ─────────────────
        CREATE TABLE IF NOT EXISTS diagnostic_events (
            event_id              INTEGER PRIMARY KEY REFERENCES events(id),
            type                  TEXT,                -- lab|imaging|procedure|biopsy|functional_test
            modality              TEXT,                -- PET-CT|MRI|ultrasound|PSG|colonoscopy
            ordering_event_id     INTEGER REFERENCES events(id),
            raw_values_ref        TEXT DEFAULT '[]',   -- JSON array of lab_results.id
            interpreted_report    TEXT,
            abnormal_flags        TEXT NOT NULL DEFAULT '[]'  -- JSON array
        );
        CREATE INDEX IF NOT EXISTS idx_diag_ordering ON diagnostic_events(ordering_event_id);

        -- ── Care plans ────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS care_plans (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            episode_id  INTEGER REFERENCES episodes_of_care(id),
            goals       TEXT NOT NULL DEFAULT '[]',     -- JSON array of strings
            activities  TEXT NOT NULL DEFAULT '[]',     -- JSON array of {action, deadline, status}
            status      TEXT NOT NULL DEFAULT 'active', -- draft|active|completed|revoked
            created_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now'))
        );

        -- ── Event ↔ Problem links ─────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS event_problem_links (
            event_id    INTEGER REFERENCES events(id),
            problem_id  TEXT,                           -- problem_list.problem_id (slug)
            link_type   TEXT NOT NULL DEFAULT 'reason', -- reason|result|unrelated
            PRIMARY KEY (event_id, problem_id)
        );

        -- ── Event relationships (non-ordering) ───────────────────────────
        CREATE TABLE IF NOT EXISTS event_relationships (
            parent_event_id   INTEGER REFERENCES events(id),
            child_event_id    INTEGER REFERENCES events(id),
            relationship_type TEXT NOT NULL,            -- followup_to|partOf|supersedes|triggered
            PRIMARY KEY (parent_event_id, child_event_id)
        );


        -- ── Snapshot: медикаменты ──────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS medications (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            name                  TEXT NOT NULL,
            dosage                TEXT,
            frequency             TEXT,
            route                 TEXT,                 -- oral|iv|topical|inhaled|subcutaneous
            indication_problem_id TEXT,                 -- problem_list.problem_id
            prescribing_event_id  INTEGER REFERENCES events(id),
            start_date            TEXT,
            end_date              TEXT,
            status                TEXT NOT NULL DEFAULT 'active',  -- active|completed|discontinued
            notes                 TEXT,
            created_at            TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_medications_status ON medications(status);




        -- ── Alerts (критическая информация) ───────────────────────────────
        CREATE TABLE IF NOT EXISTS alerts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            type       TEXT NOT NULL,                  -- allergy|medication_interaction|DNR|other
            severity   TEXT,                           -- low|medium|high|critical
            message    TEXT NOT NULL,
            active     INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );

        """)

    # ── ALTER TABLE на существующих (идемпотентно) ────────────────────────
    _alters = [
        # problem_list: lifecycle fields
        "ALTER TABLE problem_list ADD COLUMN onset_date TEXT",
        "ALTER TABLE problem_list ADD COLUMN resolved_date TEXT",
        "ALTER TABLE problem_list ADD COLUMN current_episode_id INTEGER",
        # 27.09: описание проблемы простыми словами для человека (медицинская формулировка
        # остаётся врачам; у этого поля один читатель — дашборд /problems)
        "ALTER TABLE problem_list ADD COLUMN plain_summary TEXT",
        # lab_results: link to events
        "ALTER TABLE lab_results ADD COLUMN event_id INTEGER",
        # lab_results: тип биоматериала (blood|urine|stool|saliva|other) —
        # разделение анализов по материалам (2026-07-01). Default 'blood' →
        # существующие строки (канон был кровь-only) бэкофиллятся сами.
        "ALTER TABLE lab_results ADD COLUMN specimen TEXT DEFAULT 'blood'",
        # pending_field_reviews: Telegram message_id для reply-to ввода
        "ALTER TABLE pending_field_reviews ADD COLUMN tg_message_id INTEGER",
        # daily_metrics: Module 2 — расширенные Apple Health метрики (2026-06-01)
        "ALTER TABLE daily_metrics ADD COLUMN exercise_min REAL",
        "ALTER TABLE daily_metrics ADD COLUMN cycling_km REAL",
        "ALTER TABLE daily_metrics ADD COLUMN walking_speed_avg REAL",
        "ALTER TABLE daily_metrics ADD COLUMN walking_step_length_avg REAL",
        "ALTER TABLE daily_metrics ADD COLUMN walking_asymmetry_avg REAL",
        "ALTER TABLE daily_metrics ADD COLUMN stand_min REAL",
        # 26.09 (решение владельца, вопрос 4 — вариант Б): выносливость и подвижность колонками
        # и в семью сигналов. Приходили с 30.06 и выбрасывались.
        "ALTER TABLE daily_metrics ADD COLUMN cardio_recovery_bpm REAL",
        "ALTER TABLE daily_metrics ADD COLUMN six_min_walk_m REAL",
        "ALTER TABLE daily_metrics ADD COLUMN stair_speed_up_ms REAL",
        "ALTER TABLE daily_metrics ADD COLUMN stair_speed_down_ms REAL",
        "ALTER TABLE daily_metrics ADD COLUMN met_avg REAL",
        # agent_reports: link to episodes
        "ALTER TABLE agent_reports ADD COLUMN episode_id INTEGER",
        "ALTER TABLE agent_reports ADD COLUMN related_event_ids TEXT DEFAULT '[]'",
        # memory_facts: temporal_class (durable|standing|transient) — ось временнóй
        # валидности (Ф0, 2026-07-07). NULL=не классифицировано; critical_flag=1 → durable.
        # durable=никогда не тускнеет; standing=инвалидация при смене; transient=TTL по возрасту.
        "ALTER TABLE memory_facts ADD COLUMN temporal_class TEXT",
        # problem_list_proposals (2026-09-01): строка = ОДНА правка; повтор той же правки —
        # счётчик, не дубль; идентичность add — условие clinical_kb (см. problems_db.proposal_dedup_key).
        "ALTER TABLE problem_list_proposals ADD COLUMN dedup_key TEXT",
        "ALTER TABLE problem_list_proposals ADD COLUMN repeats INTEGER DEFAULT 1",
        "ALTER TABLE problem_list_proposals ADD COLUMN last_proposed_at TEXT",
        # Квитанция доставки предложения человеку. Создать предложение недостаточно:
        # без отдельной доставки оно может оставаться невидимым до ручного /report|/weekly,
        # независимо от содержимого списка проблем.
        "ALTER TABLE problem_list_proposals ADD COLUMN delivered_at TEXT",
        "ALTER TABLE problem_list_proposals ADD COLUMN tg_message_id INTEGER",
    ]
    with get_conn() as conn:
        for stmt in _alters:
            try:
                conn.execute(stmt)
            except Exception:
                pass  # silent-ok: column already exists


# _migrate_consultations_to_events снят (BL-CONSULT-SECOND-HOME-1): писатель
# consultations_db.save_consultation сохраняет приём в events сам.
# Повторное копирование из consultations при каждом init_db могло бы воскрешать
# удалённые из медкарты дубли.


def _drop_unused_history_tables():
    """Удаляет только пустые устаревшие таблицы истории:
    allergies/immunizations/family_history/social_history. Наличие пустой таблицы
    не означает, что соответствующий раздел заполнен или проверен.
    Например, в выдуманном профиле пустая таблица удаляется, а непустая сохраняется
    с громким предупреждением. Записи не теряются, init_db не прерывается."""
    with get_conn() as conn:
        for t in ("allergies", "immunizations", "family_history", "social_history"):
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone():
                continue
            if conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]:
                logging.getLogger(__name__).warning("%s: есть строки — таблица оставлена", t)
                continue
            conn.execute(f"DROP TABLE {t}")


def _migrate_effect_allele_meta():
    """2026-06-17 (genome strand-fix Ф0): добавляет метаданные резолюции
    effect_allele в genetic_variants. Аддитивно, nullable, идемпотентно.

    Колонки:
      ref_allele            — референсный аллель (hg19, + strand), пара к alt
      assembly              — сборка, на которой взяты ref/alt (ожидаем GRCh37/hg19)
      effect_allele_status  — результат резолюции strand:
          resolved | palindromic | multiallelic_ambiguous |
          source_conflict | genotype_mismatch | no_call | no_data

    Зачем: effect_allele раньше никогда не заполнялся (нет производителя),
    а наивное заполнение strand-неустойчиво. Эти поля делают резолюцию
    прозрачной и позволяют integrity_tests мониторить покрытие 'resolved'.
    NULL effect_allele + status объясняет ПОЧЕМУ не верифицировано.
    """
    with get_conn() as conn:
        for col, dtype in [
            ("ref_allele",           "TEXT"),
            ("assembly",             "TEXT"),
            ("effect_allele_status", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE genetic_variants ADD COLUMN {col} {dtype}")
            except Exception:
                pass  # silent-ok: column already exists


def _migrate_constitutions():
    """2026-06-18: constitutions DB-as-source (Option 3).

    Нарратив конституций здоровья хранится в БД (единственный источник),
    а не в файлах constitutions/*.md (они порождали git split-brain:
    генерация на Studio → дубль-писатель + вечно-грязное дерево).
    Дашборд читает из БД; файлы становятся производными (untracked).
    """
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS constitutions (
                domain         TEXT PRIMARY KEY,
                title          TEXT,
                body_md        TEXT NOT NULL,
                generated_at   TEXT DEFAULT (datetime('now')),
                source_version TEXT
            )
        """)











def _migrate_workouts_dedup():
    """Дедуп тренировок + UNIQUE(date,start_time).

    Без unique-констрейнта ON CONFLICT DO NOTHING не предотвращает повторный импорт.
    Миграция оставляет MIN(id) на группу (date,start_time), затем ставит unique-индекс,
    после которого ON CONFLICT дедуплицирует записи. Идемпотентность — по наличию индекса.
    start_time используется как точная строка ISO."""
    with get_conn() as conn:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_workouts_unique'"
        ).fetchone():
            return
        conn.execute(
            "DELETE FROM workouts WHERE id NOT IN "
            "(SELECT MIN(id) FROM workouts GROUP BY date, start_time)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX idx_workouts_unique ON workouts(date, start_time)"
        )


def _migrate_lab_staging():
    """Staging-таблица перераспознавания анализов (2026-06-29, lab-recognizer).

    Канон lab_results НЕ трогаем: новый ансамблевый прогон пишет сюда,
    diff старое↔новое идёт под человек-гейтом (pending_doc_reviews), и
    только confirmed/auto строки промоутятся в lab_results. Закрывает дыру
    аудита: provenance-ссылка строка→документ (source_file/page/raw_line),
    плюс впервые хранит unit/ref/флаг и вердикты оракулов. Аддитивно,
    идемпотентно (IF NOT EXISTS), поведение бота не меняет."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lab_results_staging (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id            TEXT NOT NULL,
                extractor_version TEXT NOT NULL,
                source_file       TEXT NOT NULL,
                page              INTEGER,
                raw_line          TEXT,
                bbox              TEXT,
                date              TEXT NOT NULL,
                panel             TEXT,
                raw_name          TEXT,
                canonical_name    TEXT,
                value             REAL,
                value_text        TEXT,
                unit              TEXT,
                ref_low           REAL,
                ref_high          REAL,
                doc_flag          TEXT,
                pass1_value       REAL,
                pass2_value       REAL,
                parser_value      REAL,
                value_agreement   TEXT,
                unit_agreement    TEXT,
                ref_agreement     TEXT,
                field_evidence    TEXT,
                oracle_status     TEXT,
                oracle_notes      TEXT,
                confidence        TEXT,
                review_status     TEXT DEFAULT 'pending',
                created_at        TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stg_run ON lab_results_staging(run_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stg_src ON lab_results_staging(source_file)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stg_review ON lab_results_staging(review_status)")
        # B1 (2026-06-30): датчик происхождения даты — read|inherited|fallback
        _cols = [r[1] for r in conn.execute("PRAGMA table_info(lab_results_staging)")]
        if "date_source" not in _cols:
            conn.execute("ALTER TABLE lab_results_staging ADD COLUMN date_source TEXT")
        # 2026-08-31: момент СМЕНЫ статуса. До колонки датчик очереди ревью мерил
        # возраст от created_at (разбор документа) — «ждёт 23 дня» при реальных нуле:
        # строка попадала к человеку в день триажа, а числился за ней весь срок с
        # разбора. Старые строки остаются NULL — их историю никто не мерил, читатель
        # берёт COALESCE(status_changed_at, created_at) и честно завышает, как раньше.
        if "status_changed_at" not in _cols:
            conn.execute("ALTER TABLE lab_results_staging ADD COLUMN status_changed_at TEXT")
        # 2026-07-29: согласие проходов ПО ПОЛЯМ. `value_agreement` считает только value —
        # значит частота расхождений по единице и референсу до сих пор была НЕизмерима.
        # Аддитивно и только для ЗАМЕРА: маршрут (`lab_backfill._route`) эти колонки не
        # читает. Строки до этой даты останутся NULL — это честно, их никто не мерил.
        for _c in ("unit_agreement", "ref_agreement", "field_evidence"):
            if _c not in _cols:
                conn.execute(f"ALTER TABLE lab_results_staging ADD COLUMN {_c} TEXT")
        # 2026-07-29: `agreement` → `value_agreement`. Имя врало: колонка сравнивала ТОЛЬКО
        # `value`, а рядом теперь стоят `unit_agreement` и `ref_agreement` — на их фоне
        # `agreement` читается как «согласие в целом», то есть врёт ещё сильнее, чем врало.
        # RENAME COLUMN сохраняет данные (SQLite ≥3.25); откат — обратный RENAME.
        if "agreement" in _cols and "value_agreement" not in _cols:
            conn.execute("ALTER TABLE lab_results_staging "
                         "RENAME COLUMN agreement TO value_agreement")
        # МАТЕРИАЛ ПРОБЫ и его происхождение хранятся отдельно: имя панели
        # (lab_promote._SPECIMEN_BY_PANEL) не доказывает материал строки.
        # В выдуманном бланке заголовок «Материал-А» нельзя заменить на «Материал-Б»
        # только потому, что панель обычно связывают с последним.
        # specimen — сырая строка бланка; сведение к классу — lab_promote._specimen.
        # specimen_source — read_header|read_footer|continuation|unknown (lab_specimen).
        # page_role — data|derived_chart: график тех же чисел не импортируется.
        # Не прочитанный у старой строки материал остаётся NULL.
        for _c in ("specimen", "specimen_source", "page_role"):
            if _c not in _cols:
                conn.execute(f"ALTER TABLE lab_results_staging ADD COLUMN {_c} TEXT")
        # ОПЕРАТОР СРАВНЕНИЯ: запись «< порога» задаёт верхнюю границу результата,
        # а не измеренное значение. value хранит границу, value_op — оператор;
        # NULL означает точное измерение.
        # У скана без текстового слоя поиск оператора даёт ложный отрицательный ответ.
        # Поэтому исторические строки нельзя исправить без перечитывания бланков.
        if "value_op" not in _cols:
            conn.execute("ALTER TABLE lab_results_staging ADD COLUMN value_op TEXT")
        # 2026-07-31, шаг 5 нити loinc-name-home: МЕТОД ИССЛЕДОВАНИЯ.
        # Бланк может содержать один гормон дважды — в стероидном профиле
        # (ЖХ-МС/МС) и в иммуноанализе на другом приборе, с разными
        # референсами. Один забор, один материал, одна
        # дата, два прибора. До этих колонок различие не хранилось НИГДЕ: `panel`
        # несёт грубый класс распознавателя (`hormones`), а не заголовок бланка.
        # ДВЕ колонки по той же причине, что у материала: метод без своей ступени
        # неотличим от назначенного.
        # `method`        — то, что НАПЕЧАТАНО в бланке (заголовок секции,
        #                   название исследования либо метод в имени строки);
        # `method_source` — read_name|read_section|read_study|unknown (lab_specimen).
        # Строки, чей документ не PDF или не найден, остаются NULL честно.
        for _c in ("method", "method_source"):
            if _c not in _cols:
                conn.execute(f"ALTER TABLE lab_results_staging ADD COLUMN {_c} TEXT")


def _migrate_lab_freshness_twotier():
    """Two-tier сроки валидности (2026-07-01): interval_stable_days (базовый для
    стабильного состояния) + interval_doctor_days (рекомендация врача, доминирует).
    effective = doctor ?? stable. Аддитивно, идемпотентно."""
    with get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(lab_monitoring_schedule)")]
        if "interval_stable_days" not in cols:
            conn.execute("ALTER TABLE lab_monitoring_schedule ADD COLUMN interval_stable_days INTEGER")
        if "interval_doctor_days" not in cols:
            conn.execute("ALTER TABLE lab_monitoring_schedule ADD COLUMN interval_doctor_days INTEGER")


def _migrate_lab_value_op():
    """Оператор сравнения в КАНОНЕ (2026-07-31). Пара к колонке в staging: число
    доезжает потолком, оператор — вместе с ним, иначе на границе staging→канон
    знание теряется ровно там, где его начинают читать (бриф, консилиум, тренд)."""
    with get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(lab_results)")]
        # Пустой PRAGMA = таблицы НЕТ (тестовая фикстура создаёт её после init_db).
        # Без этой проверки ALTER падает и роняет init_db целиком — соседняя
        # миграция `_migrate_lab_uniq_specimen` защищена тем же способом.
        if cols and "value_op" not in cols:
            conn.execute("ALTER TABLE lab_results ADD COLUMN value_op TEXT")


def _migrate_lab_uniq_specimen():
    """idx_lab_uniq += specimen (2026-07-01). specimen стал дименсией дедупа промоута
    (date,canonical,specimen); старый UNIQUE(date,test_name,source) без specimen ронял
    промоут IntegrityError, когда blood+urine одного аналита шли из одного источника.
    Идемпотентно: пересоздаём индекс со specimen, если колонка есть и её ещё нет в индексе."""
    with get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(lab_results)")]
        if "specimen" not in cols:
            return
        idx = conn.execute("SELECT sql FROM sqlite_master WHERE name='idx_lab_uniq'").fetchone()
        if idx and "specimen" in (idx[0] or ""):
            return  # уже со specimen
        conn.execute("DROP INDEX IF EXISTS idx_lab_uniq")
        conn.execute("CREATE UNIQUE INDEX idx_lab_uniq ON lab_results(date, test_name, source, specimen)")


def _migrate_lab_method():
    """МЕТОД в каноне и в ключе уникальности (2026-07-31, шаг 6 нити loinc-name-home).

    Пара к колонке в staging. Без неё различие «один гормон масс-спектрометрией
    против него же иммуноанализом» теряется ровно на границе staging→канон — там, где
    его начинают читать тренд, бриф и консилиум.

    ЛОВУШКА, ради которой индекс идёт через COALESCE: SQLite допускает несколько
    строк с NULL в уникальном ключе. Если метод не указан, индекс прямо по колонке
    не остановит повтор. COALESCE сводит пустой метод к одному значению,
    сохраняя дедупликацию для строк без метода.

    Индекс с методом СТРОЖЕ прежнего: он различает то же самое плюс метод. Значит
    пересоздание не может упасть на данных, на которых держался прежний.
    """
    with get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(lab_results)")]
        if not cols:  # таблицы нет (фикстура создаёт её после init_db)
            return
        if "method" not in cols:
            conn.execute("ALTER TABLE lab_results ADD COLUMN method TEXT")
        idx = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='idx_lab_uniq'").fetchone()
        if idx and "method" in (idx[0] or ""):
            return
        conn.execute("DROP INDEX IF EXISTS idx_lab_uniq")
        conn.execute("CREATE UNIQUE INDEX idx_lab_uniq ON lab_results("
                     "date, test_name, source, COALESCE(specimen,''), COALESCE(method,''))")


def _migrate_specialized_lab_results():
    """specialized_lab_results (2026-07-04, BL-LAB-CANON-2): long-format для спец-панелей,
    НЕ ложащихся в биохимию крови lab_results — микробиом (виды+обилие), аутоантитела
    (титры/КП), метаболомика (орг.кислоты), иммунофенотип (CD %/абс), электрофорез
    (фракции), стул-маркеры (кальпротектин/зонулин/эластаза). Дискриминатор panel_type +
    specimen + method. Единственный писатель — промоут (расширение lab_promote), как у
    lab_results (Primary-Based Protocol). Идемпотентно."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS specialized_lab_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                source TEXT,
                panel_type TEXT,
                specimen TEXT,
                analyte_raw TEXT,
                analyte_canonical TEXT,
                value REAL,
                value_text TEXT,
                unit TEXT,
                ref_low REAL,
                ref_high REAL,
                flag TEXT,
                method TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_speclab_date ON specialized_lab_results(date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_speclab_panel ON specialized_lab_results(panel_type)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_speclab_uniq "
                     "ON specialized_lab_results(date, analyte_raw, source, panel_type)")


def _migrate_analyte_norm_verdicts():
    """Вердикт «у аналита нормы НЕТ / вопрос отложен» — дом решения человека (2026-09-03).

    Датчик покрытия (`check_norm_coverage`) звонил ежедневно про расчётные отношения
    (Chol_HDL_ratio, Albumin_Globulin_ratio ×11): бланк клиники печатает их БЕЗ колонки
    Normal values (проверено глазами на бланке), документа нормы для отношений нет, а
    оракул — врач, который «без необходимости не появится». У вопроса не было дома,
    поэтому он повторялся как новый (§13 banner blindness; §9 — вердикт = данные с
    оракулом). Решение владельца 03.09: таблица в health.db, не JSON parked_decisions —
    вердикт о норме живёт рядом с нормой, в бэкапе R1 и per-tenant.

    ДВА вида и ОБА с датой пересмотра (линза согласованности, R4): `deferred` — ждёт
    оракула до review_at; `no_norm` — нормы нет по решению (расчётный, судится по
    компонентам), но и он гаснет в review_at: лаборатория может начать печатать
    референс, и вердикт не должен пережить предмет (§18). После review_at датчик
    звонит снова — это гаситель, а не дефект. CHECK — нативно (ступень 3 лестницы).
    Читатель — `labs_db.analyte_norm_verdicts`, писатель — `labs_db.set_analyte_norm_verdict`.
    """
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analyte_norm_verdicts (
                analyte    TEXT PRIMARY KEY,
                verdict    TEXT NOT NULL CHECK (verdict IN ('deferred','no_norm')),
                rationale  TEXT NOT NULL CHECK (rationale <> ''),
                oracle     TEXT NOT NULL CHECK (oracle <> ''),
                decided_on TEXT NOT NULL,
                review_at  TEXT NOT NULL CHECK (review_at > decided_on),
                created_at TEXT DEFAULT (datetime('now'))
            )""")


def _migrate_lab_domain_verdicts():
    """Вердикт «класс лабораторной строки → дом» (работа B, 2026-08-01).

    ПОЧЕМУ ТАБЛИЦА, А НЕ СЛОВАРЬ В КОДЕ. Наличие числового значения, единицы
    и границы само по себе не отличает таксон от аналита: оба могут иметь эти поля.
    Класс строки и его допустимый дом — разные вопросы. Принадлежность класса
    к дому задаёт человек (§9); вердикт хранится с датой и обоснованием.

    ПОЧЕМУ CHECK, А НЕ ПРОВЕРКА В PYTHON: домов ровно два, и это структура, а не
    политика. Нативный констрейнт SQLite краснеет у ЛЮБОГО писателя, включая
    ручной sqlite3 (ступень 3 лестницы).

    Отсутствие вердикта — НЕ умолчание, а находка: новый класс обязан краснеть.
    Читатель — `labs_db.domain_home`, датчик — `integrity_tests`.
    """
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lab_domain_verdicts (
                panel_type TEXT PRIMARY KEY,
                home       TEXT NOT NULL CHECK (home IN ('canon','specialized')),
                decided_on TEXT NOT NULL,
                oracle     TEXT NOT NULL,
                rationale  TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )""")
        # Seed = вердикты владельца 2026-07-31 (§9, п.4 закрытого списка: init
        # пишет в таблицу, рантайм читает ИЗ ТАБЛИЦЫ, а не из литерала).
        # INSERT OR IGNORE: ПРАВКА вердикта человеком (UPDATE) переживает рестарт,
        # УДАЛЕНИЕ — нет, seed вернёт строку на следующем init_db. Названо вслух,
        # потому что «удалил, а оно вернулось молча» — ровно тот класс сюрприза,
        # против которого весь этот слой. Снять класс с учёта = сменить home,
        # а не удалить строку.
        seed = [
            ("microbiome", "specialized", "владелец",
             "таксон — организм, не аналит; тренд по обилию видов не ложится "
             "в числовой стор события"),
            # Профильная панель без канонических имён и продольного ряда
            # относится к специализированному дому. Придумывать имена ради помещения
            # в числовой стор события нельзя.
            # Граница классификатора: уже канонизированная строка может остаться
            # в каноне; общий заголовок панели не переопределяет её класс.
            # INSERT OR IGNORE меняет только начальное заполнение. Для существующей
            # базы смену дома выполняет migrations/metabolomics_home_20260914.py.
            ("metabolomics", "specialized", "владелец",
             "профиль органических кислот мочи: однократная панель, по одному "
             "замеру на показатель, тренда нет, имена пришлось бы назначать авторски — "
             "дом профильной панели, как у микробиома (решение 2026-09-14)"),
            ("amino_acids", "canon", "владелец", "рядовые количественные результаты"),
            ("stool_markers", "canon", "владелец", "рядовые количественные результаты"),
            ("trace_elements", "canon", "владелец",
             "нормальные единицы и границы; тяжёлые металлы обязаны попадать "
             "в тренд и консилиум; материал разводит колонка specimen"),
            ("fatty_acids", "canon", "владелец", "рядовые количественные результаты"),
            ("gastro_markers", "canon", "владелец", "рядовые количественные результаты"),
            ("electrophoresis", "canon", "владелец", "рядовые количественные результаты"),
            ("immunophenotype", "canon", "владелец", "рядовые количественные результаты"),
            ("autoantibodies", "canon", "владелец", "рядовые количественные результаты"),
            # Канонический класс получает вердикт. Его вариант на другом языке
            # должен нормализовать распознаватель: отдельный дом для варианта
            # написания узаконил бы ошибку классификации как новую сущность.
            ("oncomarkers", "canon", "владелец",
             "онкомаркеры крови — рядовые количественные результаты, обязаны "
             "попадать в тренд и консилиум"),
            ("other", "canon", "владелец",
             "мусорная корзина суб-классификации: строки с единицей и границей, "
             "класс которых не назван — дом канон, видимость даёт датчик"),
            # Панели биохимии крови: дом канон по построению, вердикт записан
            # явно, чтобы «нет вердикта» означало ровно новый класс.
            ("cbc", "canon", "построение", "биохимия крови — исходный дом канона"),
            ("chemistry", "canon", "построение", "биохимия крови — исходный дом канона"),
            ("lipids", "canon", "построение", "биохимия крови — исходный дом канона"),
            ("hormones", "canon", "построение", "биохимия крови — исходный дом канона"),
            ("vitamins", "canon", "построение", "биохимия крови — исходный дом канона"),
            ("tumor_markers", "canon", "построение", "биохимия крови — исходный дом канона"),
            ("coagulation", "canon", "построение", "биохимия крови — исходный дом канона"),
            ("cardiac", "canon", "построение", "биохимия крови — исходный дом канона"),
            ("immunology", "canon", "построение", "биохимия крови — исходный дом канона"),
            ("markers", "canon", "построение", "биохимия крови — исходный дом канона"),
            ("urine", "canon", "построение",
             "панель мочи распознавателя; материал разводит specimen, не дом"),
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO lab_domain_verdicts "
            "(panel_type, home, decided_on, oracle, rationale) VALUES (?,?,?,?,?)",
            # Дата вердикта — часть его провенанса, а не украшение: по ней
            # видно, какие классы человек судил разом, а какие догоняли позже.
            [(pt, home, {"oncomarkers": "2026-08-01"}.get(pt, "2026-07-31"),
              oracle, why) for pt, home, oracle, why in seed])


def _migrate_trend_thresholds():
    """Трендовые/оконные пороги (BL-ALERT-TREND-1, 2026-07-04): «N дней подряд» /
    «скользящее среднее» — то, что single-day absolute_thresholds выразить не может.
    Значения-нормы здесь (данные), оконная логика — trend_alerts.py (код)."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trend_thresholds (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                metric          TEXT NOT NULL,
                direction       TEXT NOT NULL,
                value           REAL NOT NULL,
                window_days     INTEGER NOT NULL,
                mode            TEXT NOT NULL,
                level           TEXT NOT NULL,
                domain          TEXT DEFAULT '',
                reason_template TEXT NOT NULL,
                source          TEXT NOT NULL,
                source_date     TEXT,
                active          INTEGER NOT NULL DEFAULT 1,
                updated_at      TEXT DEFAULT (datetime('now')),
                UNIQUE(metric, direction, window_days, mode, level)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trend_thresh_active ON trend_thresholds(active)")
        # brief-neutralization Фаза 3a-2: condition_key гейтит правило по состоянию тенанта
        # (пусто = не гейтится). Онко-специфичное правило (sleep_score 14д medical) → 'oncology'.
        try:
            conn.execute("ALTER TABLE trend_thresholds ADD COLUMN condition_key TEXT DEFAULT ''")
        except Exception:
            pass  # silent-ok: колонка уже есть (идемпотентный ALTER)

        # ── lab_trend_thresholds (safety-net-thresholds, аудит E1 2026-07-18) ──
        # Отдельный дом от trend_thresholds: линейка иная — «изменение на pct% за N
        # ПОСЛЕДНИХ ИЗМЕРЕНИЙ» (не за N дней). Лабы сдаются нерегулярно; 3 измерения
        # CEA = могут быть за полгода. Источник данных — lab_results (строки), не
        # daily_metrics (колонки) → отдельный reader (safety_net.check_lab_trends),
        # daily-whitelist сюда не применяется.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lab_trend_thresholds (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                metric          TEXT NOT NULL,
                direction       TEXT NOT NULL,
                pct_change      REAL NOT NULL,
                n_readings      INTEGER NOT NULL,
                level           TEXT NOT NULL,
                reason_template TEXT NOT NULL DEFAULT '',
                source          TEXT NOT NULL,
                source_date     TEXT,
                active          INTEGER NOT NULL DEFAULT 1,
                updated_at      TEXT DEFAULT (datetime('now')),
                UNIQUE(metric, direction)
            )
        """)
        _canonize_threshold_metrics(conn, "lab_trend_thresholds")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lab_trend_active ON lab_trend_thresholds(active)")
        # Пол по величине (решение владельца 2026-09-03: «колебания у границы нормы в пределах
        # 20 % от неё должны приходить; если нижняя граница 0 — имеет значение только верхняя»).
        # Колонка на строке порога, провенанс рядом: порог решения, оракул владелец (§9).
        cols = {r[1] for r in conn.execute("PRAGMA table_info(lab_trend_thresholds)")}
        if "near_boundary_share" not in cols:
            conn.execute("ALTER TABLE lab_trend_thresholds ADD COLUMN near_boundary_share REAL")
            conn.execute("ALTER TABLE lab_trend_thresholds ADD COLUMN near_boundary_source TEXT")


from collections.abc import Mapping


class _ReasonTemplates(Mapping):
    """Keep the legacy template mapping without loading dictionaries during import."""

    def __init__(self, keys):
        self._keys = keys

    def __getitem__(self, key):
        return i18n.t(self._keys[key], "ru")

    def __iter__(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._keys)


# Тексты оконных тревог — ОДИН дом: число берётся из самого порога ({thr}),
# чтобы сообщение не расходилось с правилом после персонализации.
# Ключ — идентичность правила (metric, окно, режим).
_TREND_REASONS = _ReasonTemplates({
    ('sleep_score', 14, 'avg'):
        'health_db.reason.sleep_score_average',
    ('sleep_score', 3, 'consecutive'):
        'health_db.reason.sleep_score_consecutive',
    ('sleep_deep', 3, 'consecutive'):
        'health_db.reason.deep_sleep_consecutive',
    ('hrv', 2, 'consecutive'):
        'health_db.reason.hrv_consecutive',
    ('readiness', 2, 'consecutive'):
        'health_db.reason.readiness_consecutive',
})


def _seed_trend_thresholds():
    """Seed трендовых правил из конституций (ревью алертов 2026-07-04). INSERT OR IGNORE.

    Значения — стартовые для тенанта без своего ряда; _personalize_trend_thresholds выводит
    личные. Ни одно не снято с чужого ряда (BL-PUB-16 а): ВСР и глубокий сон — 0 («молчит, пока
    нет своих данных»), готовность и sleep_score — шкала прибора (<70)."""
    R = _TREND_REASONS
    seeds = [
        ('sleep_score', 'floor', 70.0, 14, 'avg', 'medical', 'sleep',
         R[('sleep_score', 14, 'avg')], 'bootstrap_pop_norm', '2026-09-27'),
        ('sleep_score', 'floor', 70.0, 3, 'consecutive', 'load', 'sleep',
         R[('sleep_score', 3, 'consecutive')], 'bootstrap_pop_norm', '2026-09-27'),
        ('sleep_deep', 'floor', 0.0, 3, 'consecutive', 'load', 'sleep',
         R[('sleep_deep', 3, 'consecutive')], 'bootstrap_until_own_data', '2026-09-27'),
        ('hrv', 'floor', 0.0, 2, 'consecutive', 'load', 'nervous_system',
         R[('hrv', 2, 'consecutive')], 'bootstrap_until_own_data', '2026-09-27'),
        ('readiness', 'floor', 70.0, 2, 'consecutive', 'load', 'movement',
         R[('readiness', 2, 'consecutive')], 'bootstrap_pop_norm', '2026-09-27'),
    ]
    with get_conn() as conn:
        for s in seeds:
            conn.execute(
                "INSERT OR IGNORE INTO trend_thresholds "
                "(metric,direction,value,window_days,mode,level,domain,reason_template,source,source_date) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)", s)


def _seed_lab_trend_thresholds():
    """Пороги ЛАБОРАТОРНЫХ трендов: с 2026-09-02 (нить norm-from-documents) pct_change —
    односторонний RCV из биологической вариации EFLM (снимок data/norm_docs/eflm_bv.json,
    norm_documents.rcv_pct: z=1.65, CV_A = 0.5·CV_I по желательной APS, пока лаборатория не дала
    свою — system_config 'norm.cva_source'). Направление и уровень тревоги — клиническая
    конвенция наблюдения (рост онкомаркера — urgent; падение Hb/альбумина, рост MCV — warn),
    не число из документа; число — из документа. n_readings=2: RCV судит две соседние точки.
    Прежние литералы (50/15/5/10 %, пересказ) переписываются на месте по UNIQUE(metric,direction)."""
    import norm_documents
    if not norm_documents.EFLM_SNAPSHOT.exists():
        # Чистая установка без снимка EFLM (лицензия не даёт его распространять — NOTICE.md):
        # порога без документа нет, строк не сеем; safety_net громко уходит в резерв.
        # С 25.09 (нить eflm-rule) ручной карты аналитов нет — состав снимка выводится из словаря
        # канона, поэтому скачать может любая установка.
        log.warning("пороги лаб-трендов не посеяны: нет снимка EFLM %s — "
                    "scripts/install.py --apply --fetch-eflm (нужна сеть)", norm_documents.EFLM_SNAPSHOT)
        return
    snap = norm_documents.load_eflm()
    fetched = snap.get("fetched", "")
    # (metric, direction, level, reason) — конвенция; pct — EFLM
    conv = [
        ('CEA',     'up',   'urgent', 'CEA растёт: +{pct:.0f}% за {n} измерения — онкомаркер, сдвиг больше биологического шума (RCV)'),
        ('CA19-9',  'up',   'urgent', 'CA19-9 растёт: +{pct:.0f}% за {n} измерения — сдвиг больше RCV'),
        ('HGB',     'down', 'warn',   'HGB падает: {pct:.0f}% за {n} измерения — сдвиг больше RCV'),
        ('MCV',     'up',   'warn',   'MCV растёт: +{pct:.0f}% за {n} измерения — сдвиг больше RCV'),
        ('Albumin', 'down', 'warn',   'Альбумин падает: {pct:.0f}% за {n} измерения — сдвиг больше RCV'),
    ]
    with get_conn() as conn:
        for metric, direction, level, reason in conv:
            if metric not in snap["analytes"]:
                # Снимок собран правилом, а не картой: аналит мог выпасть (EFLM переименовал его или
                # в справочнике появился двойник). Порога без документа нет — строку не сеем, но громко.
                log.warning("порог лаб-тренда %s/%s не посеян: в снимке EFLM нет %s", metric, direction, metric)
                continue
            pct = norm_documents.rcv_pct(metric, snapshot=snap)
            src = f"EFLM_BV_{snap['analytes'][metric]['updated_at'][:10]}"
            conn.execute(
                "INSERT OR IGNORE INTO lab_trend_thresholds "
                "(metric,direction,pct_change,n_readings,level,reason_template,source,source_date) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (metric, direction, pct, 2, level, reason, src, fetched))
            # пересказ → документ, на месте (UNIQUE не даёт второй строки)
            conn.execute(
                "UPDATE lab_trend_thresholds SET pct_change=?, n_readings=2, source=?, source_date=?, "
                "reason_template=?, updated_at=datetime('now') "
                "WHERE metric=? AND direction=? AND (source LIKE 'safety_net_migration%' OR source LIKE 'EFLM_BV_%')",
                (pct, src, fetched, reason, metric, direction))
        # Пол по величине — слово владельца 2026-09-03 («у границы нормы в пределах 20 % от неё»);
        # только где не задано: значение владельца из БД сид не перетирает. Применимость
        # решают ДАННЫЕ бланка (safety_net: нижняя граница 0/нет → судится только ULN),
        # двусторонний интервал (HGB, Albumin) правило не трогает — прочтение (а) владельца.
        conn.execute(
            "UPDATE lab_trend_thresholds SET near_boundary_share=?, near_boundary_source=? "
            "WHERE near_boundary_share IS NULL",
            (LAB_TREND_NEAR_BOUNDARY_SHARE, "owner_word:2026-09-03"))


# Резерв пола по величине (§9 п.4: сид пишет в таблицу, рантайм читает из таблицы через
# rules_db, переопределяемо по строке). Число — слово владельца 2026-09-03, не литература.
LAB_TREND_NEAR_BOUNDARY_SHARE = 0.2

_LIFESTYLE_SAFETY_METRICS = ('spo2', 'readiness', 'sleep_score')   # daily-строки variant='safety_net'


def _seed_safety_lab_thresholds():
    """Абсолютные ЛАБ-пороги safety_net → absolute_thresholds (variant='safety_net').

    С 2026-09-02 (нить norm-from-documents) строки ВЫВОДЯТСЯ из снимка документа
    data/norm_docs/ctcae_lab_v5.0.json (norm_documents.snapshot_ctcae: NCI CTCAE v5.0 xlsx →
    грейды 1/2/3 → warn/urgent/critical; кратные ULN/LLN — kind='relative', baseline='ULN'|'LLN').
    Ни одного набранного числа: прежние 64 литерала (пересказ CTCAE по памяти модели, апрель)
    уходят в архив — variant='safety_net_legacy', active=0 — чтобы UNIQUE освободил ключ и
    история осталась читаемой. Amylase — личный порог владельца, остаётся как был.
    Резерв safety_net._FALLBACK_LAB читает ТОТ ЖЕ снимок (coherence-тест: снимок ≡ БД)."""
    import norm_documents
    if not norm_documents.CTCAE_SNAPSHOT.exists():
        # Чистая установка без снимка CTCAE (коды MedDRA — NOTICE.md): порога без документа
        # нет, строк не сеем; safety_net громко уходит в резерв.
        log.warning("лаб-пороги safety_net не посеяны: нет снимка CTCAE %s — в публичную версию он не входит "
                    "(лицензия кодов MedDRA не проверена, NOTICE.md); safety_net работает на резерве",
                    norm_documents.CTCAE_SNAPSHOT)
        return
    snap = norm_documents.load_ctcae_rows()
    doc = snap["document"]
    src, src_date = doc["id"], doc["issued"]
    with get_conn() as conn:
        # 1) архивировать пересказ (идемпотентно: второй прогон ничего не находит)
        # только ЛАБ-строки: lifestyle-строки того же source (spo2/readiness/sleep_score) сеет
        # _seed_safety_lifestyle_thresholds и они остаются; повторный прогон не трогает уже
        # архивированный ключ (иначе UNIQUE)
        ph = ",".join("?" * len(_LIFESTYLE_SAFETY_METRICS))
        conn.execute(
            f"UPDATE absolute_thresholds SET variant='safety_net_legacy', active=0, "
            f"updated_at=datetime('now') WHERE variant='safety_net' "
            f"AND source='safety_net_migration_2026-07-18' AND metric NOT IN ({ph}) "
            f"AND NOT EXISTS (SELECT 1 FROM absolute_thresholds l WHERE l.metric=absolute_thresholds.metric "
            f"AND l.direction=absolute_thresholds.direction AND l.band_label=absolute_thresholds.band_label "
            f"AND l.variant='safety_net_legacy')", _LIFESTYLE_SAFETY_METRICS)
        # 1b) предыдущая версия того же документа → в архив под своим именем (UNIQUE освобождён,
        #     история читаема: variant='superseded:<id>'); смена версии = смена CTCAE_CURRENT
        for (old_src,) in conn.execute(
                "SELECT DISTINCT source FROM absolute_thresholds WHERE variant='safety_net' "
                "AND active=1 AND source LIKE 'CTCAE_%' AND source != ?", (src,)).fetchall():
            conn.execute(
                "UPDATE absolute_thresholds SET variant=?, active=0, updated_at=datetime('now') "
                "WHERE variant='safety_net' AND source=? AND NOT EXISTS (SELECT 1 FROM absolute_thresholds l "
                "WHERE l.metric=absolute_thresholds.metric AND l.direction=absolute_thresholds.direction "
                "AND l.band_label=absolute_thresholds.band_label AND l.variant=?)",
                (f"superseded:{old_src}", old_src, f"superseded:{old_src}"))
        # 2) строки документа
        nxt = str(get_today() + timedelta(days=int(doc.get("next_check_days") or 365)))
        for r in snap["rows"]:
            reason = (f"{r['metric']} {{val:g}} {r['unit']} — CTCAE {doc['version']} "
                      f"«{r['term']}» grade {r['grade']}")
            conn.execute(
                "INSERT OR IGNORE INTO absolute_thresholds "
                "(metric,direction,value,reason_template,source,source_date,kind,baseline,"
                " band_label,variant,norm_kind,next_review) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["metric"], r["direction"], r["value"], reason, src, src_date, r["kind"],
                 r["baseline"], r["band"], "safety_net", "decision_threshold", nxt))
        # 2b) Glucose-потолки CTCAE v6 — грейды по глюкозе НАТОЩАК; бланк не различает,
        #     профиль знает (routine.fasting_labs). Условие активности — данные профиля,
        #     не допущение: не натощак → строки неактивны (вид 1 с бланка остаётся).
        try:
            fasting = conn.execute("SELECT value_text FROM patient_profile WHERE key='routine.fasting_labs'").fetchone()
            fasting_ok = fasting is None or str(fasting[0]).strip().lower() in ("true", "1", "yes", "да")
        except Exception:  # noqa: BLE001 — таблицы профиля может не быть (урезанная фикстура)
            fasting_ok = True
        conn.execute("UPDATE absolute_thresholds SET active=? WHERE metric='Glucose' AND direction='ceiling' "
                     "AND variant='safety_net' AND source=?", (1 if fasting_ok else 0, src))
        # 3) Личный порог амилазы владельца здесь больше НЕ сеется (27.09, BL-PUB-16 а): сид
        # шёл в БД ЛЮБОГО тенанта, и у второго стоял чужой «личный» порог с подписью
        # «норма пациента». Личная норма — данные тенанта: у владельца строки остались в его БД,
        # у партнёра сняты актом plans/personal_threshold_parity_20260927.py.


# Стратифицированные цели (norm_kind='stratified_target'): число — из решения врача-документа
# (norm_documents, data/norm_docs/), страта — состояние clinical_kb. Строка активна ТОЛЬКО у
# тенанта, у которого страта активна: у остальных аналит судит референс бланка (вид 1).
_STRATIFIED_TARGETS_PATH = Path(__file__).resolve().parent / "data" / "norm_docs" / "stratified_targets.json"


def _stratified_targets() -> list[dict]:
    """Нет файла (приватная зона: решения врача тенанта) — у установки нет целей, не ошибка."""
    if not _STRATIFIED_TARGETS_PATH.exists():
        return []
    return json.loads(_STRATIFIED_TARGETS_PATH.read_text(encoding="utf-8"))["targets"]


def _seed_stratified_targets(active_conds: set | None = None):
    """Засеять цели врача и включить их по страте СМОТРЯЩЕГО тенанта.

    active_conds — override для тестов; по умолчанию clinical_kb.active_conditions(conn).
    Расписание контроля (lab_monitoring_schedule) пишется, только если прежнее решение врача
    СТАРШЕ документа: более новое назначение (encounter позже даты документа) не перетирается."""
    with get_conn() as conn:
        if active_conds is None:
            try:
                import clinical_kb
                active_conds = clinical_kb.active_conditions(conn)
            except Exception as e:  # noqa: BLE001 — нет страты = цель выключена, громко
                logging.getLogger(__name__).warning("stratified targets: страты не прочитать (%s) — цели выключены", e)
                active_conds = set()
        for t in _stratified_targets():
            on = 1 if t["stratum"] in active_conds else 0
            reason = f"{t['metric']} {{val:g}} {t['unit']} — {t['note']}"
            for direction in ("floor", "ceiling"):
                conn.execute(
                    "INSERT OR IGNORE INTO absolute_thresholds (metric,direction,value,reason_template,"
                    "source,source_date,kind,baseline,band_label,variant,norm_kind,next_review,stratum,active) "
                    "VALUES (?,?,?,?,?,?,'absolute',NULL,'warn','safety_net','stratified_target',?,?,?)",
                    (t["metric"], direction, t[direction], reason, t["doc"], t["doc_date"],
                     t["next_review"], t["stratum"], on))
                # Документ — первичная копия, строка — реплика: правка документа (число, текст,
                # срок пересмотра) обязана доехать при следующем init_db. До 21.09 здесь
                # обновлялся один `active`, и правка документа молча оставалась в документе.
                conn.execute(
                    "UPDATE absolute_thresholds SET active=?, value=?, reason_template=?, source_date=?, "
                    "next_review=?, stratum=?, norm_kind='stratified_target', updated_at=datetime('now') "
                    "WHERE metric=? AND direction=? AND variant='safety_net' AND source=?",
                    (on, t[direction], reason, t["doc_date"], t["next_review"], t["stratum"],
                     t["metric"], direction, t["doc"]))
            if not on:
                continue
            cur = conn.execute("SELECT source, effective_from FROM lab_monitoring_schedule WHERE test_name=?",
                               (t["metric"],)).fetchone()
            if cur is not None and (cur["source"] == f"encounter:{t['doc']}"
                                    or (cur["effective_from"] or "") > t["doc_date"]):
                continue
            conn.execute(
                "INSERT INTO lab_monitoring_schedule (test_name, interval_days, priority, source, effective_from, "
                "note, updated_at, interval_doctor_days) VALUES (?,?,?,?,?,?,datetime('now'),?) "
                "ON CONFLICT(test_name) DO UPDATE SET interval_days=excluded.interval_days, "
                "source=excluded.source, effective_from=excluded.effective_from, note=excluded.note, "
                "updated_at=excluded.updated_at, interval_doctor_days=excluded.interval_doctor_days",
                (t["metric"], t["interval_days"], "medium", f"encounter:{t['doc']}", t["doc_date"],
                 t["note"], t["interval_days"]))


def _seed_safety_lifestyle_thresholds():
    """Lifestyle-пороги safety_net → absolute_thresholds (§9-вынос E1, механический).

    Абсолютные (variant='safety_net'): spo2/readiness/sleep_score, band-полосы, floor.
      Разводятся с brief-полами той же метрики (spo2 floor 94 variant='', readiness 65,
      sleep_score 70) по variant — сосуществуют, не конфликтуют по UNIQUE.
    Относительные (variant='safety_net_rel'): value = пороговое число, формула per-metric
      живёт в safety_net.check_lifestyle_alerts (МЕХАНИЧЕСКИ та же): hrv — drop-фракция vs
      baseline (0.20 = падение 20%); resting_hr — дельта-рост bpm; readiness — дельта-падение pts.
      baseline-поле помечает базис. Резерв _FALLBACK_LIFESTYLE держится равным сенсором coherence."""
    SRC, DATE = 'safety_net_migration_2026-07-18', '2026-07-18'
    abs_rows = [
        ('spo2',        'floor', 90.0, 'critical', 'SpO2 критически низкая: {val:g}%'),
        ('spo2',        'floor', 92.0, 'urgent',   'SpO2 низкая: {val:g}%'),
        ('spo2',        'floor', 94.0, 'warn',     'SpO2 ниже нормы: {val:g}%'),
        ('readiness',   'floor', 30.0, 'urgent',   'Готовность критически низкая: {val:g}'),
        ('readiness',   'floor', 45.0, 'warn',     'Готовность низкая: {val:g}'),
        ('sleep_score', 'floor', 40.0, 'urgent',   'Sleep score критически низкий: {val:g}'),
        ('sleep_score', 'floor', 55.0, 'warn',     'Sleep score низкий: {val:g}'),
    ]
    # (metric, direction, value, band_label, baseline, reason)
    rel_rows = [
        ('hrv',        'floor',   0.20, 'warn',     'avg30_hrv',       'ВСР падение ≥20% vs 30д среднего'),
        ('hrv',        'floor',   0.35, 'urgent',   'avg30_hrv',       'ВСР падение ≥35% vs 30д среднего'),
        ('hrv',        'floor',   0.50, 'critical', 'avg30_hrv',       'ВСР падение ≥50% vs 30д среднего'),
        ('resting_hr', 'ceiling', 8.0,  'warn',     'avg30_rhr',       'ЧСС покоя +8 bpm vs 30д среднего'),
        ('resting_hr', 'ceiling', 15.0, 'urgent',   'avg30_rhr',       'ЧСС покоя +15 bpm vs 30д среднего'),
        ('readiness',  'floor',   20.0, 'warn',     'avg30_readiness', 'Готовность −20 pts vs 30д среднего'),
        ('readiness',  'floor',   35.0, 'urgent',   'avg30_readiness', 'Готовность −35 pts vs 30д среднего'),
    ]
    with get_conn() as conn:
        for m, d, v, band, reason in abs_rows:
            conn.execute(
                "INSERT OR IGNORE INTO absolute_thresholds "
                "(metric,direction,value,reason_template,source,source_date,kind,baseline,band_label,variant) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (m, d, v, reason, SRC, DATE, 'absolute', None, band, 'safety_net'))
        for m, d, v, band, base, reason in rel_rows:
            conn.execute(
                "INSERT OR IGNORE INTO absolute_thresholds "
                "(metric,direction,value,reason_template,source,source_date,kind,baseline,band_label,variant) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (m, d, v, reason, SRC, DATE, 'relative', base, band, 'safety_net_rel'))


def _scrub_leaky_seed_reasons():
    """brief-neutralization Фаза 0: заморозка живой утечки в УЖЕ засеянных БД.

    Обе seed-функции выше используют INSERT OR IGNORE → в существующих тенант-БД остаётся
    СТАРЫЙ (утекающий) reason_template, код-фикс ловят только свежие БД (failure-mode «устаревшая
    реплика»). Этот идемпотентный UPDATE переписывает известные утекающие строки на нейтральный
    текст в БД КАЖДОГО тенанта на init. Матч по (source, metric, ...) — стабильному ключу ряда,
    не по тексту. Значения порогов НЕ трогаем (пол сохранён); чистим только чужой текст/провенанс.
    """
    with get_conn() as conn:
        # absolute_thresholds: личный p10-бэйзлайн владельца в тексте → нейтрально.
        # band_label НЕ трогаем — часть UNIQUE-ключа и ключа поиска get_threshold(band='').
        for metric, reason in (
            ('hrv',        i18n.t('health_db.reason.hrv_low', "ru")),
            ('readiness',  i18n.t('health_db.reason.readiness_low', "ru")),
            ('sleep_deep', i18n.t('health_db.reason.deep_sleep_low', "ru")),
        ):
            conn.execute(
                "UPDATE absolute_thresholds SET reason_template=? "
                "WHERE source='threshold_analysis_2026-04-21' AND direction='floor' AND metric=?",
                (reason, metric))
        # trend_thresholds: текст из единого дома _TREND_REASONS по идентичности правила (не по
        # source — после персонализации source другой, и прежний матч молча промахивался).
        for (metric, window_days, mode), reason in _TREND_REASONS.items():
            conn.execute(
                "UPDATE trend_thresholds SET reason_template=? "
                "WHERE metric=? AND window_days=? AND mode=?",
                (reason, metric, window_days, mode))
        # Фаза 3a-2: онко-специфичное правило (14-дн medical = мониторинг рецидива) гейтится по
        # oncology — не-онко тенант больше НЕ получает medical-эскалацию из устойчиво низкого сна
        # (у него остаётся 3-дн load-правило + percentile-путь). Гейт generic (по condition_key).
        try:
            conn.execute("UPDATE trend_thresholds SET condition_key='oncology' "
                         "WHERE metric='sleep_score' AND window_days=14 AND level='medical'")
        except Exception:
            pass  # silent-ok: колонки нет (миграция ещё не прошла) — сид/миграция поставят


def _derive_metric_percentile(conn, metric: str, pct: int = 10, days: int = 90, min_n: int = 14):
    """p{pct} метрики из daily_metrics ЭТОГО тенанта за последние `days`. None если < min_n
    ненулевых значений. Личное значение выводится per-tenant — НЕ сидтся литералом
    (brief-neutralization Фаза 3a). Метрика — только из белого списка (не user-input)."""
    import math
    if metric not in ("hrv", "readiness", "sleep_deep", "sleep_score", "sleep_total",
                      "sleep_awake"):
        return None
    since = get_today() - timedelta(days=days)
    try:
        rows = conn.execute(
            f"SELECT {metric} AS v FROM daily_metrics WHERE date >= ? AND {metric} IS NOT NULL",
            (str(since),)).fetchall()
    except Exception:  # silent-ok: нет колонки/таблицы → нельзя вывести, вернём None
        return None
    vals = sorted(float(r[0]) for r in rows if r[0] is not None and float(r[0]) > 0)
    if len(vals) < min_n:
        return None
    idx = max(0, math.ceil(pct / 100 * len(vals)) - 1)
    return vals[idx]


def _personalize_absolute_floors(conn=None):
    """brief-neutralization Фаза 3a (решение владельца: ПЕРСОНАЛИЗИРУЕМ). Личные p10-полы hrv/readiness/
    sleep_deep/sleep_score/sleep_total выводятся из данных КАЖДОГО тенанта, а не сидтся бэйзлайном ВЛАДЕЛЬЦА
    (накладка Фазы 0: p10 владельца применялся к партнёру). Пока данных мало (<min_n) — остаётся консервативный
    bootstrap-дефолт из _seed (source не выдаётся за личный). Идемпотентно: рефреш на каждом
    init_db (рестарт). band_label/variant НЕ трогаем (ключ поиска get_threshold)."""
    c = conn or get_conn()
    today = str(get_today())
    for metric in ("hrv", "readiness", "sleep_deep", "sleep_score", "sleep_total"):
        p10 = _derive_metric_percentile(c, metric, pct=10)
        if p10 is None:
            continue  # data-бедный тенант — bootstrap-дефолт остаётся (не чужой личный)
        c.execute(
            "UPDATE absolute_thresholds SET value=?, source=?, source_date=? "
            "WHERE metric=? AND direction='floor' AND band_label='' AND variant='' AND kind='absolute'",
            (round(p10, 3), "p10_personal", today, metric))
    try:
        c.commit()
    except Exception:  # silent-ok: conn без commit (в транзакции вызывающего)
        pass


def _personalize_absolute_ceilings(conn=None):
    """brief-neutralization (числовая ось, потолки — решение владельца «всё выводится из данных»).
    Зеркало _personalize_absolute_floors для верхних «плохих» порогов: sleep_awake выводится
    как личный p90 из данных КАЖДОГО тенанта, не сидтся литералом. Отличие от полов:
    min_n=30 (не 14). Пол ошибается в безопасную сторону (выше пол → флагов БОЛЬШЕ); потолок
    наоборот — шумный высокий p90 на малой выборке ПРОПУСТИТ проблему → требуем больше точек
    (прецедент B2 p5-union, min_n=30). Data-бедный тенант остаётся на консерв. bootstrap.
    Идемпотентно: рефреш на каждом init_db. band_label/variant НЕ трогаем (ключ get_threshold)."""
    c = conn or get_conn()
    today = str(get_today())
    for metric in ("sleep_awake",):
        p90 = _derive_metric_percentile(c, metric, pct=90, min_n=30)
        if p90 is None:
            continue  # data-бедный тенант — bootstrap-дефолт остаётся (не чужой личный)
        c.execute(
            "UPDATE absolute_thresholds SET value=?, source=?, source_date=? "
            "WHERE metric=? AND direction='ceiling' AND band_label='' AND variant='' AND kind='absolute'",
            (round(p90, 3), "p90_personal", today, metric))
    try:
        c.commit()
    except Exception:  # silent-ok: conn без commit (в транзакции вызывающего)
        pass


def _derive_windowed_avg_percentile(conn, metric: str, window_days: int, pct: int = 10,
                                    days: int = 365, min_n: int = 14):
    """p{pct} СКОЛЬЗЯЩИХ `window_days`-средних метрики (для avg-mode trend-правил). None если
    < min_n окон. Порог avg-правила НЕЛЬЗЯ выводить дневным p10: 14-дн среднее почти никогда не
    падает ниже дневного p10 → правило не сработает. Считаем перцентиль по самим окнам-средним.
    Метрика — из белого списка (не user-input). brief-neutralization Фаза 3a-3."""
    import math
    if metric not in ("hrv", "readiness", "sleep_deep", "sleep_score"):
        return None
    since = get_today() - timedelta(days=days)
    try:
        rows = conn.execute(
            f"SELECT {metric} AS v FROM daily_metrics WHERE date >= ? ORDER BY date",
            (str(since),)).fetchall()
    except Exception:  # silent-ok: нет колонки/таблицы → None
        return None
    seq = [r[0] for r in rows]
    means = []
    w = int(window_days)
    for i in range(len(seq) - w + 1):
        chunk = [float(x) for x in seq[i:i + w] if x is not None and float(x) > 0]
        if len(chunk) >= max(1, int(w * 0.7)):  # окно валидно при ≥70% заполнении
            means.append(sum(chunk) / len(chunk))
    means.sort()
    if len(means) < min_n:
        return None
    idx = max(0, math.ceil(pct / 100 * len(means)) - 1)
    return means[idx]


def _personalize_trend_thresholds(conn=None):
    """brief-neutralization Фаза 3a-3 (директива владельца: «никакие числа не константы — вычисляются
    и меняются»). ВСЕ оконные пороги (hrv/readiness/sleep_deep/sleep_score) выводятся из данных
    КАЖДОГО тенанта, а не сидтся литералом владельца. consecutive-mode → дневной p{pct}; avg-mode →
    p{pct} скользящих средних (окно = window_days). Пока данных мало (< min_n) — остаётся
    bootstrap-дефолт из _seed (source не выдаётся за личный). Идемпотентно: рефреш на каждом init_db.
    Метрика — из белого списка _derive (не user-input); ключ UPDATE — (metric,window_days,mode)."""
    c = conn or get_conn()
    today = str(get_today())
    try:
        rows = c.execute(
            "SELECT metric, window_days, mode FROM trend_thresholds WHERE active=1").fetchall()
    except Exception:  # silent-ok: таблицы/колонок нет (миграция не прошла) — нечего выводить
        return
    for metric, window_days, mode in rows:
        if mode == "avg":
            val = _derive_windowed_avg_percentile(c, metric, int(window_days), pct=10)
        else:  # consecutive / прочее оконное → дневной p10 (как абсолютный пол)
            val = _derive_metric_percentile(c, metric, pct=10)
        if val is None:
            continue  # data-бедный тенант — bootstrap-дефолт остаётся (не чужой личный)
        c.execute(
            "UPDATE trend_thresholds SET value=?, source=?, source_date=? "
            "WHERE metric=? AND window_days=? AND mode=? AND active=1",
            (round(float(val), 3), "p10_personal_trend", today, metric, window_days, mode))
    try:
        c.commit()
    except Exception:  # silent-ok: conn без commit (в транзакции вызывающего)
        pass


def _migrate_ecg_readings():
    """ЭКГ Apple Watch через HAE (2026-07-06). Хранит ВЕРДИКТ+метаданные, не сырую волну
    (512 Гц — в провенанс-файле ecg_rest/). Дедуп по (start_time, source). Читатели:
    integrity_tests.check_ecg_nonsinus (кардио-алерт: AFib/High HR) + gp_context. Клинически
    важно при сердечно-сосудистом риске лечения."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ecg_readings (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time     TEXT NOT NULL,
                end_time       TEXT,
                date           TEXT,
                classification TEXT NOT NULL,
                severity       TEXT,
                avg_hr         REAL,
                n_voltage      INTEGER,
                sampling_hz    INTEGER,
                source         TEXT,
                source_file    TEXT,
                raw_meta       TEXT,
                created_at     TEXT DEFAULT (datetime('now')),
                UNIQUE(start_time, source)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ecg_class ON ecg_readings(classification)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ecg_start ON ecg_readings(start_time)")


def _migrate_body_composition():
    """Fitdays состав тела (Фаза 2, 2026-07-12): жир/мышцы/вода/висцеральный/кость/протеин/BMR.

    Источник — умные весы Fitdays (биоимпеданс), тот же поток что вес (import_fitdays).
    Идемпотентно (ALTER guarded). Все REAL. BMI включён по решению владельца (значение прибора,
    выводимость ≠ повод не брать). Body age/Heart rate/Cardiac Index НЕ берём (гиммик / пусто).
    """
    cols = [
        ("bmi",                 "REAL"),   # индекс массы тела (CSV BMI, значение прибора)
        ("body_fat_pct",        "REAL"),   # % жира (CSV Body Fat)
        ("skeletal_muscle_pct", "REAL"),   # % скелетных мышц (CSV Skeletal Muscle)
        ("muscle_mass_kg",      "REAL"),   # кг мышечной массы (CSV Muscle mass)
        ("visceral_fat",        "REAL"),   # индекс висцерального жира (CSV Visceral Fat)
        ("body_water_pct",      "REAL"),   # % воды (CSV Body Water)
        ("bone_mass_kg",        "REAL"),   # кг костной массы (CSV Bone Mass)
        ("protein_pct",         "REAL"),   # % белка (CSV Protein)
        ("bmr_kcal",            "REAL"),   # базовый метаболизм, ккал (CSV BMR)
    ]
    with get_conn() as conn:
        for col, dtype in cols:
            try:
                conn.execute(f"ALTER TABLE daily_metrics ADD COLUMN {col} {dtype}")
            except Exception:
                pass  # silent-ok: колонка уже есть (идемпотентный ALTER ADD COLUMN)


def _migrate_context_cards():
    """Анти-повтор утреннего брифа: событийный лог карточек контекста + recurrence FSM.

    Замысел v3 (project_morning_brief_redesign). Одна строка на (semantic_key, date) —
    полный аудит решения дня: status+gate_reason доказывают, что молчали ПРАВИЛЬНО
    (наблюдаемость обязательна). FSM-переход читает строку с date<today (строго) →
    идемпотентен при catch-up (генерация at-least-once). tenant_id НЕ хранится:
    БД = тенант (HEALTH_DATA_DIR); хранить = дубль идентичности → split-brain.
    trust = derived, не хранится. Аддитивно и идемпотентно (IF NOT EXISTS).
    Зеркало: tests/fixtures/health_schema.sql (сторож test_fixture_schema_covers_prod_tables).
    """
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS context_cards (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                date               TEXT NOT NULL,
                provider           TEXT NOT NULL,
                semantic_key       TEXT NOT NULL,
                lane               TEXT NOT NULL CHECK (lane IN ('safety','routine')),
                origin             TEXT NOT NULL DEFAULT 'internal'
                                   CHECK (origin IN ('internal','personal_external','official','third_party','curated','computed')),
                delivery           TEXT NOT NULL DEFAULT 'computed'
                                   CHECK (delivery IN ('stable_api','cached_freshness','scrape_risk','computed','manual_staleness')),
                relevance          REAL,
                importance         REAL,
                severity           REAL,
                status             TEXT NOT NULL DEFAULT 'candidate'
                                   CHECK (status IN ('candidate','shown','suppressed_cooldown','suppressed_gate','degraded_stale')),
                gate_reason        TEXT,
                evidence_summary   TEXT,
                allowed_claims     TEXT,
                forbidden_claims   TEXT,
                recurrence_state   TEXT NOT NULL DEFAULT 'none'
                                   CHECK (recurrence_state IN ('none','new_alert','still_active','worsened','resolved')),
                last_value         TEXT,
                last_shown_at      TEXT,
                escalation_level   INTEGER NOT NULL DEFAULT 0,
                cooldown_until     TEXT,
                expires_at         TEXT,
                rendered_text_hash TEXT,
                created_at         TEXT DEFAULT (datetime('now')),
                UNIQUE(semantic_key, date)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_context_cards_date ON context_cards(date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_context_cards_key ON context_cards(semantic_key, date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_context_cards_status ON context_cards(status, date)")


def _ensure_device_location_table():
    """Сигнал локации с устройства (iOS Shortcut → /location/ingest). Event-log
    координат + провенанс свежести (received_at) — вход travel-режима брифа и
    belief current_location. tenant_id НЕ хранится: БД = тенант (HEALTH_DATA_DIR).
    Аддитивно и идемпотентно (IF NOT EXISTS)."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS device_location (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                subject     TEXT DEFAULT 'self',
                lat         REAL NOT NULL,
                lon         REAL NOT NULL,
                accuracy_m  REAL,
                name        TEXT,
                is_home     INTEGER,
                source      TEXT DEFAULT 'device_gps',
                received_at TEXT DEFAULT (datetime('now'))
            )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_device_location_recv "
                     "ON device_location(subject, received_at)")


def _create_base_tables():
    """Базовые таблицы (daily_metrics, checkins, …). Вызывается ПЕРВЫМ в init_db: миграции ниже
    делают ALTER TABLE по этим таблицам и глотают ошибку «уже есть», — на свежей БД, где базовых
    таблиц ещё не было, они молча не добавляли ни одной колонки (38 колонок daily_metrics у
    чистой установки не было; найдено пробой чистого клона 2026-09-23)."""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS daily_metrics (
            date TEXT PRIMARY KEY,
            sleep_total REAL,
            sleep_deep  REAL,
            sleep_rem   REAL,
            sleep_score INTEGER,
            hrv         REAL,
            resting_hr  REAL,
            readiness   INTEGER,
            steps       INTEGER,
            active_kcal REAL,
            spo2_avg    REAL,
            weight      REAL,
            vo2max      REAL,
            raw         TEXT
        );

        -- experiments / experiment_log СНЯТЫ (BL-EXP-1, 2026-07-10): конвейер
        -- экспериментов ретайрен, init_db их больше не создаёт (иначе рестарт
        -- воскрешал бы пустые оболочки). Замысел — docs/explanation/hypothesis_engine.md.

        CREATE TABLE IF NOT EXISTS checkins (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT,
            time_of_day TEXT DEFAULT 'morning',
            question   TEXT,
            answer     TEXT,
            context    TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS patterns (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            discovered     TEXT DEFAULT (date('now')),
            category       TEXT,
            description    TEXT,
            evidence       TEXT,
            confidence     REAL DEFAULT 0.5,
            is_active      INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS recommendations (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            date         TEXT,
            text         TEXT,
            category     TEXT,
            followed_up  INTEGER DEFAULT 0,
            outcome      TEXT,
            created_at   TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_metrics_date ON daily_metrics(date);
        CREATE INDEX IF NOT EXISTS idx_checkins_date ON checkins(date);
        CREATE VIRTUAL TABLE IF NOT EXISTS checkins_fts
            USING fts5(question, answer, content='checkins', content_rowid='id');
        """)


def _ensure_orphan_schema():
    """Колонки и таблицы, которые живая БД получила разовыми миграциями или руками, а свежая
    установка — нет (сверка схемы чистого клона с каноном, 2026-09-23). Здесь их единственный
    дом в коде; DDL снят с живой БД. Идемпотентно: ALTER — только если колонки нет."""
    add_cols = [("periods", "deleted_at", "TEXT"), ("genetic_variants", "description_ru", "TEXT"),
                ("alerts", "source", "TEXT"), ("pending_field_reviews", "tg_message_id", "INTEGER")]
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS protocols (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            title                TEXT NOT NULL,
            behavior             TEXT NOT NULL,
            rationale            TEXT,
            frequency            TEXT,
            linked_hypothesis_id INTEGER,
            linked_experiment_id INTEGER,
            reminder_days        TEXT DEFAULT "[]",
            status               TEXT DEFAULT "active",
            created_at           TEXT,
            retired_at           TEXT,
            notes                TEXT,
            review_date          TEXT,
            domain               TEXT);
        CREATE TABLE IF NOT EXISTS assessment_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            instrument_id TEXT NOT NULL,
            wording_version_hash TEXT NOT NULL,
            chat_id INTEGER,
            task_id INTEGER,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT,
            answers_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'in_progress',
            FOREIGN KEY (task_id) REFERENCES tasks(id));
        CREATE INDEX IF NOT EXISTS idx_assess_sess_status ON assessment_sessions(status);
        CREATE INDEX IF NOT EXISTS idx_assess_sess_chat ON assessment_sessions(chat_id);
        """)
        for table, col, typ in add_cols:
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if have and col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def init_db():
    """Создаёт схему если не существует."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _create_base_tables()           # ПЕРВЫМ: миграции ниже ALTER-ят эти таблицы
    _ensure_memory_table()          # 2026-09-26: дашборд и отчёты читают её на пустой установке (dashboard-tidy)
    migrate_v2()
    _migrate_blood_pressure()
    _migrate_bp_readings()
    _migrate_review_dates()
    _migrate_tasks()
    _migrate_consultations_and_resolution()
    _migrate_problem_proposals()
    _migrate_genomics()
    _migrate_promethease()
    _migrate_daily_metrics_v2()
    _ensure_data_repair_log()       # журнал вмешательств в значения (2026-07-31)
    _migrate_patient_profile_and_config()
    _migrate_consultation_sessions()
    _migrate_visual_intake()        # symptom-intake WP0: visual_case/photo/domains (domain-agnostic)
    _migrate_pending_doc_reviews()
    _migrate_imported_docs()
    _migrate_doc_patterns()
    _seed_doc_patterns()
    _cleanup_doc_patterns_duplicates()   # 2026-06-15: удаляем накопленные дубли
    _migrate_hypotheses_cbcr()
    _migrate_medical_record()
    _drop_unused_history_tables()      # 27.09: пустые, без писателя и читателя
    _migrate_medications_treatment()   # 2026-06-18: лечение из документов (modality/cycles/гейт)
    _migrate_absolute_thresholds()  # Sprint 2 / Р-1 step 2
    _migrate_routing_keywords()     # Sprint 2 / Р-1 step 4
    _migrate_checkin_scores()       # Sprint 3 / Р-2
    _seed_data_freshness()  # Sprint 2 / Р-1: seed DATA_FRESHNESS → lab_monitoring_schedule
    _seed_absolute_thresholds()     # Sprint 2 / Р-1 step 2: seed FLOORS/CEILINGS
    _migrate_trend_thresholds()     # BL-ALERT-TREND-1 (2026-07-04): оконные пороги
    _seed_trend_thresholds()
    _migrate_norm_documents()       # norm-from-documents 2026-09-02: реестр документов нормы (CTCAE, EFLM, гайдлайны)
    _seed_lab_trend_thresholds()    # safety-net-thresholds (E1): пороги лаб-трендов (CEA/HGB pct за N измерений)
    _seed_safety_lab_thresholds()   # safety-net-thresholds (E1): абс. лаб-пороги CEA/HGB/... (variant='safety_net')
    _seed_safety_lifestyle_thresholds()  # safety-net-thresholds (E1): lifestyle spo2/readiness/hrv (safety_net/_rel)
    # _seed_location_home удалён 2026-09-23 (pii-scrub): сеял дом из литерала — города владельца.
    # Дом — только данные тенанта (location_signal.home_anchor); у живых тенантов он уже в system_config.
    _scrub_leaky_seed_reasons()     # brief-neutralization Фаза 0: scrub утекающего reason в засеянных БД
    _personalize_absolute_floors()  # brief-neutralization Фаза 3a: личные p10-полы per-tenant (не бэйзлайн владельца)
    _personalize_absolute_ceilings()# brief-neutralization числовая ось: личные p90-потолки awake/stress per-tenant
    _personalize_trend_thresholds() # brief-neutralization Фаза 3a-3: оконные пороги выводятся per-tenant (не литерал)
    _classify_norm_kinds()          # norm-provenance 2026-09-02: вид нормы по провенансу, остаток — человеку
    _seed_routing_keywords()        # Sprint 2 / Р-1 step 4: seed _DOMAIN_KEYWORDS
    _migrate_import_library()       # 2026-06-01: lab_formats + lab_name_aliases
    _seed_lab_formats()             # 2026-06-01: seed Synevo format + aliases
    _migrate_hae_registry()         # 2026-06-01: hae_metric_registry
    _seed_hae_registry()            # 2026-06-01: seed known Apple Health metrics
    _migrate_effect_allele_meta()   # 2026-06-17: genome strand-fix Ф0 — ref_allele/assembly/status
    _migrate_constitutions()        # 2026-06-18: constitutions DB-as-source (Option 3)
    _migrate_pharmaco_tables()      # 2026-06-26: фармакогенетика — genome_imports + pharmaco_phenotypes
    _migrate_workouts_dedup()    # 2026-06-28: dedup workouts + UNIQUE(date,start_time)
    _migrate_lab_staging()       # 2026-06-29: staging перераспознавания анализов (lab-recognizer)
    _migrate_lab_freshness_twotier()  # 2026-07-01: two-tier сроки валидности (stable + doctor)
    _migrate_lab_value_op()          # 2026-07-31: оператор сравнения в каноне
    _migrate_lab_uniq_specimen()      # 2026-07-01: idx_lab_uniq += specimen (промоут blood+urine)
    _migrate_lab_method()             # 2026-07-31: method в каноне + в ключе (COALESCE, шаг 6)
    _migrate_specialized_lab_results()  # 2026-07-04 BL-LAB-CANON-2: спец-панели (микробиом/аутоантитела/метаболомика/иммунофенотип)
    _migrate_lab_domain_verdicts()      # 2026-08-01 работа B: «класс → дом», оракул человек, не литерал в коде
    _migrate_analyte_norm_verdicts()    # 2026-09-03: «нормы нет / отложено до даты» — дом вердикта о норме аналита
    _refresh_lab_refs()                 # norm-from-documents 2026-09-02: референсы = мода бланков, не литерал (после миграций lab_results)
    _migrate_ecg_readings()      # 2026-07-06: ЭКГ Apple Watch через HAE (вердикт+метаданные)
    _migrate_body_composition()  # 2026-07-12 Фаза 2: состав тела Fitdays (жир/мышцы/вода/висц/кость/протеин/BMR)
    _migrate_context_cards()     # 2026-07-13: анти-повтор брифа — event-log карточек контекста + recurrence FSM
    _ensure_device_location_table()  # 2026-07-14: сигнал локации с iOS Shortcut (travel-режим)
    # data-in-code-9: пол безопасности питания (§9 п.4: YAML→таблица food_floor) + склад
    # сгенерированных диет-правил. Ленивый импорт — избегаем цикла health_db↔food_*.
    try:
        import food_floor as _food_floor
        _food_floor.seed_floor()
    except Exception as e:
        logging.getLogger(__name__).warning("food_floor seed failed: %s", e)
    # brief-neutralization Фаза 2: единый clinical_kb (индекс состояний + доменные срезы) →
    # per-tenant таблицы. Аддитивно (Инкремент A): движок брифа ещё НЕ читает эти таблицы,
    # переключение + снятие хардкода — следующий инкремент после доказанного паритета.
    try:
        import clinical_kb as _ckb
        _ckb.seed_clinical_kb()
    except Exception as e:
        logging.getLogger(__name__).warning("clinical_kb seed failed: %s", e)
    _seed_stratified_targets()      # 2026-09-21: цель по типу пациента (врач), ПОСЛЕ clinical_kb — страта читает его
    # diagnosis-hardcode A2-full: единый канон CPIC → 3 проекции (cpic_drug_catalog /
    # cpic_drug_risk / cpic_gene_implication). Общая мед-справка (НЕ PII), одинакова у
    # всех тенантов; конкретный фенотип берётся из pharmaco_phenotypes СМОТРЯЩЕГО тенанта.
    # Аддитивно: seed засеивает таблицы; дашборд + консилиум переключены на них.
    try:
        import cpic_reference_db as _cpic
        _cpic.seed()
    except Exception as e:
        logging.getLogger(__name__).warning("cpic_reference_db seed failed: %s", e)
    try:
        import generated_food_rules as _gfr
        _gfr.ensure_table()
    except Exception as e:
        logging.getLogger(__name__).warning("generated_food_rules ensure_table failed: %s", e)
    _ensure_orphan_schema()         # ПОСЛЕДНИМ: колонки/таблицы, у которых не было дома в коде
    log.info(f"DB инициализирована: {DB_PATH}")


def _migrate_problem_proposals():
    """Создаёт таблицу предложений по problem list."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS problem_list_proposals (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at   TEXT DEFAULT (datetime('now')),
                source       TEXT NOT NULL,   -- gp_weekly|gp_monthly
                proposed     TEXT NOT NULL,   -- JSON: список изменений
                status       TEXT DEFAULT 'pending',  -- pending|approved|rejected
                reviewed_at  TEXT,
                review_note  TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_proposals_status "
            "ON problem_list_proposals(status)"
        )


def question_answer_gate_ddl() -> list[str]:
    """DDL гейта «вопрос не закрывается пустотой» — ОДИН дом текста триггеров.

    Публичная, потому что её читает не только миграция, но и тест: проверять надо
    тот самый текст, который стоит в боевой базе, а не его пересказ в фикстуре
    (§20 — зелёный обязан быть причинён проверяемым механизмом). Скопированный в
    тест DDL разъехался бы с этим при первой же правке и продолжал зеленеть."""
    return [
        f"""
        CREATE TRIGGER IF NOT EXISTS {trg}
        {when}
        FOR EACH ROW
        WHEN NEW.type = 'question'
         AND NEW.status = 'completed'
         AND (NEW.resolved_text IS NULL
              OR TRIM(NEW.resolved_text) = ''
              OR NEW.resolved_text = 'completed via Reminders.app')
        BEGIN
            SELECT RAISE(ABORT,
                'question task cannot be completed without an answer text');
        END
        """
        for trg, when in (
            ("trg_question_answer_required_upd", "BEFORE UPDATE ON tasks"),
            ("trg_question_answer_required_ins", "BEFORE INSERT ON tasks"),
        )
    ]


def _migrate_tasks():
    """Создаёт таблицу tasks если не существует, добавляет новые колонки."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at  TEXT DEFAULT (datetime('now')),
                source      TEXT NOT NULL,
                source_date TEXT,
                type        TEXT NOT NULL,
                priority    TEXT DEFAULT 'medium',
                content     TEXT NOT NULL,
                reason      TEXT,                 -- клиническое обоснование
                fingerprint TEXT,                 -- канонический ключ для дедупликации
                deadline    TEXT,
                status      TEXT DEFAULT 'open',
                resolved_at TEXT,
                resolved_text TEXT,
                sent_at     TEXT,
                followup_sent_at TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_source_date ON tasks(source_date)")
        # Добавляем колонки если таблица уже существовала без них
        # judge_verdict / judge_reason (2026-09-15, BL-JUDGE-VERDICT-UNRECORDED-1).
        # ОТДЕЛЬНО ОТ `reason`, И ЭТО ГЛАВНОЕ В ЭТОЙ ПРАВКЕ. `reason` — поле
        # ПУБЛИКАЦИИ: `task_agent.format_tasks_message` рендерит его человеку в
        # Telegram курсивом под текстом задачи. Замер 15.09: у всех 23 вопросов из
        # памяти там лежит одна и та же константа «вопрос накоплен ассистентом в
        # разговоре», а настоящая причина судьи — которая была в руках в тот же
        # момент — затёрта ею. Положить машинное суждение в `reason` значило бы
        # показывать внутренности судьи человеку под каждым вопросом; слой данных
        # и слой публикации в одной колонке — мина, которая уже стоит взведённой.
        # Поэтому два новых поля, а не переиспользование существующего (§16).
        for col, definition in [
            ("reason",        "TEXT"),
            ("fingerprint",   "TEXT"),
            ("tg_message_id", "INTEGER"),  # квитанция доставки вопроса: к чему прилетит реплай
            ("judge_verdict", "TEXT"),     # patient|unsure|not_patient|unparsed — суждение, НЕ публикация
            ("judge_reason",  "TEXT"),     # своими словами судьи; человеку не показывается
        ]:
            try:
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {definition}")
            except Exception:
                pass  # уже существует
        # Индекс по fingerprint — после того как колонка точно есть
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_fingerprint ON tasks(fingerprint)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_tg_message ON tasks(tg_message_id)"
        )

        # ── Гейт ответа: вопрос не закрывается пустотой ──────────────────────
        # Писателей у задачи ТРИ (бот, дашборд, reminders_sync) — проверка внутри
        # одного из них не стережёт остальных, поэтому переход состояния охраняет
        # сама база (WSTG-BUSL-06: обход шага workflow). Закрытие без текста
        # не доказывает, что человек ответил.
        for stmt in question_answer_gate_ddl():
            conn.execute(stmt)

        # ── Расписание мониторинга лабораторных показателей ──────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lab_monitoring_schedule (
                test_name       TEXT PRIMARY KEY,
                interval_days   INTEGER NOT NULL,
                priority        TEXT NOT NULL DEFAULT 'medium',
                source          TEXT NOT NULL DEFAULT 'default',
                effective_from  TEXT,
                note            TEXT,
                updated_at      TEXT DEFAULT (datetime('now'))
            )
        """)



def _migrate_consultations_and_resolution():
    """Добавляет resolution_type в tasks + таблицу consultations."""
    with get_conn() as conn:
        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN resolution_type TEXT DEFAULT 'self_managed'")
        except Exception:
            pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS consultations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT NOT NULL,
                specialist_type TEXT NOT NULL,
                specialist_name TEXT,
                platform        TEXT,
                key_findings    TEXT,
                source_file     TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_consult_date ON consultations(date)")
        # Mini App pending-consult workflow (main.py API)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS consult_requests (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      TEXT,
                question     TEXT NOT NULL,
                status       TEXT DEFAULT 'pending',
                response     TEXT,
                specialist   TEXT,
                created_at   TEXT DEFAULT (datetime('now')),
                updated_at   TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_consult_req_status ON consult_requests(status)")


# ── Запись метрик ──────────────────────────────────────────────────────────




def migrate_all_json():
    """Переносит все существующие JSON-файлы в SQLite."""
    files = sorted(METRICS_DIR.glob("*.json"))
    log.info(f"Миграция {len(files)} файлов в SQLite...")
    for f in files:
        try:
            data = json.loads(f.read_text())
            upsert_metrics_from_json(f.stem, data)
        except Exception as e:
            log.warning(f"  Ошибка {f.name}: {e}")
    log.info("Миграция завершена")


# ── Чтение метрик ──────────────────────────────────────────────────────────










# ── Чекины ────────────────────────────────────────────────────────────────







# ── Эксперименты ───────────────────────────────────────────────────────────
















# ── Паттерны ──────────────────────────────────────────────────────────────







# ── Компактный контекст для отчёта ────────────────────────────────────────




# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    cmd = sys.argv[1] if len(sys.argv) > 1 else "migrate"

    if cmd == "init":
        init_db()
    elif cmd == "migrate":
        init_db()
        migrate_all_json()
        stats = get_stats(365)
        print(f"\nСтатистика за год: {stats}")
    elif cmd == "stats":
        init_db()
        stats = get_stats(30)
        print(json.dumps(stats, indent=2, ensure_ascii=False))
    elif cmd == "context":
        init_db()
        ctx = build_context(date.today() - timedelta(days=1))
        print(json.dumps(ctx, indent=2, ensure_ascii=False, default=str))


# ── Анализы (lab results) ─────────────────────────────────────────────────

def _ensure_lab_table():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS lab_results (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,
            source      TEXT,
            test_name   TEXT NOT NULL,
            value       REAL,
            value_text  TEXT,
            unit        TEXT,
            ref_low     REAL,
            ref_high    REAL,
            status      TEXT,
            notes       TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_lab_date ON lab_results(date);
        CREATE INDEX IF NOT EXISTS idx_lab_name ON lab_results(test_name);
        CREATE INDEX IF NOT EXISTS idx_lab_date_name ON lab_results(date, test_name);
        """)


def import_biochemical_json(filepath: str) -> int:
    """Импортирует один файл из папки biochemical/. Возвращает кол-во импортированных строк.

    ФЕНС (BL-LAB-CANON-1, 2026-07-04): канон lab_results принадлежит vision-пайплайну
    (lab_backfill→lab_promote, source=doc:) как ЕДИНСТВЕННОМУ писателю (Primary-Based
    Protocol, Таненбаум §7.5). Старый biochemical-JSON writer систематически терял
    десятичную точку (условно: RDW 15.5 → 155) и ВОСКРЕШАЛ мусор при каждом прогоне
    (save() плодил дубли _2/_594 → каждый импортировался как отдельный source
    *_chemistry.json → write-write конфликт). ЖЁСТКИЙ БЛОК без escape-hatch (design-
    принцип #1: гибкость через полгода = "всегда обхожу"; а env-хатч — вектор
    воскрешения, ровно то, от чего фенс защищает). Легитимный ре-импорт — только
    через lab_backfill→lab_promote. См. BACKLOG BL-LAB-CANON-1."""
    raise RuntimeError(
        f"import_biochemical_json РЕТАЙРНУТ (фенс BL-LAB-CANON-1): {Path(filepath).name} "
        "НЕ записан. Канон lab_results принадлежит единственному писателю lab_promote "
        "(source=doc:). Старый biochemical-JSON путь выведен из эксплуатации — терял "
        "точность и воскрешал мусор. Ре-импорт лаб-данных — только через lab_backfill."
    )
    date_str = data.get("date", "unknown")
    source   = Path(filepath).name
    results  = data.get("results") or {}

    count = 0
    with get_conn() as conn:
        # Удаляем старые записи из этого файла чтобы не дублировать
        conn.execute("DELETE FROM lab_results WHERE source=?", (source,))
        for test_name, item in results.items():
            val = item.get("value")
            if val is None:
                continue
            try:
                val_float = float(val)
            except (TypeError, ValueError):
                val_float = None
            conn.execute("""
                INSERT INTO lab_results
                    (date, source, test_name, value, value_text, unit, ref_low, ref_high, status)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                date_str, source, test_name,
                val_float,
                str(val) if val_float is None else None,
                item.get("unit"),
                item.get("ref_low"),
                item.get("ref_high"),
                "flagged" if item.get("flagged") else "normal",
            ))
            count += 1
    return count


def import_all_biochemical():
    """Импортирует все файлы из папки biochemical/."""
    bio_dir = _HEALTH_DIR / "data" / "biochemical"
    if not bio_dir.exists():
        log.debug(f"biochemical dir not found, skipping: {bio_dir}")
        return 0
    _ensure_lab_table()
    total = 0
    errors = 0
    for month_dir in sorted(bio_dir.iterdir()):
        if not month_dir.is_dir():
            continue
        for f in sorted(month_dir.glob("*.json")):
            try:
                n = import_biochemical_json(str(f))
                total += n
            except Exception as e:
                log.warning(f"lab import {f.name}: {e}")
                errors += 1
    log.info(f"Лабораторные данные: {total} записей импортировано, {errors} ошибок")
    return total















# ── Memory / артефакты из разговоров ──────────────────────────────────────

def _ensure_data_repair_log():
    """Журнал вмешательств в ЗНАЧЕНИЯ данных (2026-07-31, нить validation-gate-repair).

    Зачем. Репарация без следа необратима, а необратимое по §13 требует человека на
    каждый случай. Журнал хранит достаточно, чтобы вернуть прежнее состояние, поэтому
    та же операция становится ступенью 1 (авто-ремонт с логом), а не ступенью 3.
    Побочно это единственное место, где видна история правок данных: до него такого не было
    (искал по данным — из 90 таблиц только `genome_update_log` и `vcf_import_log`,
    оба про ФАКТ импорта, а не про изменение значения).

    `home` обязателен, потому что у `daily_metrics` ДВА дома — таблица и
    `~/health/data/daily_metrics/*.json`. Откат без указания дома неполон, а неполный
    откат — это обещание обратимости без обратимости.

    Значения хранятся как JSON-скаляры (`json.dumps`), а не как сырой текст: иначе
    «было 0.0» и «поля не было» неразличимы, а именно это различие вся нить и защищает.
    """
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS data_repair_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT NOT NULL CHECK(run_id <> ''),
            repaired_at TEXT NOT NULL DEFAULT (datetime('now')),
            home        TEXT NOT NULL CHECK(home <> ''),
            entity      TEXT NOT NULL CHECK(entity <> ''),
            field       TEXT NOT NULL CHECK(field <> ''),
            old_value   TEXT NOT NULL,
            new_value   TEXT NOT NULL,
            reason      TEXT NOT NULL CHECK(reason <> ''),
            reverted_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_repair_run ON data_repair_log(run_id);
        CREATE INDEX IF NOT EXISTS idx_repair_entity ON data_repair_log(home, entity);
        """)


def log_repair(conn, *, run_id, home, entity, field, old_value, new_value, reason):
    """Записывает ОДНО изменение значения в журнал. Вызывать в той же транзакции,
    что и само изменение, иначе журнал и данные разъедутся при отказе посередине.

    old_value/new_value — любые JSON-сериализуемые скаляры, включая None.
    """
    if old_value == new_value:
        raise ValueError(
            f"log_repair: старое и новое значения совпадают ({old_value!r}) — "
            f"это не репарация, а шум в журнале ({home}/{entity}/{field})"
        )
    conn.execute(
        "INSERT INTO data_repair_log(run_id, home, entity, field, old_value, new_value, reason) "
        "VALUES (?,?,?,?,?,?,?)",
        (run_id, home, entity, field,
         json.dumps(old_value), json.dumps(new_value), reason),
    )


# Таблицы, чьи строки чинятся через журнал: таблица → ключ строки (entity в журнале).
# Ремонт фактов о лечении 04.10.2026 (нить treatment-homes) — канон владельца, §13: откат обязан
# быть исполнимым, а не заявленным.
# memory и tasks — с 05.10.2026 (нить test-thread-leak): ремонт следов потока теста в живой базе.
_ROW_REPAIR_HOMES = {"medications": "id", "patient_profile": "key", "problem_list": "problem_id",
                     "memory": "id", "tasks": "id"}


def revert_repairs(run_id: str) -> int:
    """Возвращает прежние значения по журналу прогона. Возвращает число откаченных записей.

    Обратимость обязана быть ИСПОЛНИМОЙ, а не заявленной: без этой функции запись в журнал
    была бы обещанием отката, а не откатом. Уже откаченные записи (`reverted_at`) пропускаются,
    поэтому повторный вызов безопасен.

    Поддерживаемые дома: `sqlite:daily_metrics` и `json:daily_metrics`. Незнакомый дом —
    отказ, а не пропуск: молча не откатить хуже, чем упасть.
    """
    _KNOWN_HOMES = ("sqlite:daily_metrics", "json:daily_metrics", "sqlite:daily_metrics.raw",
                    *(f"sqlite:{t}" for t in _ROW_REPAIR_HOMES))
    done = 0
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, home, entity, field, old_value FROM data_repair_log "
            "WHERE run_id=? AND reverted_at IS NULL ORDER BY id", (run_id,)
        ).fetchall()
        unknown = {r[1] for r in rows} - set(_KNOWN_HOMES)
        if unknown:
            raise ValueError(f"revert_repairs: неизвестные дома {sorted(unknown)}, откат не полон")

        # `field` идёт в SQL идентификатором, поэтому сверяется с ЖИВОЙ схемой, а не с
        # литеральным списком: список разъехался бы со схемой молча. Журнал пишем мы сами,
        # но это граница доверия между двумя запусками — на ней не экономим.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_metrics)")}
        bad = {r[3] for r in rows if r[1] == "sqlite:daily_metrics"} - cols
        for table in _ROW_REPAIR_HOMES:
            tcols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            bad |= {r[3] for r in rows if r[1] == f"sqlite:{table}"} - tcols
        if bad:
            raise ValueError(f"revert_repairs: journal fields outside table schema: {sorted(bad)}")

        for rid, home, entity, field, old_json in rows:
            old = json.loads(old_json)
            if home == "sqlite:daily_metrics":
                conn.execute(
                    f"UPDATE daily_metrics SET {field} = ? WHERE date = ?", (old, entity)
                )
            elif home.startswith("sqlite:") and home[7:] in _ROW_REPAIR_HOMES:
                table = home[7:]
                conn.execute(f"UPDATE {table} SET {field} = ? WHERE {_ROW_REPAIR_HOMES[table]} = ?",
                             (old, entity))
            elif home == "sqlite:daily_metrics.raw":
                row = conn.execute(
                    "SELECT raw FROM daily_metrics WHERE date = ?", (entity,)).fetchone()
                if not row or not row[0]:
                    raise ValueError(
                        f"revert_repairs: нет raw для {entity}, откат не полон")
                doc = json.loads(row[0])
                doc.setdefault("sleep", {})[field] = old
                conn.execute("UPDATE daily_metrics SET raw = ? WHERE date = ?",
                             (json.dumps(doc, ensure_ascii=False), entity))
            else:
                path = METRICS_DIR / f"{entity}.json"
                if not path.exists():
                    raise FileNotFoundError(f"revert_repairs: нет файла {path} для отката")
                doc = json.loads(path.read_text())
                doc.setdefault("sleep", {})[field] = old
                path.write_text(json.dumps(doc, ensure_ascii=False, indent=2))
            conn.execute(
                "UPDATE data_repair_log SET reverted_at = datetime('now') WHERE id = ?", (rid,)
            )
            done += 1
    return done


def _ensure_memory_table():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS memory (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT DEFAULT (date('now')),
            category   TEXT NOT NULL,
            key        TEXT,
            value      TEXT NOT NULL,
            confidence REAL DEFAULT 0.8,
            source     TEXT DEFAULT 'conversation',
            active     INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_memory_category ON memory(category);
        CREATE INDEX IF NOT EXISTS idx_memory_date ON memory(date);
        CREATE INDEX IF NOT EXISTS idx_memory_active ON memory(active);
        """)


def _ensure_memory_facts_table():
    """Типизированная разговорная память (Фаза 1, 2026-07-04). ОТДЕЛЬНАЯ таблица —
    не трогает общую `memory` с подсистемой гипотез. mem_class = fact|state|
    preference|question|recommendation|experiment. Би-темпоральность: valid_to
    IS NULL = актуально; supersede вместо delete. subject=third_party (R11) — факт
    про другого человека, исключён из профиля/консилиума. critical_flag — не тухнет."""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS memory_facts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            mem_class     TEXT NOT NULL,
            key           TEXT,
            value         TEXT NOT NULL,
            confidence    REAL DEFAULT 0.8,
            valid_from    TEXT DEFAULT (date('now')),
            valid_to      TEXT,
            superseded_by INTEGER,
            source        TEXT DEFAULT 'conversation',
            subject       TEXT DEFAULT 'self',
            confirmations INTEGER DEFAULT 0,
            critical_flag INTEGER DEFAULT 0,
            temporal_class TEXT,
            active        INTEGER DEFAULT 1,
            created_at    TEXT DEFAULT (datetime('now')),
            updated_at    TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_mf_class   ON memory_facts(mem_class);
        CREATE INDEX IF NOT EXISTS idx_mf_key     ON memory_facts(key);
        CREATE INDEX IF NOT EXISTS idx_mf_active  ON memory_facts(active);
        CREATE INDEX IF NOT EXISTS idx_mf_valid   ON memory_facts(valid_to);
        CREATE INDEX IF NOT EXISTS idx_mf_subject ON memory_facts(subject);
        """)
        # receipt_shown_at (2026-08-04, нить brief-repeat): квитанция «записал так,
        # поправь» показывается РОВНО ОДИН РАЗ (решение владельца). Носитель отметки —
        # сама строка факта: у неё есть гаситель (supersede), в отличие от TTL-окна.
        # НЕ переиспользуем `confirmations` — там живёт другой смысл (объективное
        # подтверждение каноном, memory_truthcheck.apply_confirmations + beliefs._is_confirmed);
        # два смысла в одном поле — это §16, за который уже платили. Идемпотентно:
        # CREATE TABLE IF NOT EXISTS колонку в существующую таблицу не добавляет.
        _mf_cols = {r[1] for r in conn.execute("PRAGMA table_info(memory_facts)")}
        if "receipt_shown_at" not in _mf_cols:
            conn.execute("ALTER TABLE memory_facts ADD COLUMN receipt_shown_at TEXT")
        # retire_reason (2026-09-15, долг BL-JUDGE-VERDICT-UNRECORDED-1, решение
        # владельца — вариант Б). ПОЧЕМУ ЭТО НЕ НОВЫЙ ДОМ, А ОСТАНОВКА ПОТЕРИ:
        # `retire_fact(id, reason)` УЖЕ принимает причину — и отправляет её в лог,
        # который ротируется. То есть данные существовали в руках у вызывающего и
        # выбрасывались; заводится не смысл, а место для уже существующего смысла.
        #
        # Замер 15.09, ради которого: из 60 кандидатов в вопросы 37 сняты судьёй,
        # и ни у одного нельзя узнать, ПОЧЕМУ. Следствие тихое: «судья стал строже»
        # и «разговоров просто не было» снаружи неразличимы — §18 в чистом виде,
        # утверждение «канал работает» без гасителя.
        #
        # Колонка НАРОЧНО общая, а не «вердикт судьи»: `retire_fact` зовут три
        # разных места (судья, дедуп уже открытого вопроса, разовые планы), и
        # сужать поле до одного из них значило бы завести второй дом для тех же
        # данных при первом же следующем вызывающем.
        if "retire_reason" not in _mf_cols:
            conn.execute("ALTER TABLE memory_facts ADD COLUMN retire_reason TEXT")















# ══════════════════════════════════════════════════════════════════════════
# НОВАЯ АРХИТЕКТУРА ДАННЫХ v2
# ══════════════════════════════════════════════════════════════════════════

def migrate_v2():
    """
    Создаёт новые таблицы архитектуры v2.
    Безопасно — не трогает существующие таблицы.
    """
    with get_conn() as conn:
        conn.executescript("""

        -- ── Именованные периоды ──────────────────────────────────────────
        -- Поездки, курсы лечения, эксперименты, стрессовые периоды.
        -- Ось для тематических срезов: JOIN с любой таблицей по дате.
        CREATE TABLE IF NOT EXISTS periods (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            type        TEXT NOT NULL,       -- travel|treatment|experiment|stress|vacation|illness|other
            start_date  TEXT NOT NULL,       -- YYYY-MM-DD
            end_date    TEXT,                -- NULL = ongoing
            tags        TEXT DEFAULT '[]',   -- JSON array
            source      TEXT DEFAULT 'manual', -- manual|calendar|auto
            notes       TEXT,
            active      INTEGER DEFAULT 1,
            created_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_periods_dates ON periods(start_date, end_date);
        CREATE INDEX IF NOT EXISTS idx_periods_type  ON periods(type);

        -- ── Жизненный контекст ────────────────────────────────────────────
        -- Всё что не измерено датчиком: настроение, стресс, питание,
        -- погода, плотность встреч, локация, события.
        -- Источники: чекин, Gmail, Calendar, погода, разговоры.
        CREATE TABLE IF NOT EXISTS context_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,       -- YYYY-MM-DD
            ts          TEXT,                -- точное время если есть
            source      TEXT NOT NULL,       -- checkin|gmail|calendar|weather|conversation|manual
            category    TEXT NOT NULL,       -- mood|energy|stress|symptom|nutrition|social|work|weather|travel|note
            key         TEXT,                -- mood_score|energy_level|stress_level|meeting_count|temp_c|meal_quality
            value_num   REAL,               -- числовое значение
            value_text  TEXT,               -- текстовое значение
            period_id   INTEGER REFERENCES periods(id),
            tags        TEXT DEFAULT '[]',   -- JSON array
            created_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_ctx_date     ON context_events(date);
        CREATE INDEX IF NOT EXISTS idx_ctx_category ON context_events(category);
        CREATE INDEX IF NOT EXISTS idx_ctx_key      ON context_events(key);
        CREATE INDEX IF NOT EXISTS idx_ctx_source   ON context_events(source);

        -- ── Отчёты агентов ────────────────────────────────────────────────
        -- Хранит все выводы лайфстайл-агентов и врачей.
        -- Накапливается для ретроспективы.
        -- Структура output: JSON с обязательными полями контракта.
        CREATE TABLE IF NOT EXISTS agent_reports (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT NOT NULL,      -- дата отчёта YYYY-MM-DD
            agent_type      TEXT NOT NULL,      -- lifestyle|specialist|therapist|synthesizer
            agent_name      TEXT NOT NULL,      -- sleep|stress|oncology|cardiology|therapist...
            period_days     INTEGER DEFAULT 7,  -- за какой период
            has_findings    INTEGER DEFAULT 0,  -- 1 = есть изменения, идёт в отчёт
            -- Обязательные поля контракта (proof of work)
            data_queried    TEXT DEFAULT '[]',  -- JSON: список запросов к БД
            pubmed_ids      TEXT DEFAULT '[]',  -- JSON: реальные PMID если были
            peers_reviewed  TEXT DEFAULT '[]',  -- JSON: какие агенты учтены
            changes_summary TEXT,               -- что изменилось vs прошлый отчёт
            -- Полный вывод
            findings        TEXT,               -- нарратив по домену
            recommendations TEXT,               -- рекомендации
            data_requests   TEXT,               -- какие данные нужны (анализы, измерения)
            raw_output      TEXT,               -- полный JSON output агента
            created_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_reports_date  ON agent_reports(date);
        CREATE INDEX IF NOT EXISTS idx_reports_agent ON agent_reports(agent_name);
        CREATE INDEX IF NOT EXISTS idx_reports_type  ON agent_reports(agent_type, date);

        -- ── Журнал необоснованных корреляций (UC-B-09, 2026-08-08) ────────
        -- Датчик check_correlations_grounded ловит коэффициенты из отчётов,
        -- которым нет опоры в ПРИНЯТОЙ вере. Скользящее окно без журнала теряет
        -- находку по возрасту отчёта, даже если её причина не исправлена.
        --
        -- Журнал различает повтор старого неподтверждённого значения и новое
        -- значение. Счётчик показывает лишь рост количества, скрывая источник:
        -- цитату, устаревшую веру или повтор собственного прошлого отчёта.
        -- Для разбора причины нужны история и связь с исходным отчётом.
        --
        -- `is_baseline` — засечка обратного заполнения. Ратчет считает находкой
        -- значение, которого нет среди baseline-строк; второго дома у порога нет,
        -- он выводится из самой таблицы (§9: не константа в коде).
        CREATE TABLE IF NOT EXISTS ungrounded_correlations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date     TEXT NOT NULL,      -- дата отчёта, где напечатано число
            agent_type      TEXT NOT NULL,      -- кто напечатал
            value           REAL NOT NULL,      -- |r|, округлён до 2 — как считает датчик
            belief_n        INTEGER,            -- сколько значений было в вере в тот момент
            belief_accepted INTEGER,            -- принята ли вера (0/1)
            is_baseline     INTEGER DEFAULT 0,  -- 1 = строка обратного заполнения 08.08
            logged_at       TEXT NOT NULL,      -- когда запись сделана
            UNIQUE(report_date, agent_type, value)
        );

        -- ── Решения врача по наблюдению (нить treatment-facts) ─────
        -- Решение отложить исследование должно возвращаться во входной контекст,
        -- иначе агент может предлагать его снова, не учитывая действующий план.
        -- Дом: тема → решение → кто → когда → до какого срока → провенанс.
        -- НЕ protocols (поведенческие протоколы), НЕ parked_decisions (ops),
        -- НЕ problem_list (проблема ≠ план наблюдения). Читатели gp_context и
        -- literature_curator учитывают активное решение до повторного предложения
        -- (doctor_in_loop: ответ врача возвращается ВХОДОМ).
        CREATE TABLE IF NOT EXISTS surveillance_decisions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            topic        TEXT NOT NULL,       -- slug темы: cardio_imaging, pet, colonoscopy …
            title        TEXT NOT NULL,       -- человекочитаемо
            decision     TEXT NOT NULL CHECK(decision IN ('defer','do','stop')),
            decided_by   TEXT NOT NULL,       -- онколог / гастроэнтеролог / владелец
            decided_on   TEXT NOT NULL,       -- YYYY-MM-DD — дата решения (или записи, если не названа)
            valid_until  TEXT,                -- решение действует до (NULL = бессрочно)
            rationale    TEXT,
            source       TEXT NOT NULL,       -- провенанс: owner_word:<дата> | doc:<файл> | encounter:<id>
            created_at   TEXT DEFAULT (datetime('now')),
            retired_at   TEXT
        );

        -- ── Problem list (терапевт) ───────────────────────────────────────
        -- Живой список проблем. Единственный постоянный артефакт системы.
        -- Обновляется терапевтом еженедельно.
        CREATE TABLE IF NOT EXISTS problem_list (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            problem_id      TEXT UNIQUE NOT NULL,  -- стабильный ID: 'deep_sleep_decline'
            title           TEXT NOT NULL,
            description     TEXT,
            status          TEXT NOT NULL DEFAULT 'active',  -- active|monitoring|resolved
            priority        INTEGER DEFAULT 2,     -- 1=высокий, 2=средний, 3=низкий
            domain          TEXT,                  -- sleep|hrv|oncology|nutrition|stress...
            first_seen      TEXT NOT NULL,         -- когда впервые появилась
            last_updated    TEXT NOT NULL,         -- когда последний раз менялась
            -- Watchful waiting
            watch_trigger   TEXT,   -- условие эскалации: 'если deep < 20м три ночи подряд'
            watch_deadline  TEXT,   -- дата пересмотра
            -- Срок пересмотра (TESTING_CONTRACTS.md §1)
            -- SCHEMA DEBT: поле ещё не добавлено, нужна миграция
            -- review_date TEXT,   -- YYYY-MM-DD, обязательно при создании
            --                     -- тревога если прошла и статус не изменён
            -- Связи
            supporting_data TEXT DEFAULT '[]',  -- JSON: event IDs, report IDs
            notes           TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_problems_status   ON problem_list(status);
        CREATE INDEX IF NOT EXISTS idx_problems_priority ON problem_list(priority);
        CREATE INDEX IF NOT EXISTS idx_problems_domain   ON problem_list(domain);

        """)
    return True


# ── Вспомогательные функции для новых таблиц ─────────────────────────────






















# ── Periods helpers (D++ hybrid 2026-06-19, PERIODS-SEMANTICS) ───────────────
#
# Семантика columns:
# - end_date IS NULL  → ongoing phase (без планируемого end)
# - end_date >= today → current phase (active on date)
# - end_date < today  → past phase (historical record)
# - deleted_at IS NULL    → valid record
# - deleted_at IS NOT NULL → soft-deleted (superseded/invalid)
#
# Колонка `active` deprecated с 2026-06-19, оставлена для backward-compat.
# Новый код использует deleted_at semantics; старые callers с `WHERE active=1`
# по-прежнему работают (active=1 ⇔ deleted_at IS NULL по invariant).
#
# Helpers ниже — единая точка входа для всех queries по periods.




















# ── Task management ───────────────────────────────────────────────────────────







# ── Лечение (онкорежимы) в medications ───────────────────────────────────────
# 2026-06-18: лечение — производное из документов, не ручная строка профиля.
# medications расширяется полями режима/циклов; заполняется treatment_extractor
# при импорте; читается treatment_summary детерминированно. Человек-гейт:
# extractor пишет confirmation='proposed' — каноничным становится после подтверждения.

_MEDICATIONS_TREATMENT_COLS = [
    ("modality",         "TEXT"),     # chemo|chemoradiation|immunotherapy|targeted|radiation|other
    ("intent",           "TEXT"),     # neoadjuvant|adjuvant|palliative|maintenance|definitive
    ("cycles_completed", "INTEGER"),
    ("cycles_planned",   "INTEGER"),
    ("agents",           "TEXT"),     # JSON-массив действующих веществ
    ("source",           "TEXT"),     # 'manual' | 'extractor:<event_id>'
    ("confirmation",     "TEXT"),     # proposed|confirmed|rejected (человек-гейт)
    ("updated_at",       "TEXT"),
    ("gate_sent_at",     "TEXT"),     # когда отправлена карточка подтверждения
]


def _migrate_medications_treatment():
    """Аддитивно расширяет medications полями онкорежима/циклов/гейта.
    Идемпотентно: добавляет только отсутствующие колонки."""
    with get_conn() as conn:
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(medications)")}
        if not existing:
            return  # таблицы нет (создаётся в _migrate_medical_record раньше) — пропускаем
        for col, typedef in _MEDICATIONS_TREATMENT_COLS:
            if col not in existing:
                conn.execute(f"ALTER TABLE medications ADD COLUMN {col} {typedef}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_medications_confirmation "
            "ON medications(confirmation)"
        )
        _ensure_medication_status_trigger(conn)


def _ensure_medication_status_trigger(conn) -> None:
    """Нативный страж словаря статуса (нить treatment-homes, 04.10.2026).

    Словарь живёт в treatment_db.MED_STATUSES; триггер пересобирается из него на каждом
    init_db, поэтому второго дома у словаря нет. CHECK потребовал бы пересборки таблицы.
    Писатель мимо treatment_db.upsert_medication (прямой SQL, ручная правка) тоже упрётся."""
    from treatment_db import MED_STATUSES
    allowed = ",".join(f"'{s}'" for s in MED_STATUSES)
    for event in ("INSERT", "UPDATE OF status"):
        name = "medications_status_vocab_" + event.split()[0].lower()
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        conn.execute(
            f"CREATE TRIGGER {name} BEFORE {event} ON medications "
            f"WHEN NEW.status NOT IN ({allowed}) "
            f"BEGIN SELECT RAISE(ABORT, 'medications.status outside vocabulary'); END")


# Синонимы схем лечения ТЕНАНТА — данные (methodology/regimens.yaml, приватная зона: набор схем
# выдаёт диагноз, решение владельца 2026-09-23). Нет файла — имена идут как есть.
_REGIMENS_PATH = Path(__file__).resolve().parent / "methodology" / "regimens.yaml"


def _load_regimen_synonyms() -> dict:
    if not _REGIMENS_PATH.exists():
        return {}
    import yaml
    doc = yaml.safe_load(_REGIMENS_PATH.read_text(encoding="utf-8")) or {}
    return {str(k).lower(): str(v) for k, v in (doc.get("synonyms") or {}).items()}


_REGIMEN_SYNONYMS = _load_regimen_synonyms()


def _normalize_regimen(name: str) -> str:
    """Канонизирует имя режима по синонимам тенанта (methodology/regimens.yaml)."""
    if not name:
        return ""
    n = name.strip().lower()
    for k, v in _REGIMEN_SYNONYMS.items():
        if k in n:
            return v
    return name.strip()


def _src_rank_med(s) -> int:
    return 2 if str(s or "").startswith("manual") else 0



































# ── Problem List Proposals ────────────────────────────────────────────────────













# ── Genomics ──────────────────────────────────────────────────────────────────

def _migrate_promethease():
    """Таблица для Promethease SNP-отчёта."""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS promethease_variants (
            rsnum        TEXT PRIMARY KEY,
            geno         TEXT,
            magnitude    REAL,
            repute       TEXT,
            genes        TEXT,
            genosummary  TEXT,
            topic        TEXT,
            numrefs      INTEGER,
            clinvar_diseases TEXT,
            domain_tags  TEXT,
            imported_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_prom_magnitude ON promethease_variants(magnitude DESC);
        CREATE INDEX IF NOT EXISTS idx_prom_repute    ON promethease_variants(repute);
        """)


def _migrate_genomics():
    """Создаёт таблицы для геномных данных."""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS raw_snps (
            rsid        TEXT PRIMARY KEY,
            chromosome  TEXT,
            position    INTEGER,
            genotype    TEXT
        );
        CREATE TABLE IF NOT EXISTS genetic_variants (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            rsid            TEXT UNIQUE NOT NULL,
            gene            TEXT,
            genotype        TEXT,
            significance    TEXT,
            prev_significance TEXT,
            conditions      TEXT,
            domain_tags     TEXT,
            effect_allele   TEXT,
            clinical_summary TEXT,
            annotation_source TEXT DEFAULT 'myvariant',
            annotated_at    TEXT,
            updated_at      TEXT
        );
        CREATE TABLE IF NOT EXISTS genome_update_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date        TEXT NOT NULL,
            variants_checked INTEGER DEFAULT 0,
            variants_changed INTEGER DEFAULT 0,
            changes_json    TEXT,
            narrative       TEXT,
            sent_to_user    INTEGER DEFAULT 0
        );
        """)














# Ранг патогенности для ORDER BY (genome strand-fix Ф5). LIKE-основан, чтобы
# ловить «Pathogenic; drug response», «Pathogenic/Likely pathogenic» и т.п.
_PATHO_RANK_SQL = """
  CASE
    WHEN significance LIKE '%athogenic%' AND significance NOT LIKE '%Likely%'
         AND significance NOT LIKE '%Conflict%' THEN 1
    WHEN significance LIKE '%Likely pathogenic%' THEN 2
    WHEN significance LIKE '%Conflict%' THEN 3
    WHEN significance LIKE '%Uncertain%' THEN 4
    ELSE 5
  END
"""

















# ── Протоколы ────────────────────────────────────────────────────────────────












# ── Консультации ─────────────────────────────────────────────────────────────










# -- Patient Constraints (2026-04-20) -------------------------------------------





























# ── Schema: review_date (TESTING_CONTRACTS.md §1) ───────────────────────────
# review_date добавлен 2026-04-24.
# Тревога: review_date прошла и статус не изменён (active/monitoring).
# GP-агент предлагает review_in_days (длительность), apply_proposal() → абсолютная дата.


def _migrate_review_dates():
    """Добавляет review_date в problem_list и protocols, domain в protocols."""
    with get_conn() as conn:
        for table, col, dtype in [
            ("problem_list", "review_date", "TEXT"),
            ("protocols",    "review_date", "TEXT"),
            ("protocols",    "domain",      "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {dtype}")
            except Exception:
                pass  # уже существует

# ── Import status (Conit-контракты §7.2.1) ───────────────────────────────────
#
# Conit-границы для health данных (предупреждение, не отказ):
#   C1-биометрика (Oura)  : staleness ≤ 26ч
#   C2-кардио (Withings)  : staleness ≤ 6ч
#   C3-онко (PDF импорт)  : staleness ≤ 12ч — явное предупреждение в ответе
#   C4-память/протоколы   : без проверки (всегда Studio-primary)
#
CONIT_LIMITS_HOURS = {
    "oura":         26.0,
    "apple_health": 30.0,   # суточная выгрузка ~24-28ч: 26ч флапал на нормальной задержке; 30ч даёт зазор, пропущенный день (>30ч) всё равно ловится (2026-06-19)
    "oncology_pdf": 12.0,
    "withings":      6.0,
}

# Референсные диапазоны лабораторных тестов (канонические, для мужчины).
# Формат: test_name → (min, max, unit).
# lab_refs — КЭШ моды референсов бланков (labs_db.compute_bank_refs), пересчитывается на init_db
# (_refresh_lab_refs). Прежний литерал LAB_REFS_CANONICAL «для мужчины» без источника удалён
# 2026-09-02 (нить norm-from-documents): число без документа хуже пустоты.





  # при ошибке парсинга — не блокировать





# ─────────────────────────────────────────────────────────────────────────────
# PATIENT PROFILE — единый conit профиля пациента (replaces profile_context.json)
# ─────────────────────────────────────────────────────────────────────────────

def _migrate_patient_profile_and_config():
    """Создаёт таблицы patient_profile и system_config если не существуют."""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS patient_profile (
            key        TEXT PRIMARY KEY,
            value_text TEXT,
            value_json TEXT,
            category   TEXT,
            updated_at TEXT DEFAULT (datetime('now')),
            updated_by TEXT DEFAULT 'manual'
        );

        CREATE TABLE IF NOT EXISTS system_config (
            key        TEXT PRIMARY KEY,
            value_text TEXT,
            value_num  REAL,
            value_json TEXT,
            category   TEXT,
            updated_at TEXT DEFAULT (datetime('now')),
            source     TEXT DEFAULT 'manual'
        );
        """)
    with get_conn() as conn:
        # Раздел строки профиля — префикс ключа. Если category потеряна,
        # её можно восстановить из ключа, не меняя содержимое записи.
        conn.execute("UPDATE patient_profile SET category = substr(key, 1, instr(key, '.') - 1) "
                     "WHERE category IS NULL AND instr(key, '.') > 1")
        # Удаляется пустая строка устаревшего ключа профиля.
        # SQL-ключ сохранён: изменение миграции требует отдельного решения.
        conn.execute("DELETE FROM patient_profile WHERE key = 'routine.nurosym' "
                     "AND value_text IS NULL AND value_json IS NULL")
    with get_conn() as conn:
        # Порог «два числа — одно измерение» (§9: методическая константа живёт в
        # данных, оракул — инженер). Ручка не косметическая: она решает, сколько
        # измерений вообще доезжает до канона. Значение 0.01 — снимок литерала,
        # который стоял в lab_promote; сид его переносит, а не переоценивает.
        # Зеркало в коде — lab_promote._SPREAD_FALLBACK, держится равным
        # coherence-тестом (резерв, а не альтернативная норма).
        conn.execute(
            """INSERT OR IGNORE INTO system_config (key, value_num, category, source)
               VALUES ('lab.conflict_spread', 0.01, 'lab', 'code_seed')""")
        # Пороги датчиков тишины входа (§9: конфиг анализа — данные, оракул инженер).
        # Зеркала в коде — integrity_tests._INTAKE_FALLBACK, равенство держит
        # coherence-тест. Пульс: вотчер опрашивает раз в 60с, 10 минут = 10 промахов
        # подряд, случайная заминка iCloud столько не длится.
        conn.execute(
            """INSERT OR IGNORE INTO system_config (key, value_num, category, source)
               VALUES ('lab.intake_pulse_max_min', 10, 'lab', 'code_seed')""")
        # Отсрочка до вопроса «видит и не берёт»: распознавание одного бланка идёт
        # до двух минут, плюс дебаунс дозаписи. Час — с запасом на очередь.
        conn.execute(
            """INSERT OR IGNORE INTO system_config (key, value_num, category, source)
               VALUES ('lab.intake_grace_hours', 1, 'lab', 'code_seed')""")
        # Час будильника задачи в CalDAV-напоминаниях (reminders_backend, docker-install этап 3):
        # предпочтение человека, не механика — данные (§9). 9 — начало дня после утреннего брифа.
        conn.execute(
            """INSERT OR IGNORE INTO system_config (key, value_num, category, source)
               VALUES ('reminders.alarm_hour', 9, 'reminders', 'code_seed')""")
        # Очередь человека: 7 дней без движения — это уже не «занят», это забыто.
        conn.execute(
            """INSERT OR IGNORE INTO system_config (key, value_num, category, source)
               VALUES ('lab.review_stale_days', 7, 'lab', 'code_seed')""")
        # Провенанс нормы (нить norm-provenance 2026-09-02; §9: конфиг анализа — данные,
        # оракул инженер; числа — решение владельца 02.09: X=25%, N=5). Зеркало в коде —
        # integrity_tests._NORM_FALLBACK, равенство держит coherence-тест.
        # witness_max_dev: норма вида reference_interval расходится с модальным интервалом
        # лаборатории больше чем на эту долю → красное. witness_min_docs: сколько РАЗНЫХ
        # документов должны напечатать интервал, чтобы он считался свидетелем (один бланк
        # — не источник, §17). coverage_min_n: аналит мерялся ≥N раз и нормы нет → слепое пятно.
        conn.execute(
            """INSERT OR IGNORE INTO system_config (key, value_num, category, source)
               VALUES ('norm.witness_max_dev', 0.25, 'lab', 'owner_2026-09-02')""")
        conn.execute(
            """INSERT OR IGNORE INTO system_config (key, value_num, category, source)
               VALUES ('norm.witness_min_docs', 3, 'lab', 'owner_2026-09-02')""")
        conn.execute(
            """INSERT OR IGNORE INTO system_config (key, value_num, category, source)
               VALUES ('norm.coverage_min_n', 5, 'lab', 'owner_2026-09-02')""")
        # «Чужой геном поверх своего чипа» (приём VCF, 28.09; в настройки — решение владельца
        # 29.09). Доля расхождений VCF с чипом выше порога → фаза A ничего не пишет. Свой WGS
        # против своего чипа — 0 из 157 950; у разных людей — десятки процентов. Меньше общих
        # позиций, чем минимум, — судить не по чему. Зеркало — genome_intake._CHIP_FALLBACK.
        conn.execute(
            """INSERT OR IGNORE INTO system_config (key, value_num, category, source)
               VALUES ('genome.chip_disagreement_max', 0.005, 'genome', 'code_seed')""")
        conn.execute(
            """INSERT OR IGNORE INTO system_config (key, value_num, category, source)
               VALUES ('genome.chip_identity_min_matched', 1000, 'genome', 'code_seed')""")
        # Морская карточка брифа (решение владельца 29.09: пороги — в настройках). Зеркало —
        # env_context._MARINE_FALLBACK. Волны ≥ notable — не зовём купаться; ≥ safety — полоса
        # безопасности; вода ≥ swim_temp — приглашение поплавать.
        for _k, _v in (("env.sea_waves_notable_m", 1.25), ("env.sea_waves_safety_m", 2.5),
                       ("env.sea_swim_temp_c", 22.0)):
            conn.execute("INSERT OR IGNORE INTO system_config (key, value_num, category, source) "
                         "VALUES (?, ?, 'env', 'code_seed')", (_k, _v))
        # Языки OCR и маркеры типов документов — данные тенанта (решение владельца 29.09).
        # Сид — нейтральный минимум; языки/слова бланков конкретной страны пишет установка тенанта.
        # Зеркала — config_db._OCR_LANGUAGES_FALLBACK / _DOC_TYPE_MARKERS_FALLBACK.
        conn.execute("INSERT OR IGNORE INTO system_config (key, value_text, category, source) "
                     "VALUES ('ocr.languages', 'eng+rus', 'docs', 'code_seed')")
        conn.execute("INSERT OR IGNORE INTO system_config (key, value_json, category, source) "
                     "VALUES ('docs.type_markers', '{}', 'docs', 'code_seed')")








# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM CONFIG — единый conit конфигурации (replaces Python-константы)
# ─────────────────────────────────────────────────────────────────────────────
















# ── ConsultationSession persistence ───────────────────────────────────────────










# ── Pending document reviews ──────────────────────────────────────────────────
















# ── Imported docs (dedup) ──────────────────────────────────────────────────────













# ── Doc patterns ───────────────────────────────────────────────────────────────




# ── W5A-INT-3: CBCR payload API (Q1=B) ──────────────────────────────────────








# ── Medical Record — публичный API ────────────────────────────────────────────
















# ── HV-1: hypothesis_outcomes ────────────────────────────────────────────────

def _ensure_hypothesis_outcomes():
    """Lazy-создание таблицы hypothesis_outcomes + миграция новых колонок."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hypothesis_outcomes (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id        INTEGER NOT NULL,
                verdict          TEXT NOT NULL,
                confidence       REAL,
                evidence_json    TEXT,
                reasoning        TEXT,
                coordinator_text TEXT,
                evaluated_at     TEXT DEFAULT (date('now')),
                sent_at          TEXT,
                delivering_since TEXT,
                telegram_chat_id INTEGER
            )
        """)
        # Миграция: добавляем колонки если таблица существовала без них
        for col, typedef in [
            ("coordinator_text", "TEXT"),
            ("sent_at",          "TEXT"),
            ("delivering_since", "TEXT"),
            ("telegram_chat_id", "INTEGER"),
        ]:
            try:
                conn.execute(
                    f"ALTER TABLE hypothesis_outcomes ADD COLUMN IF NOT EXISTS {col} {typedef}"
                )
            except Exception:
                pass  # silent-ok: ADD COLUMN IF NOT EXISTS не нужна если колонка есть


























# ══════════════════════════════════════════════════════════════════════════════
# IMPORT LIBRARY: lab_formats + lab_name_aliases  (2026-06-01)
# ══════════════════════════════════════════════════════════════════════════════

def _migrate_import_library() -> None:
    """
    Создаёт таблицы lab_formats и lab_name_aliases.
    Идемпотентно — вызывается при каждом init_db().
    """
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS lab_formats (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL UNIQUE,   -- 'synevo', 'imd_berlin', …
            description     TEXT,
            detector_config TEXT DEFAULT '{}',       -- JSON: сигналы для classify()
            parser_type     TEXT DEFAULT 'fitz',     -- 'fitz'|'regex'|'llm'
            created_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS lab_name_aliases (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            format_id    INTEGER NOT NULL REFERENCES lab_formats(id),
            raw_name     TEXT NOT NULL,
            canonical    TEXT NOT NULL,
            confirmed    INTEGER NOT NULL DEFAULT 0,  -- 0=suggested, 1=confirmed
            orphan       INTEGER NOT NULL DEFAULT 0,  -- 1=canonical не в lab_refs
            source_example TEXT,                      -- пример raw PDF text
            confirmed_at TEXT,
            created_at   TEXT DEFAULT (datetime('now')),
            UNIQUE(format_id, raw_name)
        );

        CREATE TABLE IF NOT EXISTS pending_field_reviews (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            format_id    INTEGER REFERENCES lab_formats(id),
            raw_name     TEXT NOT NULL,
            value        REAL,
            unit         TEXT,
            source_file  TEXT,
            suggested    TEXT,                        -- suggested canonical (fuzzy)
            status       TEXT NOT NULL DEFAULT 'pending',  -- pending|confirmed|rejected
            created_at   TEXT DEFAULT (datetime('now'))
        );

        -- Карантин мерцающих пар валид-гейта (Ш3, 2026-07-26). СОСТОЯНИЕ, а не diff недели:
        -- до этого метка снималась сама через один стабильный прогон, без вердикта (P1-03).
        -- method_epoch в ключе: смена метода — новый вопрос о той же паре, старый вердикт к
        -- нему не относится. UNIQUE даёт идемпотентность повторного прогона (INSERT OR IGNORE).
        CREATE TABLE IF NOT EXISTS passset_quarantine (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            -- CHECK'и добавлены 2026-07-26 по ревью R4 (VG-R4-05): порча колонок давала
            -- pending=0/stuck=0 без единого предупреждения — «карантин пуст» и «карантин
            -- нечитаем» были неотличимы. ВНИМАНИЕ: CREATE TABLE IF NOT EXISTS не достраивает
            -- ограничения к УЖЕ созданной таблице, а SQLite не умеет ALTER ADD CONSTRAINT.
            -- Поэтому боевую таблицу защищает не это, а датчик `corrupt_quarantine_rows`;
            -- он же сообщает, есть ли CHECK на живой таблице (`quarantine_schema_guarded`).
            pair         TEXT NOT NULL CHECK (trim(pair) <> ''),
            family       TEXT NOT NULL CHECK (family IN ('D','A','q_lag')),
            method_epoch TEXT NOT NULL DEFAULT '',
            entered_at   TEXT NOT NULL DEFAULT (datetime('now')),
            status       TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','admitted','rejected')),
            resolved_at  TEXT,
            resolution   TEXT,                        -- обоснование вердикта (обязательно)
            resolved_by  TEXT,                        -- human | controller
            -- разрешённая пара обязана нести обоснование: вердикт без причины — не решение
            CHECK (status = 'pending' OR trim(coalesce(resolution,'')) <> ''),
            UNIQUE(pair, method_epoch)
        );
        """)


def _seed_lab_formats() -> None:
    """
    Засевает начальные форматы и alias-карты из кода.
    INSERT OR IGNORE — идемпотентно.
    """
    with get_conn() as conn:
        # Synevo (Georgian digital PDF)
        conn.execute(
            "INSERT OR IGNORE INTO lab_formats(name, description, parser_type) "
            "VALUES (?,?,?)",
            ("synevo", "Synevo — digital PDF with Georgian script", "fitz")
        )
        # doc_pattern: "synevo" в имени файла → lab (idempotent, WHERE NOT EXISTS)
        conn.execute(
            "INSERT INTO doc_patterns"
            "(pattern, doc_type, match_on, match_type, notes) "
            "SELECT ?,?,?,?,? WHERE NOT EXISTS ("
            "  SELECT 1 FROM doc_patterns WHERE pattern=? AND doc_type=?"
            ")",
            ("synevo", "lab", "filename", "literal",
             "Synevo filename pattern — seeded by _seed_lab_formats 2026-06-01",
             "synevo", "lab")
        )
        row = conn.execute(
            "SELECT id FROM lab_formats WHERE name='synevo'"
        ).fetchone()
        synevo_id = row[0] if row else None

        if synevo_id is None:
            return

        # Seed SYNEVO_NAME_MAP as confirmed aliases
        SEED_ALIASES = [
            ("CRP", "CRP"), ("AST", "AST"), ("ALT", "ALT"), ("GGT", "GGT"),
            # 2026-07-29: конвенция имени — явное имя `lab_canon`, решение владельца.
            # Было ("Total Bilirubin","Bilirubin") и ещё пять коротких форм; короткое
            # `Bilirubin` двусмысленно рядом с существующим `Bilirubin_direct`.
            ("Total Bilirubin", "Bilirubin_total"), ("Glucose", "Glucose"),
            ("Hemoglobin A1C", "HbA1c"), ("Lipase", "Lipase"), ("TSH", "TSH"),
            ("CA-125 II", "CA125"), ("CA-125", "CA125"),
            ("Triglycerides", "Triglycerides"),
            ("Total Cholesterol", "Cholesterol_Total"),
            ("HDL Cholesterol", "HDL"), ("LDL Cholesterol", "LDL"),
            ("Atherogenic index", "Atherogenic_index"), ("VLDL", "VLDL"),
            ("Ferritin", "Ferritin"), ("Transferrin", "Transferrin"),
            ("Iron", "Iron"), ("Leucocytes", "WBC"), ("Erythrocytes", "RBC"),
            ("Hemoglobin", "HGB"), ("Hematocrit", "HCT"),
            ("MCV", "MCV"), ("MCH", "MCH"), ("MCHC", "MCHC"),
            ("MPV", "MPV"), ("RDW", "RDW"), ("PLT", "PLT"),
            ("Platelets", "PLT"), ("Vitamin D", "Vitamin_D"),
            ("Vitamin B12", "Vitamin_B12"), ("Folate", "Folate"),
            ("ESR", "ESR"), ("Creatinine", "Creatinine"), ("Urea", "Urea"),
            # 2026-06-01 additions
            ("Mg", "Magnesium"), ("P-amylase", "Amylase_pancreatic"),
            ("Troponin T hs", "Troponin"), ("Troponin T", "Troponin"),
            ("Thrombocytes", "PLT"),
            # 2026-07-29, исправлено ПО ИСТОЧНИКУ (бланк лаборатории).
            # Бланк печатает ДВЕ колонки и обе помечает: процентную суффиксом «%»
            # («Neutrophils /ნეიტროფილები %  <знач>  %  <реф>»), абсолютную — «Abs»
            # («Neutrophils Abs/ნეიტროფილები აბს  <знач>  10^9/L  <реф>»).
            # ГОЛОГО имени в бланке НЕТ: строки 199 и 208 — это «… Abs», разорванные
            # переносом при извлечении текста. Отсюда две правки:
            #   1) «X Abs» ведёт в «X_abs», а не в голое «X» (раньше вело в голое —
            #      это и создавало у аналита два канонических имени, см. ратчет);
            #   2) пары для ГОЛОГО имени убраны. Раньше они утверждали «Neutrophils =
            #      процент», что источнику противоречит: голое имя здесь — обрезанный
            #      абсолют. Догадку в данных заменяем отсутствием догадки: голое имя
            #      падает в `lab_canon`, который отвечает «_abs» — и это совпадает с
            #      бланком. «Basophilis» ОСТАВЛЕНА: это опечатка самой лаборатории
            #      в процентной строке, а не наша.
            ("Neutrophils Abs", "Neutrophils_abs"), ("Lymphocytes Abs", "Lymphocytes_abs"),
            ("Monocytes Abs", "Monocytes_abs"), ("Eosinophils Abs", "Eosinophils_abs"),
            ("Basophils Abs", "Basophils_abs"), ("Basophilis", "Basophils_pct"),
        ]
        for raw, canonical in SEED_ALIASES:
            conn.execute(
                "INSERT OR IGNORE INTO lab_name_aliases"
                "(format_id, raw_name, canonical, confirmed, confirmed_at) "
                "VALUES (?,?,?,1,datetime('now'))",
                (synevo_id, raw, canonical)
            )

        # ── IMD Berlin (немецкая лаборатория, 2026-06-15) ─────────────────────
        conn.execute(
            "INSERT OR IGNORE INTO lab_formats(name, description, parser_type) "
            "VALUES (?,?,?)",
            ("imd_berlin", "IMD Institut für Medizinische Diagnostik Berlin", "fitz")
        )
        conn.execute(
            "INSERT INTO doc_patterns"
            "(pattern, doc_type, match_on, match_type, notes) "
            "SELECT ?,?,?,?,? "
            "WHERE NOT EXISTS ("
            "    SELECT 1 FROM doc_patterns WHERE pattern=? AND doc_type=?"
            ")",
            ("imd", "lab", "filename", "literal",
             "IMD Berlin filename pattern — seeded 2026-06-15",
             "imd", "lab"),
        )
        imd_row = conn.execute(
            "SELECT id FROM lab_formats WHERE name='imd_berlin'"
        ).fetchone()
        imd_id = imd_row[0] if imd_row else None

        if imd_id is not None:
            IMD_ALIASES = [
                ("NT-pro BNP i.S.", "NT_proBNP"),
                ("Immunglobulin G 1", "IgG1"),
                ("Immunglobulin G 2", "IgG2"),
                ("Immunglobulin G 3", "IgG3"),
                ("Immunglobulin G 4", "IgG4"),
                ("MCV-AAk i.S.", "MCV_AAk"),
                ("Holotranscobalamin (akt. VB12) i.S.", "Holotranscobalamin"),
                ("Methylmalonsäure i.S.°", "Methylmalonic_acid"),
                ("CRP i.S.", "CRP"),
                ("Ferritin i.S.", "Ferritin"),
                ("TSH i.S.", "TSH"),
                ("Vitamin D i.S.", "Vitamin_D"),
                ("HbA1c i.B.", "HbA1c"),
                ("Glucose i.S.", "Glucose"),
                ("Kreatinin i.S.", "Creatinine"),
                ("Harnstoff i.S.", "Urea"),
                ("Harnsäure i.S.", "UricAcid"),
            ]
            for raw, canonical in IMD_ALIASES:
                conn.execute(
                    "INSERT OR IGNORE INTO lab_name_aliases"
                    "(format_id, raw_name, canonical, confirmed, confirmed_at) "
                    "VALUES (?,?,?,1,datetime('now'))",
                    (imd_id, raw, canonical)
                )


# ── CRUD: lab_formats ─────────────────────────────────────────────────────────







# ── CRUD: lab_name_aliases ────────────────────────────────────────────────────










# ── CRUD: pending_field_reviews ───────────────────────────────────────────────
















# ══════════════════════════════════════════════════════════════════════════════
# HAE METRIC REGISTRY  (2026-06-01)
# ══════════════════════════════════════════════════════════════════════════════

def _migrate_hae_registry() -> None:
    """Создаёт hae_metric_registry. Идемпотентно."""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS hae_metric_registry (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_name TEXT NOT NULL UNIQUE,
            status      TEXT NOT NULL DEFAULT 'new',
                -- 'handled'  = import_apple_health.py полностью обрабатывает
                -- 'tracked'  = известна, но не обрабатывается (осознанное решение)
                -- 'new'      = впервые увидели, нужен алерт
            first_seen  TEXT,
            last_seen   TEXT,
            sample_values TEXT DEFAULT '[]',  -- JSON array из 3-5 примеров
            unit        TEXT,
            alerted_at  TEXT,                 -- последний алерт (дедупликация 7д)
            notes       TEXT
        );
        """)


# Поддерживаемые метрики Apple Health для реестра: текущий поток и импорт архивов.
# Перечень описывает возможности приёма, а не наличие записей конкретного человека.
# 'handled' = парсер обрабатывает  'tracked' = знаем, не берём (осознанно)
_HAE_SEED: list[tuple[str, str, str]] = [
    # handled — парсер умеет
    ("sleep_analysis",                  "handled", "сон из iPhone"),
    ("heart_rate",                      "handled", "ЧСС из iPhone"),
    ("resting_heart_rate",              "handled", "ЧСС покоя"),
    ("heart_rate_variability",          "handled", "HRV из iPhone"),
    ("blood_oxygen_saturation",         "handled", "SpO2"),
    ("step_count",                      "handled", "шаги"),
    ("walking_running_distance",        "handled", "дистанция"),
    ("active_energy",                   "handled", "активные ккал"),
    ("basal_energy_burned",             "handled", "базальные ккал"),
    ("vo2_max",                         "handled", "VO2max"),
    ("weight_body_mass",                "handled", "вес"),
    ("blood_pressure_systolic",         "handled", "давление систол."),
    ("blood_pressure_diastolic",        "handled", "давление диастол."),
    ("respiratory_rate",                "handled", "частота дыхания"),
    ("walking_heart_rate_average",      "handled", "ЧСС при ходьбе"),
    ("time_in_daylight",                "handled", "минуты на свету"),
    ("flights_climbed",                 "handled", "пролёты лестниц"),
    ("mindful_minutes",                 "handled", "минуты медитации"),
    # tracked — знаем, не берём пока (Модуль 2 добавит)
    ("apple_exercise_time",             "tracked", "минуты упражнений (Apple ring)"),
    ("physical_effort",                 "tracked", "MET — интенсивность"),
    ("walking_speed",                   "tracked", "скорость ходьбы — гейт-маркер"),
    ("walking_step_length",             "tracked", "длина шага — гейт-маркер"),
    ("walking_asymmetry_percentage",    "tracked", "асимметрия походки"),
    ("walking_double_support_percentage","tracked", "двойная опора — гейт"),
    ("apple_stand_hour",                "tracked", "часы стоя (Apple ring)"),
    ("apple_stand_time",                "tracked", "минуты стоя"),
    ("stair_speed_up",                  "tracked", "скорость подъёма по лестнице"),
    ("stair_speed_down",                "tracked", "скорость спуска по лестнице"),
    ("environmental_audio_exposure",    "tracked", "шумовое воздействие"),
    ("headphone_audio_exposure",        "tracked", "Apple audio dosimetry в наушниках, low signal — не подключаем (added 2026-06-15)"),
    # Дополнительные метрики: статус определяет способ обработки, а не наличие данных.
    # «кандидат» означает, что решение о включении ещё предстоит.
    # Состав тела может приходить из Fitdays через Apple Health.
    ("body_fat_percentage",             "handled", "берём: весы Fitdays через Apple Health, только в пустые ячейки (решение 26.09)"),
    ("body_mass_index",                 "handled", "берём: весы Fitdays через Apple Health, только в пустые ячейки (решение 26.09)"),
    ("lean_body_mass",                  "tracked", "тощая масса — Fitdays (Фаза 2)"),
    ("blood_pressure",                  "handled", "давление тонометра (Withings): одна метрика, поля systolic/diastolic; колонка = среднее, max в raw (26.09)"),
    ("cardio_recovery",                 "tracked", "восстановление ЧСС после нагрузки — кандидат"),
    ("six_minute_walking_test_distance","tracked", "6-мин тест ходьбы — функц. статус, кандидат (онко-релевантно)"),
    ("height",                          "tracked", "рост — статич."),
    # Питание: поддерживаемые категории журнала еды.
    ("dietary_energy",                  "tracked", "пищевая энергия (лог питания)"),
    ("protein",                         "tracked", "белок (лог питания) — кандидат (онко)"),
    ("carbohydrates",                   "tracked", "углеводы (лог питания)"),
    ("fiber",                           "tracked", "клетчатка (лог питания)"),
    ("total_fat",                       "tracked", "жиры всего (лог питания)"),
    ("saturated_fat",                   "tracked", "насыщ. жиры (лог питания)"),
    ("monounsaturated_fat",             "tracked", "мононенасыщ. жиры (лог питания)"),
    ("polyunsaturated_fat",             "tracked", "полиненасыщ. жиры (лог питания)"),
    ("cholesterol",                     "tracked", "холестерин пищевой (лог питания)"),
    ("dietary_sugar",                   "tracked", "сахар (лог питания)"),
    ("dietary_water",                   "tracked", "вода (лог питания)"),
    ("calcium",                         "tracked", "кальций (лог питания)"),
    ("iron",                            "tracked", "железо (лог питания) — кандидат (анемия/онко)"),
    ("magnesium",                       "tracked", "магний (лог питания)"),
    ("potassium",                       "tracked", "калий (лог питания)"),
    ("sodium",                          "tracked", "натрий (лог питания)"),
    ("zinc",                            "tracked", "цинк (лог питания)"),
    ("phosphorus",                      "tracked", "фосфор (лог питания)"),
    ("chloride",                        "tracked", "хлор (лог питания)"),
    ("copper",                          "tracked", "медь (лог питания)"),
    ("manganese",                       "tracked", "марганец (лог питания)"),
    ("selenium",                        "tracked", "селен (лог питания)"),
    ("iodine",                          "tracked", "йод (лог питания)"),
    ("chromium",                        "tracked", "хром (лог питания)"),
    ("molybdenum",                      "tracked", "молибден (лог питания)"),
    ("vitamin_a",                       "tracked", "вит. A (лог питания)"),
    ("vitamin_c",                       "tracked", "вит. C (лог питания)"),
    ("vitamin_d",                       "tracked", "вит. D (лог питания) — кандидат (онко)"),
    ("vitamin_e",                       "tracked", "вит. E (лог питания)"),
    ("vitamin_k",                       "tracked", "вит. K (лог питания)"),
    ("vitamin_b6",                      "tracked", "вит. B6 (лог питания)"),
    ("vitamin_b12",                     "tracked", "вит. B12 (лог питания) — кандидат (анемия)"),
    ("folate",                          "tracked", "фолат (лог питания) — кандидат (анемия)"),
    ("niacin",                          "tracked", "ниацин B3 (лог питания)"),
    ("riboflavin",                      "tracked", "рибофлавин B2 (лог питания)"),
    ("thiamin",                         "tracked", "тиамин B1 (лог питания)"),
    ("biotin",                          "tracked", "биотин B7 (лог питания)"),
    ("pantothenic_acid",                "tracked", "пантотеновая B5 (лог питания)"),
]


# Решения по обработке метрик («не берём:» — судимый формат; «берём:» — пометка
# обработки). Формат судит hae_checker.judge_payload: префикс «не берём:» = решение;
# «покрыто: <колонка>» = утверждение, проверяемое по дням прихода.
# Метрики без решения сюда НЕ пишутся: отсутствие решения видит ночной триаж.
_HAE_DECISIONS: dict[str, str] = {
    "environmental_audio_exposure": "не берём: шум окружения — не показатель тела (26.09)",
    "headphone_audio_exposure":     "не берём: громкость в наушниках, слабый сигнал (2026-06-15)",
    "apple_stand_hour":             "не берём: дубль минут стояния; покрыто: stand_min",
    # Приём значений в пустые ячейки предотвращает потерю из-за ложного «покрытия».
    "body_fat_percentage":          "берём: весы Fitdays через Apple Health, только в пустые ячейки (решение 26.09)",
    "body_mass_index":              "берём: весы Fitdays через Apple Health, только в пустые ячейки (решение 26.09)",
    "lean_body_mass":               "не берём: производная веса и жира (состав тела — derived, решение владельца 2026-07-12)",
    "height":                       "не берём: статичный параметр",
}


_HAE_MODULE2_HANDLED = {
    "apple_exercise_time", "cycling_distance", "physical_effort",
    "walking_speed", "walking_step_length", "walking_asymmetry_percentage",
    "walking_double_support_percentage", "apple_stand_time",
    # 2026-09-26: ветка парсера появилась; до неё запись «tracked, см. systolic/diastolic»
    # была ложной с апреля (раздельные имена не пришли ни разу) — живую строку правим здесь,
    # INSERT OR IGNORE выше статус существующей не меняет
    "blood_pressure",
}


def _seed_hae_registry() -> None:
    """Засевает начальные метрики. INSERT OR IGNORE — идемпотентно.
    Module 2: обновляет tracked → handled для новых парсеров."""
    with get_conn() as conn:
        for metric_name, status, notes in _HAE_SEED:
            conn.execute(
                "INSERT OR IGNORE INTO hae_metric_registry"
                "(metric_name, status, notes) VALUES (?,?,?)",
                (metric_name, status, notes)
            )
        # Реконсиль залежавшегося 'new': метрика в сиде = ИЗВЕСТНАЯ диспозиция, поэтому
        # 'new' у неё — устаревший артефакт (checker зарегистрировал раньше, чем появилась
        # seed-запись, напр. headphone_audio_exposure). INSERT OR IGNORE выше статус существующих
        # не трогает → навязываем seed-статус ТОЛЬКО там, где сейчас 'new' (handled/tracked не ломаем),
        # и доливаем пустые notes. Легитимный 'new' остаётся только у метрик ВНЕ сида (истинно новых).
        for metric_name, status, notes in _HAE_SEED:
            conn.execute(
                "UPDATE hae_metric_registry SET status=?, notes=COALESCE(NULLIF(notes,''), ?) "
                "WHERE metric_name=? AND status='new'",
                (status, notes, metric_name)
            )
        # Module 2: метрики теперь обрабатываются → обновляем статус. С 26.09 только из
        # tracked/new: статус судит hae_checker.judge_payload по поведению разборщика, и его
        # вердикт 'unowned' сид при каждом init_db перетирать не должен (иначе ночной датчик
        # слепнет после любого рестарта бота).
        for name in _HAE_MODULE2_HANDLED:
            conn.execute(
                "UPDATE hae_metric_registry SET status='handled' "
                "WHERE metric_name=? AND status IN ('tracked','new')",
                (name,)
            )
        # Решения «не берём» (26.09): дом — _HAE_DECISIONS; живые notes следуют ему,
        # статус не трогаем (его судит разборщик).
        for name, note in _HAE_DECISIONS.items():
            conn.execute(
                "UPDATE hae_metric_registry SET notes=? WHERE metric_name=? AND notes IS NOT ?",
                (note, name, note)
            )
        # cycling_distance поддерживается парсером: регистрируем как handled.
        conn.execute(
            "INSERT OR IGNORE INTO hae_metric_registry"
            "(metric_name, status, notes) VALUES (?,?,?)",
            ("cycling_distance", "handled", "велосипед — обнаружен сканером 2026-06-01")
        )
        conn.execute(
            "UPDATE hae_metric_registry SET status='handled' WHERE metric_name='cycling_distance' "
            "AND status IN ('tracked','new')"
        )


# ── CRUD ─────────────────────────────────────────────────────────────────────







def _migrate_pharmaco_tables() -> None:
    """2026-06-26: фармакогенетика Wave 1.

    genome_imports — трекер запусков vcf_import_pipeline; даёт genome_import_id
    для write-through гарантии между raw_snps и производными таблицами.

    pharmaco_phenotypes — результаты star-allele calling для 7 фармакогенов.
    genome_import_id → genome_imports.id обеспечивает проверку свежести.
    confidence ∈ {high, medium, low, indeterminate}.
    """
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS genome_imports (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                import_date           TEXT    NOT NULL DEFAULT (date('now')),
                vcf_source            TEXT,
                phase_e_pharmaco_at   TEXT,
                phase_f_monogenic_at  TEXT,
                phase_g_prs_at        TEXT,
                completed_at          TEXT,
                notes                 TEXT
            );

            CREATE TABLE IF NOT EXISTS pharmaco_phenotypes (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                gene              TEXT    NOT NULL,
                star_allele_1     TEXT,
                star_allele_2     TEXT,
                phenotype         TEXT    NOT NULL,
                confidence        TEXT    NOT NULL DEFAULT 'indeterminate',
                coverage_snp_count INTEGER DEFAULT 0,
                genome_import_id  INTEGER REFERENCES genome_imports(id),
                computed_at       TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE UNIQUE INDEX IF NOT EXISTS uq_pharmaco_gene
                ON pharmaco_phenotypes(gene);
        """)





# ── Поток C (2026-06-27): домен experiments вынесен в experiments_db.py ──
from experiments_db import (  # noqa: E402
    get_active_experiments, get_experiment_stats, complete_experiment,
    log_experiment_day, start_experiment, update_experiment_check_results,
)


# ── Поток C (2026-06-27): домен protocols вынесен в protocols_db.py ──
from protocols_db import (  # noqa: E402
    get_active_protocols, save_protocol, retire_protocol,
)


# ── Поток C (2026-06-27): домен alerts → alerts_db.py ──
from alerts_db import (  # noqa: E402
    save_alert, get_active_alerts,
)


# ── Поток C (2026-06-27): домен consultations → consultations_db.py ──
from consultations_db import (  # noqa: E402
    get_consultations, save_consultation, get_last_consultation,
    consultation_exists, specialty_key,
)


# ── Поток C (2026-06-27): домен tasks → tasks_db.py ──
from tasks_db import (  # noqa: E402
    save_task, resolve_task, get_open_tasks, get_overdue_tasks, mark_task_sent,
    get_unsent_assessment_tasks, get_unsent_tasks, get_task_by_tg_message,
    wake_snoozed_assessment_tasks,
    get_questions_needing_delivery, get_open_questions, get_tasks_known_to_person,
    record_duplicate_skip,
)


# ── Поток C (2026-06-27): домен memory → memory_db.py ──
from memory_db import (  # noqa: E402
    get_memory, save_memory,
)


# ── Поток C (2026-06-27): домен agent_reports → agent_reports_db.py ──
from agent_reports_db import (  # noqa: E402
    save_agent_report, get_agent_report, get_reports_with_findings,
)


# ── Поток C (2026-06-27): домен genome → genome_db.py ──
from genome_db import (  # noqa: E402
    get_significant_variants, get_carrier_variants, get_variants_by_genes, genome_summary_counts, get_snps_batch, upsert_genetic_variant, save_genome_update_log, get_raw_snp, mark_genome_log_sent,
    carrier_status_null_allele_violations,
)


# ── Поток C (2026-06-27): домен labs → labs_db.py ──
from labs_db import (  # noqa: E402
    get_recent_labs, canon_window_note, declared_boundary, get_lab_series, get_modal_reference, compute_bank_refs, get_lab_refs_meta, get_lab_trend, get_lab_trend_by_component, trend_members, get_labs_by_date, get_lab_history, get_lab_refs, get_effective_lab_schedule, build_lab_history_context, build_specialized_context, build_codraw_context, draw_key, upsert_monitoring_rule, get_lab_format_by_name, get_lab_format_by_id, get_confirmed_aliases, get_confirmed_aliases_all, confirmed_alias_conflicts, confirmed_alias_targets, domain_verdicts, domain_home, analyte_norm_verdicts, set_analyte_norm_verdict, get_all_canonical_names, get_all_lab_dates, effective_freshness, result_text,
    unrepeated_draw, build_unrepeated_draw_context,
    draw_summaries, flag_direction,
)


# ── Поток C (2026-06-27): домен metrics → metrics_db.py ──
from metrics_db import (  # noqa: E402
    get_day, get_window, get_stats, get_metric_percentiles, upsert_metrics_from_json, build_context,
    render_all_metrics, metric_columns, data_sources,
)


# ── Поток C (2026-06-27): домен checkins → checkins_db.py ──
from checkins_db import (  # noqa: E402
    get_recent_checkins, save_checkin, get_checkin_by_date, update_checkin_scores,
)


# ── Поток C (2026-06-27): домен problems → problems_db.py ──
from problems_db import (  # noqa: E402
    get_problem_list, upsert_problem, save_problem_proposal,
    record_surveillance_decision, active_surveillance_decisions, format_surveillance_decisions,
    existing_problem_for, proposal_dedup_key,
)


# ── Поток C (2026-06-27): домен periods → periods_db.py ──
from periods_db import (  # noqa: E402
    historical_periods, current_periods, get_active_period, save_period, future_periods, soft_delete_period,
)


# ── Поток C (2026-06-27): домен treatment → treatment_db.py ──
from treatment_db import (  # noqa: E402
    get_medications, upsert_medication, get_episodes, set_medication_confirmation, get_proposed_medications, get_unsent_proposed_medications, mark_medication_gate_sent, save_episode, complete_planned_event,
)


# ── Поток C (2026-06-27): домен assessments → assessments_db.py ──
from assessments_db import (  # noqa: E402
    save_assessment_session, get_assessment_session, get_active_assessment_session, update_assessment_session,
)


# ── Поток C (2026-06-27): домен events → events_db.py ──
from events_db import (  # noqa: E402
    save_event, get_events, get_context_events, save_context_event,
)


# ── Поток C (2026-06-27): домен hae → hae_db.py ──
from hae_db import (  # noqa: E402
    get_hae_registry, upsert_hae_metric, get_pending_hae_alerts,
)


# ── Поток C (2026-06-27): домен profile → profile_db.py ──
from profile_db import (  # noqa: E402
    get_patient_profile, apply_stated, profile_fields, upsert_profile, get_profile_context,
)


# ── Поток C (2026-06-27): домен config → config_db.py ──
from config_db import (  # noqa: E402
    get_config, get_routing_keywords, get_domain_signals, get_doc_patterns, mark_imported, get_imported_sources, upsert_config,
    default_visit_specialist, ocr_languages, doc_type_markers, marker_doc_type,
)


# ── Поток C (2026-06-27): домен constitutions → constitutions_db.py ──
from constitutions_db import (  # noqa: E402
    upsert_constitution, get_constitution, list_constitutions,
)


# ── Поток C (2026-06-27): домен proposals → proposals_db.py ──
from proposals_db import (  # noqa: E402
    get_pending_proposals, apply_proposal, reject_proposal, expire_aged_proposals,
    get_undelivered_proposals, mark_proposal_delivered, format_proposal_card,
    stuck_undelivered_proposals,
)


# ── Поток C (2026-06-27): домен doc_reviews → doc_reviews_db.py ──
from doc_reviews_db import (  # noqa: E402
    save_pending_doc_review, get_pending_doc_reviews, mark_doc_review_sent, confirm_doc_review, reject_doc_review, auto_confirm_stale_reviews, import_from_pending,
)


# ── Поток C (2026-06-27): домен hypotheses → hypotheses_db.py ──
from hypotheses_db import (  # noqa: E402
    save_hypothesis_outcome, get_hypothesis_outcome, get_hypotheses_awaiting_evaluation, get_hypothesis_accuracy_stats, get_unsent_hypothesis_outcomes, mark_hypothesis_outcome_delivering, mark_hypothesis_outcome_sent, save_cbcr_payload, get_cbcr_payload,
)


# ── Карантин мерцающих пар (Ш3 ремонта validation_gate, 2026-07-26) → quarantine_db.py ──
from quarantine_db import (  # noqa: E402
    queue_quarantine, pending_quarantine_pairs, resolve_quarantine, quarantine_rows,
    # Ревью R4 (2026-07-26): три новых контракта, объявленные явно, а не оставленные
    # «служебными». corrupt_quarantine_rows / quarantine_schema_guarded — датчики того, что
    # состояние карантина ЧИТАЕМО (без них pending=0 покрывало собой нечитаемую таблицу);
    # method_epoch — единый источник эпохи для писателя и CLI, который до этого считал её
    # по-своему и не мог снять ни одной пары.
    corrupt_quarantine_rows, quarantine_schema_guarded, method_epoch,
    # Ревью R5 (2026-07-26): решение по паре вместо множества pending. `admitted` и `rejected`
    # были для читателя веры неразличимы — оба исчезали из выборки, и отклонённая человеком
    # связь возвращалась в конституции обычной находкой (VG-R5-01).
    quarantine_decisions,
)


# ── Поток C (2026-06-27): домен field_reviews → field_reviews_db.py ──
from field_reviews_db import (  # noqa: E402
    confirm_field_alias, queue_field_reviews, get_pending_field_reviews, set_field_review_tg_message, get_field_review_by_tg_message, resolve_field_review,
)


# ── Поток C (2026-06-27): домен consult_sessions → consult_sessions_db.py ──
from consult_sessions_db import (  # noqa: E402
    save_consultation_session, load_consultation_session, delete_consultation_session,
)


# ── Поток C (2026-06-27): домен import_status → import_status_db.py ──
from import_status_db import (  # noqa: E402
    set_import_status, get_import_staleness, check_data_freshness, get_conit_limit,
)


# ── Поток C (2026-06-27): домен rules → rules_db.py ──
from rules_db import (  # noqa: E402
    get_absolute_thresholds, get_trend_thresholds, get_lab_trend_thresholds, get_threshold,
    get_active_constraints,
)


def _translated_reason_templates(rows: list[dict]) -> list[dict]:
    """Translate only recognised seed text, including rows seeded before i18n.

    Storage stays Russian; custom/clinician text is preserved. Resolve the person's
    language on each read so a profile change does not require reseeding the database.
    """
    lang = i18n.lang_of()
    if lang == "ru":
        return rows
    keys = (
        "hrv_low", "readiness_low", "deep_sleep_low", "sleep_score_low", "short_sleep",
        "systolic_high", "diastolic_high", "awake_high", "hrv_food", "hrv_deep",
        "hrv_lifestyle", "steps_relative", "steps_target", "spo2_low",
        "sleep_score_average", "sleep_score_consecutive", "deep_sleep_consecutive",
        "hrv_consecutive", "readiness_consecutive",
    )
    translations = {i18n.t("health_db.reason." + key, "ru"):
                    i18n.t("health_db.reason." + key, lang) for key in keys}
    return [{**row, "reason_template": translations.get(row["reason_template"],
                                                       row["reason_template"])} for row in rows]


def get_absolute_thresholds_for_person(direction: str | None = None) -> list[dict]:
    """Правила порогов с причинами на языке человека (засеянный текст переводится при чтении).
    Хранилище не меняется: get_absolute_thresholds (rules_db) отдаёт строки как есть."""
    return _translated_reason_templates(get_absolute_thresholds(direction))


def get_trend_thresholds_for_person() -> list[dict]:
    """Оконные правила с причинами на языке человека; хранилище — get_trend_thresholds (rules_db)."""
    return _translated_reason_templates(get_trend_thresholds())


# ── Поток C (2026-06-27): домен workouts → workouts_db.py ──
from workouts_db import (  # noqa: E402
    upsert_workout,
)
