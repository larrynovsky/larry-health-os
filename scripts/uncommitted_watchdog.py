#!/usr/bin/env python3.11
"""
uncommitted_watchdog.py — hourly check на ГРЯЗНОЕ ДЕРЕВО ДЕПЛОЙ-ТАРГЕТА (Studio).

Контекст:
  Правило CLAUDE.md §1 («ssh-edit → немедленный commit») надёжно работает только
  если Claude помнит контекст. Инцидент 2026-05-17: Cowork написал Medical
  Record feature (dashboard.py + medical_record.html + import_medical_events.py
  + 7 новых таблиц), session завершилась с compaction, следующая сессия
  обнаружила незакоммиченное только через регрессии в nightly tests.

  Это watchdog: external enforcement правила #1. Не зависит от LLM-attention.

ЧТО ОН СТЕРЕЖЁТ СЕГОДНЯ — не то, подо что строился (уточнено 2026-09-07, §18).
  17.05 код писали на Studio, поэтому «забытый коммит» и «грязный деплой-таргет»
  были одним и тем же деревом. С 2026-05-23 (Sprint 7-GIT) пишет MacBook, Studio
  стал deploy-only — а сторож остался здесь. Он по-прежнему нужен и ловит реальное:
  правку по ssh мимо MacBook, неудавшийся push, конфликт updateInstead — всё, из-за
  чего деплой-таргет расходится с main (инцидент 2026-07-05: 25ч грязи, 1 алерт).
  Но ИСХОДНЫЙ класс — «работу написали и не закоммитили» — с 23.05 живёт на другой
  машине, и здесь его нет. Он покрыт отдельно и с другой стороны:
  `integrity_tests.check_macbook_uncommitted` судит снимок дерева MacBook
  (`refs/backups/*`, пишет `backup.sh` каждые 3ч и пушит сюда). Сторожа на ноутбук
  СОЗНАТЕЛЬНО не ставили: у ноутбука не может быть честного сигнала живости —
  «сторож умер» и «крышка закрыта» изнутри неотличимы.

Логика:
  - Hourly запуск через launchd.
  - git status --porcelain в ~/health_scripts.
  - Если dirty — пишет dirty_since в state-файл.
  - Если dirty >60 мин и alert ещё не был сегодня — отправляет Telegram alert.
  - Если clean — стирает state.

Запуск только на Studio (canonical-only — правило CLAUDE.md §8).
State-файл: logs/uncommitted_watchdog.state (JSON) — рядом с heartbeat (переезд завершён 2026-09-02).
Лог:        ~/health_scripts/logs/uncommitted_watchdog.log
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
SECRETS = Path.home() / ".health_secrets"
# ПЕРЕЕЗД 03.08 БЫЛ ПОЛОВИНЧАТЫМ (замечено 02.09): heartbeat ушёл в logs/, а state
# остался в каталоге секретов. Он пишется с umask по умолчанию (644) и, появляясь,
# краснил бы сенсор прав — при том что норма «в каталоге секретов только секреты»
# записана в самом сенсоре. Токены по-прежнему читаются из SECRETS, состояние — нет.
_LOGS = Path(__file__).resolve().parent.parent / "logs"
STATE_FILE = _LOGS / "uncommitted_watchdog.state"
LOG_FILE = REPO / "logs/uncommitted_watchdog.log"

DIRTY_THRESHOLD_MIN = 60       # минут до первого alert
ALERT_COOLDOWN_HOURS = 4       # деплой-блок — переалерт каждые 4ч (было 24: стойкая
                               # грязь молчала сутки; инцидент 2026-07-05, 25ч dirty/1 alert)
MAX_STATUS_LINES = 8           # сколько строк git status показать в alert


# ── Helpers ──────────────────────────────────────────────────────────────────
def _now() -> datetime:
    return datetime.now(timezone.utc)  # time-inject: ok


def _log(msg: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = _now().isoformat(timespec="seconds")
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{ts} {msg}\n")


def _read_secret(name: str) -> str:
    p = SECRETS / name
    return p.read_text().strip() if p.exists() else ""


def _notify_telegram(text: str) -> bool:
    import notify
    notify.fault("uncommitted_watchdog: automatic recovery failed; updates blocked", person_key=None)
    return True


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        _log(f"state read failed: {e}; treating as empty")
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _clear_state() -> None:
    if STATE_FILE.exists():
        STATE_FILE.unlink()


def _git_status() -> str:
    """Возвращает `git status --porcelain` output. Пустая строка = clean."""
    r = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if r.returncode != 0:
        raise RuntimeError(f"git status failed: {r.stderr.strip()}")
    return r.stdout


# Дом heartbeat — logs/, НЕ каталог секретов (переезд 2026-08-03; задача была
# названа ещё 2026-07-06 в security_sensors и осознанно отложена).
# Причина переезда не косметическая: secret_guard строит «иглы» из ВСЕХ файлов
# каталога секретов, а ISO-timestamp heartbeat проходит порог «есть цифра и
# длина ≥12» и становится иглой. Любой промпт с тем же моментом времени
# посекундно блокировался бы fail-closed. Замер 2026-08-03: игла
# «2026-08-03T18:…» действительно присутствовала.
HEARTBEAT_FILE = REPO / "logs/uncommitted_watchdog.heartbeat"
_HEARTBEAT_LEGACY = SECRETS / "uncommitted_watchdog.heartbeat"   # читается до первого запуска после переезда


def _write_heartbeat() -> None:
    """§14 liveness: отметка каждого запуска. Читается integrity check_watchdog_liveness."""
    try:
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_FILE.write_text(_now().isoformat(), encoding="utf-8")
    except Exception as e:  # silent-ok: heartbeat не должен ронять watchdog
        _log(f"heartbeat write failed: {e}")


def _try_auto_stash() -> bool:
    """§13 ступень 1 — авто-ремонт стойкой грязи Studio: `git stash push -u`. stash СОХРАНЯЕТ
    (envelope c: не теряем уникального, recover через `git stash pop`), поэтому safe без
    предиката «дубль». Вызывается только на dirty >threshold (envelope b: не активная правка).
    True если tree стал чистым."""
    r = subprocess.run(
        ["git", "stash", "push", "-u", "-m", f"auto-watchdog {_now().date()}"],
        cwd=REPO, capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        _log(f"auto-stash failed: {r.stderr.strip()[:200]}")
        return False
    # подтверждаем чистоту
    return not _git_status().strip()


def _format_autofix(status: str, dirty_minutes: int) -> str:
    hours = dirty_minutes // 60
    age = f"{hours}ч {dirty_minutes % 60}м" if hours else f"{dirty_minutes}м"
    return (
        "🔧 Health OS: Studio авто-разблокирован (деплой мог падать)\n"
        f"Studio был грязный {age} → авто-`git stash` (§13 ступень 1), tree чист, "
        f"деплой пойдёт. Ничего делать не нужно.\n"
        "Если это была ТВОЯ незакоммиченная работа на Studio — recover:\n"
        "  cd ~/health_scripts && git stash pop\n"
        "(Studio не редактируется напрямую — §8/protocol; повтор = разбери привычку.)"
    )


def _format_alert(status: str, dirty_minutes: int, dirty_since: datetime) -> str:
    lines = status.strip().splitlines()
    head = lines[:MAX_STATUS_LINES]
    tail = (
        f"\n... и ещё {len(lines) - MAX_STATUS_LINES} файлов"
        if len(lines) > MAX_STATUS_LINES
        else ""
    )
    hours = dirty_minutes // 60
    rem = dirty_minutes % 60
    age = f"{hours}ч {rem}м" if hours else f"{rem}м"
    # Локальное время dirty_since для удобства чтения (UTC → таймзона дома из пакета региона).
    try:
        from zoneinfo import ZoneInfo
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # корень репо: region_pack
        import region_pack
        local = dirty_since.astimezone(ZoneInfo(region_pack.value("timezone", "UTC")))
        since_str = local.strftime("%Y-%m-%d %H:%M %Z")
    except Exception:  # silent-ok: fallback на UTC если zoneinfo недоступен
        since_str = dirty_since.isoformat(timespec="minutes")
    return (
        "🚧🚫 Health OS: Studio dirty — ДЕПЛОЙ ЗАБЛОКИРОВАН\n"
        f"Замечен: {since_str}\n"
        f"Возраст: {age}. Пока не чисто — НИ ОДИН git push не доедет (updateInstead\n"
        f"отвергает грязное дерево); вся работа копится на MacBook недеплоенной,\n"
        f"а «регрессы на Studio» врут зелёным на старом коде (CLAUDE.md §12).\n\n"
        f"git status --porcelain:\n"
        + "\n".join(head)
        + tail
        + "\n\n"
        "Studio НЕ редактируется напрямую (single-writer §8). Разбор:\n"
        "  1) случайная правка/scratch → cd ~/health_scripts && git checkout -- <файлы>\n"
        "  2) правки уже закоммичены на MacBook → git checkout безопасен (дубль)\n"
        "  3) НЕ коммить на Studio (pre-commit отвергнет, нарушает single-writer)"
    )


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> int:
    host = socket.gethostname()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # корень репо: infra_config
    import infra_config  # основная машина — данные установки (private/infra.yaml)
    if not infra_config.is_primary(host):
        sys.exit(
            f"uncommitted_watchdog: запуск только на Studio (текущий: {host}). "
            "CLAUDE.md §8."
        )

    _write_heartbeat()  # §14 liveness — отметка «watchdog запустился»
    try:
        status = _git_status()
    except Exception as e:  # noqa: BLE001
        _log(f"git status error: {e}")
        return 1

    if not status.strip():
        # Clean — стираем state.
        _clear_state()
        _log("clean")
        return 0

    state = _load_state()
    now = _now()
    dirty_since_str = state.get("dirty_since")
    if not dirty_since_str:
        # Первый раз увидели dirty — запоминаем время начала.
        state["dirty_since"] = now.isoformat()
        _save_state(state)
        _log(f"dirty (new): {len(status.strip().splitlines())} files")
        return 0

    try:
        dirty_since = datetime.fromisoformat(dirty_since_str)
    except Exception:  # noqa: BLE001
        dirty_since = now
        state["dirty_since"] = now.isoformat()
        _save_state(state)

    age_min = int((now - dirty_since).total_seconds() // 60)

    if age_min < DIRTY_THRESHOLD_MIN:
        _log(f"dirty ({age_min}m) — under threshold {DIRTY_THRESHOLD_MIN}m")
        return 0

    # §13 ступень 1 — авто-ремонт: стойкая грязь (>threshold) блокирует деплой.
    # stash сохраняет (envelope c — восстановимо), >threshold = не активная правка
    # (b), лог + notify-след (d). Человека тревожим (ниже) ТОЛЬКО если stash не удался.
    if _try_auto_stash():
        _clear_state()
        _log(f"auto-stashed dirty ({age_min}m) — deploy unblocked")
        import notify
        import i18n
        notify.weekly(i18n.t("owner.weekly.unblocked"))
        return 0
    _log(f"auto-stash FAILED (dirty {age_min}m) — эскалирую человеку")

    # Cooldown check.
    last_alert_str = state.get("last_alert_at")
    if last_alert_str:
        try:
            last_alert = datetime.fromisoformat(last_alert_str)
            if now - last_alert < timedelta(hours=ALERT_COOLDOWN_HOURS):
                _log(f"dirty ({age_min}m) — alert cooldown active")
                return 0
        except Exception as e:  # noqa: BLE001
            _log(f"last_alert_at parse failed ({e!r}) — treating as expired")

    # Шлём alert.
    text = _format_alert(status, age_min, dirty_since)
    sent = _notify_telegram(text)
    if sent:
        state["last_alert_at"] = now.isoformat()
        _save_state(state)
        _log(f"alert sent (dirty {age_min}m)")
    else:
        _log(f"alert failed (dirty {age_min}m)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
