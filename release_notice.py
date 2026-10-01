"""release_notice.py — сказать человеку, что вышла новая версия Health OS (решение владельца 01.10).

Кто ставил систему из выпуска, раньше не узнавал об обновлениях никак: проверять GitHub руками
он не станет. Раз в сутки (пьедестал — доставка утреннего брифа в jobs/scheduled) модуль читает
`releases/latest` открытого репозитория и, если там версия новее той, что в образе, отдаёт текст
уведомления — один раз на версию (маркер в data-каталоге тенанта, как у food_quarterly).

Свою версию образ знает из HEALTH_RELEASE: её кладёт release.yml при сборке выпуска. Сборки не из
выпуска (Studio владельца из HEAD, урок установки в CI) переменной не имеют — проверки там нет,
и это намеренно: сравнивать не с чем, а владелец обновляется деплоем.

Чего НЕ делает: не обновляет (кнопка «Обновить» — второй шаг, через помощника на хосте; у бота
нет права управлять Докером и не будет). Сеть недоступна или GitHub ответил не так — молчит с
записью в лог: пропущенное уведомление придёт завтра, а тревога о нём человеку не нужна.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

REPO = "larrynovsky/larry-health-os"
_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
# Команда обновления — та же, что печатает install.sh в конце установки (каталог по умолчанию).
UPDATE_COMMAND = "cd ~/health-docker && bash install.sh"


def _version(tag: str | None) -> tuple[int, int, int] | None:
    """'v1.2.3' → (1, 2, 3); всё прочее (пусто, 'ci', 'v1.2') → None. Граница доверия: тег из сети."""
    m = _TAG.match((tag or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def _marker() -> Path:
    base = Path(os.environ.get("HEALTH_DATA_DIR") or (Path.home() / "health"))
    return base / "data" / "release_notice_last.txt"


def _fetch_latest(timeout: float = 10) -> dict:
    req = urllib.request.Request(_LATEST, headers={"User-Agent": "health-os",
                                                    "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def pending(current: str | None = None, fetch=_fetch_latest) -> dict | None:
    """{'current', 'latest', 'url', 'command'}, если вышла версия новее установленной и о ней ещё не говорили;
    иначе None. Не бросает: вызывающий — ежедневная доставка брифа."""
    current = current if current is not None else os.environ.get("HEALTH_RELEASE", "")
    cur = _version(current)
    if cur is None:
        return None                       # сборка не из выпуска — сравнивать не с чем
    try:
        data = fetch()
    except Exception as e:  # noqa: BLE001 — сеть/GitHub: молчим, завтра попробуем снова
        log.info("release_notice: GitHub не ответил (%s) — проверю завтра", type(e).__name__)
        return None
    tag = data.get("tag_name") if isinstance(data, dict) else None
    new = _version(tag)
    if new is None or new <= cur:
        return None
    m = _marker()
    if m.exists() and m.read_text().strip() == tag:
        return None                       # об этой версии уже сказали
    url = data.get("html_url") or f"https://github.com/{REPO}/releases/tag/{tag}"
    if not str(url).startswith(f"https://github.com/{REPO}/"):
        url = f"https://github.com/{REPO}/releases/tag/{tag}"   # ссылку из сети не верим вслепую
    return {"current": current.strip(), "latest": tag, "url": url, "command": UPDATE_COMMAND}


def mark_told(tag: str) -> None:
    """Зовётся ПОСЛЕ успешной отправки: не дошло — завтра скажем снова."""
    m = _marker()
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(tag)
