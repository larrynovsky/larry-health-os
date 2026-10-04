"""infra_config.py — единый источник сетевых адресов Studio (audit 2026-06-17).

Раньше эти значения дублировались по dashboard.py / jobs / handlers.
Немедицинская инфраструктура. В перспективе часть можно получать у системы
(tailscale status), пока — явные константы в одном месте.
"""
from pathlib import Path as _Path

import yaml as _yaml

# Значения адресов — в private/infra.yaml (приватная зона, в открытый репозиторий не едет;
# вынесено 2026-09-23, нить pii-scrub). Здесь — единственный читатель. Файла нет (публичный
# клон) → loopback: новая установка сперва работает локально, а не стучится в чужой узел.
_PRIV = _Path(__file__).resolve().parent / "private" / "infra.yaml"
_D = (_yaml.safe_load(_PRIV.read_text(encoding="utf-8")) or {}) if _PRIV.exists() else {}


def _neighbors(value) -> dict:
    """Соседние проекты установки. Пути db/lessons — относительно path либо абсолютные.
    Нет настройки → пустой периметр. Битая настройка — отказ, а не потеря соседа.
    Управляющие символы запрещены: shell-читатель передаёт пути строками с табуляцией."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("neighbors: требуется mapping")
    out = {}
    for key, spec in value.items():
        if not isinstance(key, str) or not key or not isinstance(spec, dict):
            raise ValueError("neighbors: требуется имя и mapping полей")
        if not spec.get("path"):
            raise ValueError("neighbors: требуется path")
        if set(spec) - {"path", "db", "secrets", "lessons", "label_prefix"}:
            raise ValueError("neighbors: неизвестное поле")
        if any(not isinstance(v, str) or not v or any(c in v for c in "\0\r\n\t")
               for v in spec.values()):
            raise ValueError("neighbors: поля должны быть непустыми строками без управляющих символов")
        root = _Path(spec["path"]).expanduser()
        if not root.is_absolute():
            raise ValueError("neighbors: path должен быть абсолютным или начинаться с ~/")
        n = dict(spec, path=root)
        for field in ("db", "secrets", "lessons"):
            if field in spec:
                n[field] = root / _Path(spec[field]).expanduser()
        if n.get("lessons") and n["lessons"].name != "lessons.yaml":
            raise ValueError("neighbors: дом уроков должен называться lessons.yaml")
        out[key] = n
    return out


# Единственный дом имён/путей соседей. Не настроены → потребителям нечего обходить.
# Бэкапы: <path>/backups/<db.stem>_<дата>.db; паритет: <path>/scripts/lessons.py.
NEIGHBORS = _neighbors(_D.get("neighbors"))
# Кодекс для ревьюера ночного ремонта (night_repair.py). Нет настройки — codex из PATH.
CODEX_BIN = _D.get("codex_bin", "codex")

# Основная машина установки — единственная, где пишется БД и бегут фоновые сервисы
# (single-primary, CLAUDE.md §8). Имя хоста — ДАННЫЕ установки (private/infra.yaml,
# пишет установщик), а не литерал в коде: до 2026-09-23 имя хоста владельца стоял в ~25 местах,
# и чужая машина не могла стать основной (проба чистого клона, нить onboarding).
# Нет ключа → основной машины нет: запись в БД невозможна, и get_conn говорит почему.
PRIMARY_HOST = _D.get("primary_host")

# Облачная папка установки: у владельца — iCloud Drive/health, куда он сам кладёт бланки (CR/),
# выгрузки весов, откуда импорт берёт архив Health Auto Export и куда зеркалятся отчёты.
# Настройка установки (private/infra.yaml: cloud_health_dir), а не путь в коде: до 2026-09-24
# литерал жил в 27 файлах (BL-PUB-12, нить publication-tails) — 27 копий одного значения.
# Ключа нет (новая установка) → None; читатели берут cloud_dir(), который тогда ведёт в каталог
# данных процесса — входящие у такой установки приходят через бота, облака нет.
# HEALTH_CLOUD_DIR в окружении перекрывает ключ (28.09, нить icloud-test-guard): тесты уводят
# облако в каталог прогона, и переменная, в отличие от подмены атрибута, доезжает до ДОЧЕРНИХ
# процессов. В штатной работе её не задаёт никто — поведение установки не меняется.
import os as _os
_cloud = _os.environ.get("HEALTH_CLOUD_DIR") or _D.get("cloud_health_dir")
CLOUD_HEALTH_DIR = _Path(_cloud).expanduser() if _cloud else None

# Контейнер приложения Health Auto Export в iCloud — путь САМОГО приложения (одинаков у любого
# его пользователя на macOS), не владельца. Живёт здесь, чтобы литерал облака был в одном доме;
# живая папка — именно эта, не .../health (грабли C-32).
HAE_APP_DIR = _Path.home() / "Library/Mobile Documents/iCloud~com~ifunography~HealthExport/Documents/Health"


def cloud_dir(*parts: str) -> _Path:
    """Путь внутри облачной папки установки. Нет облака → тот же путь в каталоге данных
    процесса (HEALTH_DATA_DIR, иначе ~/health): никогда не в чужом тенанте, и читатель входящих
    находит там пустоту, а писатель отчёта — каталог своих данных."""
    import os
    base = CLOUD_HEALTH_DIR or _Path(os.environ.get("HEALTH_DATA_DIR") or _Path.home() / "health")
    return base.joinpath(*parts)


# Куда основной пользователь зеркалит производные .md конституций (источник — БД, замысел
# constitutions.db_as_source). Явный ключ constitutions_mirror, иначе <облако>/constitutions,
# иначе None → экспорт в каталог данных (generate_constitutions._export_dir).
_mirror = _D.get("constitutions_mirror")
CONSTITUTIONS_MIRROR = (_Path(_mirror).expanduser() if _mirror
                        else CLOUD_HEALTH_DIR / "constitutions" if CLOUD_HEALTH_DIR else None)


def is_primary(hostname: str | None = None) -> bool:
    """Эта машина — основная? Сравнение терпит суффикс .local (macOS отдаёт то одно, то другое)."""
    if not PRIMARY_HOST:
        return False
    import socket
    h = hostname if hostname is not None else socket.gethostname()
    return h.removesuffix(".local") == str(PRIMARY_HOST).removesuffix(".local")


STUDIO_HOST = _D.get("studio_host", "127.0.0.1")   # Tailscale-интерфейс Studio (bind дашборда)
STUDIO_SSH = _D.get("studio_ssh", "localhost")      # ssh-цель Studio для скриптов MacBook
DASHBOARD_PORT = 8001            # HTMX Health Dashboard
FUNNEL_URL = f"https://{_D.get('tailnet_hostname', 'localhost')}"  # ⚠ имя историческое: :443 = tailnet-only Serve, НЕ Funnel (SEC-20)

# ── Ожидаемая Tailscale-экспозиция (SEC-20, 2026-07-06) ──────────────────────
# ЕДИНСТВЕННАЯ декларация. Доки (ARCH_SNAPSHOT §УЗЛЫ, SECURITY.md, ~/.infrastructure.md)
# ссылаются сюда и не повторяют значения (feedback_duplicate_value_splitbrain).
# Датчик: security_sensors._tailscale_exposure ↔ live `tailscale serve status --json`.
TAILSCALE_BIN = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
EXPECTED_FUNNEL_PORTS = {10000}          # единственная публичная точка: gateway соседнего проекта
EXPECTED_FUNNEL_HANDLERS = {             # публичная поверхность — сверка строгая
    10000: {"/": "http://127.0.0.1:9001"},
}
EXPECTED_SERVE_PORTS = {443, 5232, 8443, 10000}  # другие host:port в Serve = дрейф
EXPECTED_SERVE_BACKEND_PORTS = {3000, 5001, 5002, 5232, 8003, 9001}  # только loopback-бекенды
# 5232 добавлен 2026-10-03 (решение владельца, нить serve-exposure): CalDAV (Radicale в контейнере)
#   для Напоминаний на телефоне после переезда в контейнер 30.09 — tailnet-only Serve :5232 →
#   127.0.0.1:5232, пароль хешем (bcrypt). Датчик четыре ночи звал его «дрейфом».
# Проброс TCP без TLS (секция TCP → TCPForward): судится отдельно, раньше не судился вовсе.
EXPECTED_TCP_FORWARDS = {8001: "127.0.0.1:8001"}  # дашборд и приём Apple Health (docker-install, шаг 5)
# 8003 добавлен 2026-09-26 (решение владельца, решение в соседнем проекте):
#   веб-страница соседнего проекта, tailnet-only Serve :8443/cost → 127.0.0.1:8003 (stdlib http.server
#   на каталоге <каталог соседнего проекта>, launchd <метка соседнего проекта>).
# 8443 добавлен 2026-09-21 (решение владельца): релей соседнего проекта,
# tailnet-only Serve с 2026-09-17 — датчик кричал «дрейф» четыре ночи, карта молчала.
#   / → 127.0.0.1:3000  релей (colima, контейнер соседа)
#   /pair → 127.0.0.1:5002  релей спаривания мобильного клиента (контейнер соседа);
#   5000 не взят: занят ControlCenter/AirPlay. Канон узла: ~/.infrastructure.md, addendum 2026-09-17/21;
#   план — план соседнего проекта.
# 8000 удалён 2026-07-06: миниапп убит решением владельца (TD-09 закрыт),
# маршрут / снят из Serve. Появление 8000 снова = дрейф, датчик закричит.


if __name__ == "__main__":
    # Для shell-сайтов (run_checks.sh, git-хуки): `python3 infra_config.py is-primary` → 0/1.
    import sys
    # Необязательный второй аргумент — имя хоста (хук передаёт вывод `hostname`).
    if sys.argv[1:2] == ["is-primary"] and len(sys.argv) <= 3:
        sys.exit(0 if is_primary(sys.argv[2] if len(sys.argv) == 3 else None) else 1)
    sys.exit(f"использование: {sys.argv[0]} is-primary [hostname]")
