#!/usr/bin/env python3.11
"""secret_guard.py — датчик значений секретов в исходящем тексте (SEC-21, BL-SECRETS-LLM-1).

Одна публичная функция: find_secret_values(text) → list[str] с ИМЕНАМИ файлов-
секретов, чьи значения встретились в тексте. Сами значения никогда не попадают
ни в результат, ни в логи (трипвайр CLAUDE.md §19, 2026-07-06 — иначе датчик сам
стал бы каналом утечки).

Потребитель: doc_agent.analyze_diff — блокирует отправку диффа в Claude API.
Остальные LLM-тракты (hai_*, lab_recognizer-изображения) — кандидаты, подключать
по мере надобности; изображения текстовым сканом не покрываются by design.

Как строятся «иглы» (needles) из файлов secrets_paths.secrets_dir():
  * из содержимого каждого файла (≤64KB) берутся токены [A-Za-z0-9_./+=:@-]{12,};
  * токен становится иглой, если (есть цифра И длина ≥12) ИЛИ длина ≥24 —
    отсекает словарные ключи JSON («refresh_token») и короткие идентификаторы;
  * следствие: telegram_chat_id (9–10 цифр) НЕ ловится — ниже порога; принято:
    chat_id низкочувствителен и вездесущ в логах, порог ниже = шторм ложняков.

Fail-closed: если каталог секретов не читается или скан упал — возвращается
маркер «!secret_guard не отработал…»; вызывающий обязан трактовать ЛЮБОЙ
непустой результат как блок отправки. Слепой гард не пропускает (симметрия
с primary_guard и «нераспарсили tailscale = красный»).
"""

from __future__ import annotations

import re
from pathlib import Path

from secrets_paths import secrets_dir

_TOKEN_RE = re.compile(r"[A-Za-z0-9_./+=:@-]{12,}")
_MAX_FILE_BYTES = 64 * 1024

# Отсечка «иглы, которая не секрет» (2026-08-03). Дата-время проходит порог
# «есть цифра и длина ≥12», но секретом не является: она встречается в
# expiry-полях токенов и в любых служебных отметках. Игла-дата означала бы, что
# промпт с тем же моментом времени посекундно блокируется fail-closed — отказ
# без объяснимой для человека причины. Замер того дня: две такие иглы из 17
# (`google_calendar_token.json` expiry и heartbeat вотчдога, теперь переехавший).
# Порог остаётся прежним: отсекается ФОРМА, а не длина.
_DATEISH_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$")


def _needles_from_file(f: Path) -> set[str] | None:
    """Иглы файла; None — файл НЕ ПРОСКАНИРОВАН (слепота, а не «чисто»).

    До 2026-10-01 большой файл и ошибка чтения давали пустой набор — тот же
    ответ, что «в файле нет игл»: секрет из такого файла уходил молча.
    Замер перед переводом в fail-closed (01.10, Studio): в каталогах секретов
    владельца (15 файлов) и партнёра (7) нет ни одного файла больше 60 КБ —
    блок не сработает на живых данных."""
    try:
        if f.stat().st_size > _MAX_FILE_BYTES:
            return None
        text = f.read_text(errors="ignore")
    except OSError:
        return None
    out = set()
    for tok in _TOKEN_RE.findall(text):
        if _DATEISH_RE.match(tok):
            continue                      # дата — не секрет, см. _DATEISH_RE
        if (any(c.isdigit() for c in tok) and len(tok) >= 12) or len(tok) >= 24:
            out.add(tok)
    return out


def find_secret_values(text: str, dirs: list[Path] | None = None) -> list[str]:
    """Имена секрет-файлов, чьи значения найдены в text. [] = чисто.

    dirs — только для инъекции в тестах; в проде — secrets_dir() тенанта.
    Никогда не бросает: сбой → маркер-находка (fail-closed у вызывающего).
    """
    try:
        scan_dirs = dirs if dirs is not None else [secrets_dir()]
        hits: set[str] = set()
        for d in scan_dirs:
            if not d.is_dir():
                # Каталог секретов обязан существовать — его отсутствие
                # означает, что гард слеп. Кричим, не молчим.
                hits.add(f"!secret_guard не отработал: нет каталога {d.name}")
                continue
            for f in sorted(d.iterdir()):
                if not f.is_file():
                    continue
                needles = _needles_from_file(f)
                if needles is None:
                    hits.add(f"!secret_guard не отработал: {d.name}/{f.name} не прочитан "
                             f"(больше {_MAX_FILE_BYTES // 1024} КБ или ошибка чтения)")
                    continue
                for needle in needles:
                    if needle in text:
                        hits.add(f"{d.name}/{f.name}")
                        break
        return sorted(hits)
    except Exception as e:  # noqa: BLE001 — слепой гард блокирует, не пропускает
        return [f"!secret_guard не отработал: {type(e).__name__}: {e}"]
