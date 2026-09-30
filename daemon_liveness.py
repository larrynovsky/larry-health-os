#!/usr/bin/env python3.11
"""
daemon_liveness.py — датчик «KeepAlive-демон без живого PID».

Закрывает класс «сервис тихо умер»: health.api крутился в crash-loop 14 дней
(main.py удалён, плист забыли снять), и единственным сигналом был launchctl list,
который никто не читает. Теперь это warn в integrity_tests → triage → Telegram.

Один публичный entry point: find_down_daemons(). Чистый парсер parse_down_daemons
вынесен ради тестируемости (без subprocess/IO).
"""
from __future__ import annotations

import re
import infra_config  # основная машина — данные установки (private/infra.yaml)
from pathlib import Path



def parse_down_daemons(launchctl_text: str, is_keepalive) -> list[str]:
    """Парсер: текст `launchctl list` + fn(label)->bool keepalive →
    метки Health OS и настроенных соседей, которые KeepAlive, но БЕЗ живого PID.

    Колонки launchctl list: PID  STATUS  LABEL (через tab).
    PID == '-' у KeepAlive-демона = не запущен (crash-loop/throttled/упал).
    Шапка ('PID\\tStatus\\tLabel') и чужие лейблы отсеиваются сами.
    """
    prefixes = ("com.larry.health", *(n["label_prefix"] for n in infra_config.NEIGHBORS.values()
                                      if n.get("label_prefix")))
    down = []
    for line in launchctl_text.splitlines():
        parts = line.split("\t") if "\t" in line else line.split()
        if len(parts) < 3:
            continue
        pid, status, label = parts[0], parts[1], parts[2]
        if not label.startswith(prefixes):
            continue
        if pid == "-" and is_keepalive(label):
            down.append(f"{label} (last_exit={status})")
    return down


def has_our_labels(launchctl_text: str) -> bool:
    """Видит ли снимок `launchctl list` хоть одну нашу джобу. Нет — предмета не видно."""
    return any(l.startswith("com.larry.health")
               for line in launchctl_text.splitlines()
               for l in (line.split("\t") if "\t" in line else line.split())[2:3])


class NotJudged(RuntimeError):
    """Датчик не видел предмета: ответ «не судимо», который вызывающий обязан сказать
    вслух. Пустой список тут означал бы «все живы» — ровно та ложь, от которой датчик."""


def _is_keepalive(label: str) -> bool:
    import plist_env_liveness
    p = plist_env_liveness.agents_dir() / f"{label}.plist"
    if not p.exists():
        return False
    return bool(re.search(r"<key>KeepAlive</key>\s*<true/>",
                          p.read_text(errors="ignore")))


# ── Пульс постоянных служб в контейнере (docker-install, этап 2б, 2026-09-29) ─────────
# В контейнере PID службы из соседнего контейнера не виден, а доступ к сокету Докера дал бы
# датчику права root над ВМ (WSTG-CONF): живость доказывает сама служба — раз в минуту
# трогает свой файл пульса в каталоге данных. Это сильнее PID (§14): удар идёт из цикла
# событий службы, а не из самого факта процесса. Метку службе даёт compose
# (HEALTH_SERVICE_LABEL, install.py --docker); натив её не ставит — там живость по PID.
PULSE_LABEL_ENV = "HEALTH_SERVICE_LABEL"
PULSE_EVERY_S = 60        # таймаут, системная механика (§9, п.2)
PULSE_STALE_S = 5 * 60    # пять пропущенных ударов; restart: unless-stopped поднимает быстрее


def pulse_path(label: str, data_dir: str) -> Path:
    return Path(data_dir) / "logs" / "pulse" / f"{label}.pulse"


def beat() -> None:
    """Удар пульса службы. Без метки (натив) — ничего: живость там видна по PID launchd.
    Никогда не бросает: пульс не должен ронять службу, которую стережёт."""
    import os
    label, data = os.environ.get(PULSE_LABEL_ENV), os.environ.get("HEALTH_DATA_DIR")
    if not (label and data):
        return
    try:
        p = pulse_path(label, data)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    except OSError as e:
        import logging
        logging.getLogger(__name__).warning("пульс %s не записан: %s", label, e)


def stale_pulses(services: list[tuple[str, str]], now: float) -> list[str]:
    """Чистое ядро: [(метка, каталог данных)] → находки. «Ни разу не отметилась» и
    «пульс устарел» — разные сообщения (C-44): первое — служба не поднялась вовсе."""
    out = []
    for label, data in services:
        p = pulse_path(label, data)
        if not p.exists():
            out.append(f"{label} (пульса нет ни разу: {p})")
            continue
        age = now - p.stat().st_mtime
        if age > PULSE_STALE_S:
            out.append(f"{label} (пульс {int(age // 60)}м{int(age % 60):02d}с назад, "
                       f"порог {PULSE_STALE_S // 60}м)")
    return out


def _container_services() -> list[tuple[str, str]]:
    """Периметр из носителей (C-36): постоянные службы — плисты с KeepAlive в каталоге
    установки; каталог данных — из плиста каждой службы (C-19: тенантов не один)."""
    import os
    import plistlib
    import plist_env_liveness
    out = []
    for p in sorted(plist_env_liveness.agents_dir().glob("com.larry.health*.plist")):
        try:
            d = plistlib.loads(p.read_bytes())
        except Exception as e:  # noqa: BLE001 — битый плист: громко, но не роняем датчик
            out.append((f"{p.stem} (плист не читается: {e})", "/nonexistent"))
            continue
        if d.get("KeepAlive"):
            data = d.get("EnvironmentVariables", {}).get("HEALTH_DATA_DIR") or os.environ.get("HEALTH_DATA_DIR", "")
            out.append((d.get("Label", p.stem), data))
    return out


def find_down_daemons() -> list[str]:
    """Studio-only. Список упавших KeepAlive-демонов с 2-сэмпл подтверждением
    (исключаем момент рестарта). Вне Studio — [].

    В контейнере (этап 2б) — по пульсу служб (stale_pulses), NotJudged только при пустом
    периметре. ⚰️ 2a: «в контейнере всегда NotJudged — носителя нет» — погашено 2б.

    NotJudged (docker-install, этап 2a, 2026-09-29) там, где пустой список прежде
    читался как «все живы»: в выводе
    `launchctl list` нет ни одной нашей метки — сессия смотрит не в тот домен launchd
    (другой пользователь, sudo, системный домен), и парсер честно не нашёл упавших среди
    ничего. Замер 29.09: по ssh под владельцем на Studio видны все 52 метки — ssh этим
    случаем НЕ является; наблюдённого срабатывания нет, защита от класса, а не от инцидента."""
    import socket
    import plist_env_liveness
    if plist_env_liveness.in_container():
        # Этап 2б: в контейнере живость — по пульсу службы, не по launchctl.
        services = _container_services()
        if not services:
            raise NotJudged("в каталоге плистов контейнера нет ни одной постоянной службы — "
                            "периметр пуст, живость не видна")
        import time as _t
        return stale_pulses(services, _t.time())
    if not infra_config.is_primary():
        return []
    import subprocess
    import time

    def _snap() -> str:
        text = subprocess.run(["launchctl", "list"], capture_output=True,
                              text=True, timeout=10).stdout
        if not has_our_labels(text):
            raise NotJudged("в выводе launchctl list нет ни одной метки com.larry.health — "
                            "не тот домен launchd; живость не видна")
        return text

    down = parse_down_daemons(_snap(), _is_keepalive)
    if down:
        time.sleep(2)
        down = parse_down_daemons(_snap(), _is_keepalive)
    return down


if __name__ == "__main__":
    d = find_down_daemons()
    print("\n".join(d) if d else "все KeepAlive-демоны живы")


# ── Полнота рестарта: кто держит код в ПАМЯТИ и не перезапускается деплоем ────

_HOOK = Path.home() / "health_scripts" / "scripts" / "git-hooks" / "post-commit-macbook"
_REPO_DIR = Path.home() / "health_scripts"


def parse_restarted_labels(hook_src: str) -> set[str]:
    """Лейблы, которые деплой-хук перезапускает — из ТЕКСТА хука, не из памяти.

    Хук перечисляет джобы руками (`launchctl kickstart ... com.larry.health.bot`),
    поэтому список стареет молча: новую долгоживущую джобу в него просто забывают.
    """
    out = set()
    for m in re.findall(r"com\.larry\.[A-Za-z0-9._-]+", hook_src):
        out.add(m[:-6] if m.endswith(".plist") else m)
    return out


def select_memory_resident(plists, repo: str) -> set[str]:
    """KeepAlive-джобы, исполняющие `.py` ИЗ РЕПОЗИТОРИЯ: они держат код в памяти.

    plists: [(label, dict)]. Джоба на `.sh` сюда не входит намеренно — она порождает
    интерпретатор на каждое событие и подхватывает правку без рестарта. `.py` из
    ЧУЖОГО каталога (другой проект) — тоже не наша забота: наш деплой его не трогает.
    """
    out = set()
    for label, d in plists:
        if not d.get("KeepAlive"):
            continue
        args = [a for a in (d.get("ProgramArguments") or []) if isinstance(a, str)]
        if any(a.endswith(".py") and a.startswith(repo) for a in args):
            out.add(label)
    return out


def find_unrestarted_daemons() -> list[str]:
    """Studio-only. Джобы, которые деплой оставит на СТАРОМ коде: держат `.py`
    репозитория в памяти, а хук их не перезапускает.

    Например, процесс держит старый модуль в памяти после обновления файла.
    Список имён, вручную добавленный в хук, не доказывает полноту перезапуска:
    нужен обход всех заданий, исполняющих код репозитория.
    Пустой список = множества сошлись.
    """
    import plistlib
    import socket
    import plist_env_liveness
    if plist_env_liveness.in_container():
        # Класса нет по построению: деплой в контейнере — `compose up`, он пересоздаёт
        # каждую службу, чей образ сменился; перечня «кого рестартить» руками нет.
        return []
    if not infra_config.is_primary():
        return []
    la = plist_env_liveness.agents_dir()
    if not (la.exists() and _HOOK.exists()):
        return []
    plists = []
    for p in sorted(la.glob("*.plist")):
        try:
            d = plistlib.loads(p.read_bytes())
        except Exception:  # noqa: BLE001 — битый чужой плист не наша авария
            continue
        plists.append((d.get("Label", p.stem), d))
    resident = select_memory_resident(plists, str(_REPO_DIR))
    return sorted(resident - parse_restarted_labels(_HOOK.read_text(errors="ignore")))
