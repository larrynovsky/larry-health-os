#!/usr/bin/env python3.11
"""
plist_env_liveness.py — датчик «плист новее загруженной джобы» (стейл-плист).

Закрывает класс «поправили файл, не применили»: под HEALTH_MULTITENANT=1 гард R1
(health_db._resolve_health_dir) требует явный HEALTH_DATA_DIR, иначе RuntimeError
на импорте. Плисты owner-джоб получили HEALTH_DATA_DIR в файле, но НЕ были
перезагружены → загруженные джобы падали на каждом плановом запуске
(literature-search/read, assessment-scheduler, 2026-07-05..10, BL-MULTITENANT-1).
Единственный сигнал был launchctl exit=1, который никто не читает.

Правило: если плист ОБЪЯВЛЯЕТ HEALTH_DATA_DIR, но загруженная джоба его НЕ имеет
(launchctl print), И глобально включён HEALTH_MULTITENANT — плист правили без
bootout+bootstrap. Не транзиент (держится до перезагрузки/ребута) → 2-сэмпл не нужен.

Один публичный entry point: find_stale_plist_env(). Чистый parse_stale_env вынесен
ради тестируемости (без subprocess/IO).
"""
from __future__ import annotations

import re
import infra_config  # основная машина — данные установки (private/infra.yaml)
from pathlib import Path

_LA = Path.home() / "Library/LaunchAgents"
_PREFIX = "com.larry.health"
# Каталог плистов ЭТОЙ установки и признак контейнера — единственный дом обоих ответов
# (нить docker-install, этап 2a, 2026-09-29). До этого шов был в двух местах под двумя
# именами (здесь HEALTH_LAUNCHAGENTS_DIR, в stderr_watch — HEALTH_LAUNCH_AGENTS), а
# daemon_liveness, lab_intake_watcher и producer_registry читали ~/Library/LaunchAgents
# напрямую. В контейнере .env указывает сюда на плисты, отрендеренные из тех же шаблонов,
# что и расписание (install.py --docker): ритм и логи датчики берут из дома расписания.
AGENTS_DIR_ENV = "HEALTH_LAUNCHAGENTS_DIR"
RUNTIME_ENV = "HEALTH_RUNTIME"


def agents_dir() -> Path:
    """Каталог плистов установки: env HEALTH_LAUNCHAGENTS_DIR (контейнер; шов тестов),
    иначе ~/Library/LaunchAgents. Читается при каждом вызове — не кэшировать."""
    import os
    return Path(os.environ.get(AGENTS_DIR_ENV) or _LA)


def in_container() -> bool:
    """True — службы исполняет supercronic/compose, а не launchd; `launchctl` здесь нет,
    и датчик, который его зовёт, обязан сказать «не судимо», а не «всё хорошо»."""
    import os
    return os.environ.get(RUNTIME_ENV) == "container"


_ENV_KEY = "HEALTH_DATA_DIR"
_MULTITENANT_KEY = "HEALTH_MULTITENANT"


def parse_stale_env(labels, declares_fn, loaded_has_fn, multitenant_on: bool) -> list[str]:
    """Чистая функция. labels — список лейблов; declares_fn(label)->bool (плист
    объявляет HEALTH_DATA_DIR); loaded_has_fn(label)->bool|None (в загруженной
    джобе есть HEALTH_DATA_DIR; None = джоба не загружена / print не удался → пропуск).
    Возвращает лейблы, где плист объявляет env, а загруженная джоба — нет.

    Без multitenant_on гарда R1 нет → дрейф безвреден, не шумим.
    """
    if not multitenant_on:
        return []
    stale = []
    for label in labels:
        if not declares_fn(label):
            continue
        loaded = loaded_has_fn(label)
        if loaded is False:  # именно False, не None (None = не загружена → не наша забота)
            stale.append(label)
    return stale


def _health_labels() -> list[str]:
    if not agents_dir().exists():
        return []
    return sorted(p.stem for p in agents_dir().glob(f"{_PREFIX}*.plist"))


def _plist_declares_datadir(label: str) -> bool:
    p = agents_dir() / f"{label}.plist"
    if not p.exists():
        return False
    return bool(re.search(rf"<key>{_ENV_KEY}</key>",
                          p.read_text(errors="ignore")))


def _loaded_has_datadir(label: str, uid: int):
    """True/False если джоба загружена и удалось прочитать её окружение; None иначе."""
    import subprocess
    r = subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"],
                       capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        return None  # не загружена / disabled / нет доступа — не наша забота
    return _ENV_KEY in r.stdout


def find_stale_plist_env() -> list[str]:
    """Studio-only. Лейблы owner-джоб, чей плист объявляет HEALTH_DATA_DIR, но
    загруженная джоба его не имеет (стейл-плист под мультитенантом). Вне Studio
    или при HEALTH_MULTITENANT!=1 — [].
    В контейнере — [] по построению, а не по слепоте: класса «правили файл, не
    перезагрузили джобу» там нет — окружение приходит из .env, и `compose up`
    пересоздаёт контейнер при его смене."""
    import socket
    if in_container():
        return []
    if not infra_config.is_primary():
        return []
    import os
    import subprocess

    getenv = subprocess.run(["launchctl", "getenv", _MULTITENANT_KEY],
                            capture_output=True, text=True, timeout=10)
    multitenant_on = getenv.stdout.strip() == "1"
    if not multitenant_on:
        return []

    uid = os.getuid()
    return parse_stale_env(
        _health_labels(),
        _plist_declares_datadir,
        lambda label: _loaded_has_datadir(label, uid),
        multitenant_on,
    )



# ── Сколько тенантов обслуживает launchd ─────────────────────────────────────

def select_tenant_dirs(plists) -> set[str]:
    """Чистое ядро: [(label, dict)] → множество HEALTH_DATA_DIR, которые реально
    обслуживаются. Считаем по тому, ЧТО объявляет джоба, а не по каталогам на
    диске: папка может лежать от старого тенанта, которого никто не обслуживает.
    """
    out = set()
    for _label, d in plists:
        dd = (d.get("EnvironmentVariables") or {}).get("HEALTH_DATA_DIR")
        if dd:
            out.add(str(dd))
    return out


def missing_tenant_dbs(served_dirs) -> list[str]:
    """Каталоги тенантов, которых launchd обслуживает, а их health.db на диске нет.

    Это и есть реестр ОЖИДАЕМЫХ тенантов (BL-BRIEF-TENANT-REGISTRY-1): он выводится из
    того, что джобы объявляют, а не из обхода диска — glob не вернёт удалённого целиком,
    плист о нём помнит. Список руками не ведётся, поэтому и не отстаёт."""
    import secrets_paths as _sp
    return sorted(d for d in served_dirs if not (Path(d) / _sp.TENANT_DB_REL).exists())


def tenants_served() -> set[str]:
    """Каталоги данных всех тенантов, которых обслуживает launchd этой машины.

    Публично: на этом числе стоит решение владельца «молчание человека признаком
    не считать» (2026-08-02) — оно опирается на то, что не-владельческий тенант
    ОДИН и живёт рядом. Утверждение об окружении обязано иметь счётчик, а не
    дату в комментарии (§18), поэтому счётчик здесь.
    """
    import plistlib
    if not agents_dir().exists():
        return set()
    plists = []
    for p in sorted(agents_dir().glob(f"{_PREFIX}*.plist")):
        try:
            d = plistlib.loads(p.read_bytes())
        except Exception:  # noqa: BLE001 — битый плист не наша авария
            continue
        plists.append((d.get("Label", p.stem), d))
    return select_tenant_dirs(plists)

# ── Инвентарь launchd против репозитория (нить brief-repeat, 2026-08-04) ─────
# Решение: копию плиста в репозиторий НЕ кладём. Замер, на котором оно построено:
# в `launchd/` лежало 14 копий, одна из них УЖЕ врала (партнёрский бот без
# MORNING_BRIEF_GATE), а живых health-джоб 38 — то есть 24 не имели копии вовсе,
# включая бота владельца, вотчер, триаж и бэкап. Копия не имеет гасителя: launchd
# читает ~/Library/LaunchAgents, git её не инвалидирует, и она врёт молча (§18).
# Симлинк живого файла в репозиторий хуже: `git push` менял бы расписание на диске,
# не перезагружая джобу — класс §12 «код доехал до диска, но не до процесса».
# Поэтому дом остаётся один, а репозиторий получает СЧЁТЧИК: датчик считает живое
# множество и сверяет с копиями, которые всё-таки есть.

def compare_inventories(repo: dict, live: dict) -> dict:
    """ЧИСТАЯ сверка. repo/live: label → разобранный плист (dict).

    Возвращает {"drifted": [(label, [имена_разошедшихся_ключей])],
                "live_only": [label], "repo_only": [label]}.
    Имена ключей, НЕ значения: значение может быть путём к секретам (§19).
    """
    drifted = []
    for label in sorted(set(repo) & set(live)):
        a, b = repo[label], live[label]
        if a == b:
            continue
        keys = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        env_a = (a.get("EnvironmentVariables") or {})
        env_b = (b.get("EnvironmentVariables") or {})
        env_keys = sorted(k for k in set(env_a) | set(env_b) if env_a.get(k) != env_b.get(k))
        if env_keys:
            keys = [k for k in keys if k != "EnvironmentVariables"]
            keys += [f"env:{k}" for k in env_keys]
        drifted.append((label, keys))
    return {"drifted": drifted,
            "live_only": sorted(set(live) - set(repo)),
            "repo_only": sorted(set(repo) - set(live))}


def _load_plists(directory) -> dict:
    import plistlib
    from pathlib import Path as _P
    out = {}
    d = _P(directory)
    if not d.exists():
        return out
    for p in sorted(d.glob(f"{_PREFIX}*.plist")):
        try:
            out[p.stem] = plistlib.loads(p.read_bytes())
        except Exception:  # noqa: BLE001 — битый плист не наша авария
            continue
    return out


def repo_plist_drift(repo_dir=None) -> dict:
    """Studio-only. Сверка копий в `launchd/` с живыми `~/Library/LaunchAgents`.
    Вне Studio — пустой результат: у MacBook свой набор джоб, и сравнивать его
    с этим репозиторием значит краснеть на здоровом.

    Обход НЕ рекурсивный, и это решение (2026-09-07): корень `launchd/` — джобы
    Studio, `launchd/<машина>/` — копии чужих машин, которых здесь и не должно быть
    в живых. Цена ошибки измерена: копия MacBook-джобы `backup-wip`, положенная в
    корень, дала владельцу наутро ложный варн «копия есть, живой джобы нет».
    Периметр ротации логов, наоборот, рекурсивен — логи растут на обеих машинах.
    Расхождение областей закреплено `tests/unit/test_log_rotate.py::
    test_plist_scopes_differ_on_purpose`, мутация в любую сторону роняет его."""
    import socket
    from pathlib import Path as _P
    if in_container():   # живое = рендер шаблонов по построению; копиям сверяться не с чем
        return {"drifted": [], "live_only": [], "repo_only": []}
    if not infra_config.is_primary():
        return {"drifted": [], "live_only": [], "repo_only": []}
    repo_dir = repo_dir or (_P(__file__).parent / "launchd")
    return compare_inventories(_load_plists(repo_dir), _load_plists(agents_dir()))


def _naive_local(ts):
    """Aware-время → наивное местное (плисты launchd — в местном времени машины)."""
    return ts.astimezone().replace(tzinfo=None) if ts.tzinfo else ts


def _fire_of(cal, now):
    """Последний запуск ОДНОГО календарного триггера ≤ now; None — форма не выводится."""
    from datetime import timedelta
    if isinstance(cal, dict) and set(cal) == {"Day", "Hour", "Minute"}:
        # Ежемесячно (консилиум: 1-го числа, 23.09 нить cadence-thresholds). День, которого
        # в месяце нет (31-е в сентябре), launchd не исполняет — такой месяц пропускается.
        y, m = now.year, now.month
        for _ in range(13):
            try:
                t = now.replace(year=y, month=m, day=int(cal["Day"]), hour=int(cal["Hour"]),
                                minute=int(cal["Minute"]), second=0, microsecond=0)
            except ValueError:
                t = None
            if t is not None and t <= now:
                return t
            y, m = (y, m - 1) if m > 1 else (y - 1, 12)
        return None
    if not isinstance(cal, dict) or set(cal) not in ({"Hour", "Minute"},
                                                     {"Weekday", "Hour", "Minute"}):
        return None
    step = timedelta(days=7 if "Weekday" in cal else 1)
    t = now.replace(hour=int(cal["Hour"]), minute=int(cal["Minute"]),
                    second=0, microsecond=0)
    if "Weekday" in cal:
        t -= timedelta(days=(t.weekday() - (int(cal["Weekday"]) - 1) % 7) % 7)
    while t > now:
        t -= step
    return t


def last_scheduled_fire(label: str, now, la_dir=None):
    """Когда задача launchd ДОЛЖНА была стартовать в последний раз (≤ now), по её
    ЖИВОМУ плисту. None — плиста нет или форма расписания здесь не выводится.

    ЗАЧЕМ (нить nightly-liveness, 23.09). Порог «сколько дней молчания — тревога»
    у датчиков живости был литералом рядом с кодом, а ритм задачи — в плисте: два
    дома одного числа, и они расходятся молча, когда задачу переносят. Здесь порога
    нет вовсе: артефакт свеж, если он не старше последнего планового запуска.

    ПОДДЕРЖАНО: триггер {Hour, Minute} (ежедневно), {Weekday, Hour, Minute}
    (еженедельно), {Day, Hour, Minute} (ежемесячно, с 23.09) и СПИСОК таких триггеров
    (колокол: пять раз в день, 23.09 нить schedule-liveness) — берётся самый поздний.
    Остальное (StartInterval, пропущенный ключ = «каждую минуту» по семантике launchd,
    Month) — None, и вызывающий
    обязан сказать «не судимо» вслух, а не принять это за «свежо». Weekday launchd:
    0 и 7 — воскресенье, у Python понедельник = 0.

    Каталог плистов: аргумент, иначе env `HEALTH_LAUNCHAGENTS_DIR` (шов тестов,
    которые зовут датчики без аргументов), иначе ~/Library/LaunchAgents."""
    import os
    p = Path(la_dir or agents_dir()) / f"{label}.plist"
    try:
        import plistlib
        cal = plistlib.loads(p.read_bytes()).get("StartCalendarInterval")
    except Exception:  # noqa: BLE001 — нет/битый плист = не судимо, вызывающий говорит вслух
        return None
    now = _naive_local(now)
    fires = [_fire_of(c, now) for c in (cal if isinstance(cal, list) else [cal])]
    if not fires or any(f is None for f in fires):
        return None
    return max(fires)


def start_interval_s(label: str, la_dir=None):
    """StartInterval задачи launchd в секундах по ЖИВОМУ плисту; None — плиста нет или
    ритм задан не интервалом (23.09, нить first-miss: ротация логов). Для интервальной
    задачи момент последнего запуска не выводится (он отсчитывается от загрузки), зато
    выводится граница пропуска: квитанция старше интервала = пропущен хотя бы один запуск."""
    import os
    p = Path(la_dir or agents_dir()) / f"{label}.plist"
    try:
        import plistlib
        v = plistlib.loads(p.read_bytes()).get("StartInterval")
    except Exception:  # noqa: BLE001 — нет/битый плист = не судимо, вызывающий говорит вслух
        return None
    return int(v) if isinstance(v, int) and v > 0 else None


def artifact_covers_last_fire(label: str, artifact, now, la_dir=None):
    """Покрывает ли след задачи её последний плановый запуск.
    True/False; None — расписание не выводится (см. last_scheduled_fire).

    След — дата (`2026-09-23`) или момент (`2026-09-23T08:00:42`, можно с поясом).
    Дата сравнивается с ДНЁМ запуска, момент — с самим запуском: квитанция, записанная
    в 07:00, не покрывает запуск в 08:00 того же дня. Граница вслух: задача, которая
    ещё бежит, в эти минуты читается как «не покрыто» — проверяющим задачам ставить
    время позже конца прогона (ночной набор 00:00→~00:06, монитор 07:50)."""
    fire = last_scheduled_fire(label, now, la_dir)
    if fire is None:
        return None
    if not artifact:
        return False
    from datetime import date, datetime
    s = str(artifact)
    if len(s) <= 10:
        return date.fromisoformat(s) >= fire.date()
    return _naive_local(datetime.fromisoformat(s)) >= fire

if __name__ == "__main__":
    s = find_stale_plist_env()
    print("\n".join(s) if s else "плисты и загруженные джобы согласованы")
    print("инвентарь:", repo_plist_drift())
