#!/usr/bin/env python3
"""Сторож свежести данных Apple Health: метка last_import_apple_health стоит дольше
conit-лимита → служебная тревога оператору, не чаще раза в 12 ч.

Зачем: контур HAE → REST /hae/ingest может быть жив, а iOS перестать отправлять; метка
перестаёт двигаться, и раньше система молчала (прецедент: обрыв 2,5 месяца).

Перенесён в репозиторий 2026-09-29 из ~/health_ops (нить docker-install, вариант А владельца):
вне репозитория сторож читал нативную базу по зашитому пути и после переезда базы в контейнер
смотрел бы на замороженную копию. Отличия от прежней версии: состояние антиспама лежит рядом с
базой (едет с ней в том), лимит в тексте тревоги берётся из get_conit_limit, а не зашит «30ч».

Канал: notify.notify_operator (служебный, без мед-данных).
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

SOURCE = "apple_health"
ALERT_COOLDOWN_S = 12 * 3600


def _state_path() -> Path:
    import health_db
    return Path(health_db.DB_PATH).parent / "import_watchdog_state.json"


def _last_alert(state: Path) -> float:
    if not state.exists():
        return 0.0
    try:
        return float(json.loads(state.read_text(encoding="utf-8")).get("last_alert", 0))
    except (ValueError, OSError, AttributeError) as e:
        log.warning("import_watchdog: состояние %s не читается (%s) — считаю, что тревог не было", state, e)
        return 0.0


def main(now: float | None = None) -> int:
    """0 — свежо или тревога уже была за 12 ч; 1 — тревога отправлена."""
    import health_db  # noqa: F401 — сначала health_db: иначе циклический импорт
    import import_status_db
    from notify import notify_operator

    is_stale, age_h = import_status_db.get_import_staleness(SOURCE)
    if not is_stale:
        print(f"ok: {SOURCE} свеж (возраст метки {age_h}ч)")
        return 0
    now = time.time() if now is None else now
    state = _state_path()
    if now - _last_alert(state) < ALERT_COOLDOWN_S:
        print(f"stale ({age_h}ч), но тревога была <12ч назад — молчим")
        return 0
    limit = import_status_db.get_conit_limit(SOURCE)
    via = notify_operator(
        f"⚠️ {SOURCE}: нет свежих данных {age_h:.0f}ч (лимит {limit:.0f}ч). "
        f"Контур HAE→REST молчит. Лечение: открыть HAE на телефоне один раз; "
        f"проверить расписание автоматизации."
    )
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"last_alert": now, "age_h": age_h}), encoding="utf-8")
    print(f"alert sent via {via}, age={age_h}ч")
    return 1


if __name__ == "__main__":
    if "--test" in sys.argv:
        from notify import notify_operator
        print("test:", notify_operator("✅ тест сторожа apple_health (import_watchdog): канал тревог работает."))
        sys.exit(0)
    sys.exit(main())
