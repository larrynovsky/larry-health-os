#!/usr/bin/env python3.11
"""
oura_freshness_check.py — датчик свежести биометрии (Oura/Apple Health).

Переиспользует health_db.check_data_freshness (каноническая conit-проверка по
import-staleness меткам; Oura ≤26ч). Детект свежести в системе уже был
(integrity_tests + check_data_freshness), но НЕ доставлялся ИМЕНОВАННЫМ
алертом: фейл свежести попадает в integrity 'failures', а triage_agent
форвардит в Telegram только 'warnings' → пользователь не узнавал, что именно
Oura встал. Этот модуль закрывает доставку: при устаревании — конкретный алерт.

Принцип проекта: «fallback требует датчика», «тихий сбой обязан громко
сигналить». Один публичный entry point: run_check().
Запуск: ежедневно из run_checks.sh --scheduled (07:50) + вручную с --notify.
"""
import sys
import logging
import i18n
import notify as notifications
import urllib.request
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import health_db as db

log = logging.getLogger(__name__)
import os
from secrets_paths import secrets_dir
_SECRETS = secrets_dir()
BIOMETRIC_SOURCES = ["oura", "apple_health"]


def _alert(text: str) -> None:
    token = (_SECRETS / "telegram_token").read_text().strip()
    chat = (_SECRETS / "telegram_chat_id").read_text().strip()
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    urllib.request.urlopen(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=10
    )


# Алертим не на conit-лимите (он тугой, для in-chat-оговорок), а при РЕАЛЬНОМ
# обрыве: age > limit×MULT. Иначе суточная выгрузка Apple (~24-28ч) флапает
# вокруг 26ч и шлёт ложные алерты ежедневно → усталость от тревог.
ALERT_MULTIPLIER = 2.0


def run_check(notify: bool = False, sources=None,
              multiplier: float = ALERT_MULTIPLIER) -> dict:
    """Возвращает {source: {...}} для ПО-НАСТОЯЩЕМУ оборванных источников
    (age > limit×multiplier — обрыв, а не суточный сдвиг выгрузки).
    notify=True → Telegram-алерт. Пусто = всё в норме."""
    sources = sources or BIOMETRIC_SOURCES
    stale = db.check_data_freshness(sources)
    broken = {s: v for s, v in stale.items()
              if v["age_hours"] > v["limit_hours"] * multiplier}
    if broken:
        lines = [f"• {v['message']}" for v in broken.values()]
        key = "jobs.oura.stale_human.both" if len(broken) > 1 else f"jobs.oura.stale_human.{next(iter(broken))}"
        msg = i18n.t(key, hours=f"{min(v['age_hours'] for v in broken.values()):.0f}")
        log.error("oura_freshness_check BROKEN: %s", broken)
        if notify:
            try:
                notifications.fault("oura_freshness_check: " + "\n".join(lines), person_key=None)
                _alert(msg)
            except Exception as e:  # silent-ok: surfaced via return
                log.error("oura_freshness_check: alert send failed: %s", e)
    elif stale:
        log.info("oura_freshness_check: маргинальный сдвиг (<%sx), без алерта: %s",
                 multiplier, stale)
    else:
        log.info("oura_freshness_check OK: %s свежи", sources)
    return broken


if __name__ == "__main__":
    import json
    report = run_check(notify="--notify" in sys.argv)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report else 0)
