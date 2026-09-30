#!/usr/bin/env python3.11
"""
hae_checker.py — сканер новых метрик Apple Health Export.

Один модуль, одна функция — сверить пришедшие метрики с реестром: judge_payload(path) на
REST-приёме (26.09; судит по поведению разборщика, см. ниже). Прежний вход run_check
(скан iCloud-каталога) снят 26.09 — каталог пуст с перехода на REST 06.07.

КРИТИЧНО: если файл не открылся (EDEADLK / evicted) → выходим без
изменений реестра. Не алертим на «все метрики пропали».
"""
# INTENT: device_metrics_owner — у каждой метрики прибора есть хозяин.
from __future__ import annotations

import errno as _errno
import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

import infra_config   # дом облачного пути и папки приложения HAE (BL-PUB-12)
HAE_DIR = infra_config.HAE_APP_DIR
ARCHIVE_DIR = infra_config.cloud_dir("data", "hae_archive")


@dataclass
class MetricInfo:
    name:    str
    count:   int
    unit:    str
    samples: list = field(default_factory=list)
    first_date: str | None = None   # покрытие данных: самая ранняя дата записи (вариант A)
    last_date:  str | None = None   # покрытие данных: самая поздняя дата записи


# ── Сканирование ──────────────────────────────────────────────────────────────

def _scan_hae_file(path: Path) -> dict[str, MetricInfo] | None:
    """
    Открывает HAE-файл и возвращает все метрики с data > 0 записей.

    Возвращает None если файл недоступен (iCloud placeholder / EDEADLK).
    None — явный сигнал «не знаем», отличный от {} («файл пустой»).

    Guard EDEADLK: пробует brctl download + retry один раз.
    """
    try:
        return _parse_hae(path)
    except OSError as e:
        if e.errno == _errno.EDEADLK:
            log.info(f"scan_hae_file: EDEADLK {path.name}, пробуем brctl download")
            try:
                subprocess.run(["brctl", "download", str(path)],
                               capture_output=True, timeout=60)
                time.sleep(3)
                return _parse_hae(path)
            except OSError as e2:
                log.warning(f"scan_hae_file: файл недоступен после download: {e2}")
                return None
        log.warning(f"scan_hae_file: OSError {path.name}: {e}")
        return None
    except Exception as e:
        log.error(f"scan_hae_file: неожиданная ошибка {path.name}: {e}")
        return None


def _parse_hae(path: Path) -> dict[str, MetricInfo]:
    """Парсит HAE JSON, возвращает MetricInfo для каждой метрики с данными."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    result: dict[str, MetricInfo] = {}
    metrics = data.get("data", {}).get("metrics", [])

    for m in metrics:
        name    = m.get("name", "")
        entries = m.get("data") or []
        unit    = m.get("unit", "")

        if not name or not entries:
            continue  # Guard: пустые метрики не регистрируем

        # Берём 3 репрезентативных значения — не весь поток
        samples = []
        step = max(1, len(entries) // 3)
        for i in range(0, min(len(entries), 3 * step), step):
            e = entries[i]
            v = e.get("qty") or e.get("value") or e.get("Avg")
            if v is not None:
                try:
                    samples.append(round(float(v), 3))
                except (TypeError, ValueError):
                    pass

        # Покрытие данных (вариант A): min/max дата записей. Дата HAE вида
        # "2026-03-22 07:45:00 +0200" → берём первые 10 символов YYYY-MM-DD.
        dates = [e["date"][:10] for e in entries
                 if isinstance(e.get("date"), str) and len(e["date"]) >= 10
                 and e["date"][4] == "-" and e["date"][7] == "-"]
        first_date = min(dates) if dates else None
        last_date  = max(dates) if dates else None

        result[name] = MetricInfo(name=name, count=len(entries), unit=unit,
                                  samples=samples, first_date=first_date,
                                  last_date=last_date)

    return result


# ── Хозяин у каждой прибывшей метрики (26.09) ─────────────────────────────────
#
# Реестр — КОПИЯ знания «что разборщик умеет»; первичный источник — сам разборщик
# (import_apple_health.aggregate_metric_by_day). Копия отстала: blood_pressure с апреля
# стоял «tracked, см. systolic/diastolic (handled)», а раздельные имена не пришли ни разу —
# давление терялось молча. Поэтому статус судится ПОВЕДЕНИЕМ разборщика на настоящих
# записях, а не пометкой. Решение «не берём» — явная строка notes «не берём: <причина>»;
# «; покрыто: <колонка>» — утверждение, которое проверяется: колонка обязана иметь
# значение в каждый день, когда метрика пришла.

DECISION_PREFIX = "не берём:"


def _produced_keys(name: str, entries: list) -> set[str]:
    """Какие ключи дня настоящий разборщик кладёт для этих записей И база хранит.

    Кладёт, но база отбрасывает (замер 26.09: heart_rate, flights, daylight… с июня) —
    та же потеря, что и «не разбирает», поэтому пересечение с домом ключей metrics_db."""
    from import_apple_health import aggregate_metric_by_day
    from metrics_db import APPLE_RAW_KEYS, APPLE_BIO_INPUTS
    daily: dict = {}
    aggregate_metric_by_day(name, entries, daily)
    return {k for b in daily.values() for k in b} & (APPLE_RAW_KEYS | APPLE_BIO_INPUTS)


def _covered_col(notes: str | None) -> str | None:
    import re
    m = re.search(r"покрыто:\s*([a-z0-9_]+)", notes or "")
    return m.group(1) if m else None


def _uncovered_dates(col: str, dates: set[str]) -> list[str]:
    """Даты прихода, в которые колонка-«покрытие» пуста (утверждение notes ложно)."""
    import health_db as db
    if not dates or col not in db.metric_columns():
        return sorted(dates) or ["<нет колонки>"]
    ph = ",".join("?" * len(dates))
    with db.get_conn() as conn:
        have = {r[0] for r in conn.execute(
            f'SELECT date FROM daily_metrics WHERE date IN ({ph}) AND "{col}" IS NOT NULL',
            tuple(dates))}
    return sorted(dates - have)


def _load_payload(path: Path) -> dict:
    """Сырой HAE-файл: свежий .json или сжатый архивный .json.gz (compress_raw_archive)."""
    import gzip
    p = Path(path)
    opener = gzip.open if p.suffix == ".gz" else open
    with opener(p, "rt", encoding="utf-8") as f:
        return json.load(f)


def raw_archive_files(rest_dir: Path) -> list[Path]:
    """Все сырые файлы архива по времени выгрузки — и свежие, и сжатые (один дом глоба)."""
    return sorted(list(Path(rest_dir).glob("HealthAutoExport-REST-*.json"))
                  + list(Path(rest_dir).glob("HealthAutoExport-REST-*.json.gz")),
                  key=lambda p: p.name.split("-REST-")[1])


def compress_raw_archive(rest_dir: Path, keep_days: int = 14, today=None) -> dict:
    """Сжимает сырые выгрузки старше keep_days (по времени в имени) в .json.gz НА МЕСТЕ.

    Решение 26.09: архив рос без предела (2 ГБ за 2,5 месяца), а от его ГЛУБИНЫ зависит
    правило «пустая колонка = прибора нет» (integrity_tests._hae_device_absent). Удаление
    ослабило бы правило молча; сжатие сохраняет глубину (JSON жмётся ~10×). Оригинал
    удаляется только после того, как сжатая копия прочитана и разобрана тем же загрузчиком.
    """
    import gzip
    import os
    from datetime import timedelta
    from _time_inject import get_today
    cutoff = ((today or get_today()) - timedelta(days=keep_days)).strftime("%Y%m%d")
    done, freed = 0, 0
    for p in sorted(Path(rest_dir).glob("HealthAutoExport-REST-*.json")):
        if p.name.split("-REST-")[1][:8] >= cutoff:
            continue
        gz = p.with_name(p.name + ".gz")
        tmp = gz.with_name(gz.name + ".tmp")
        with open(p, "rb") as src, gzip.open(tmp, "wb") as dst:
            dst.write(src.read())
        with gzip.open(tmp, "rt", encoding="utf-8") as f:     # проверка ДО удаления оригинала
            json.load(f)
        os.replace(tmp, gz)
        freed += p.stat().st_size - gz.stat().st_size
        p.unlink()
        done += 1
    return {"compressed": done, "freed_mb": round(freed / 1e6, 1)}


def judge_payload(path: Path) -> list[str]:
    """Сверяет метрики пришедшего HAE-файла с реестром ПО ПОВЕДЕНИЮ разборщика.

    Итоговый статус: handled — разборщик что-то кладёт; tracked — не кладёт, но есть
    решение «не берём:» и его «покрыто:» (если заявлено) правда; unowned — не кладёт и
    решения нет / решение ложно; new — метрики нет в реестре. Возвращает имена
    unowned+new (ночной триаж кричит по реестру, не по возврату). Покрытие дат (first/last_seen)
    расширяется монотонно (hae_db.upsert_hae_metric).
    """
    import health_db as db
    metrics = _load_payload(path).get("data", {}).get("metrics", [])
    registry = db.get_hae_registry()
    loud: list[str] = []
    for m in metrics:
        name, entries = m.get("name", ""), m.get("data") or []
        if not name or not entries:
            continue
        dates = {e["date"][:10] for e in entries if isinstance(e.get("date"), str)}
        row = registry.get(name)
        notes = (row or {}).get("notes") or ""
        if _produced_keys(name, entries):
            status = "handled"
        elif row is None:
            status = "new"
        elif notes.startswith(DECISION_PREFIX) and not (
                _covered_col(notes) and _uncovered_dates(_covered_col(notes), dates)):
            status = "tracked"
        else:
            status = "unowned"
        db.upsert_hae_metric(
            name, status=status if (row or {}).get("status") != status else None,
            unit=m.get("units") or m.get("unit") or None,
            sample_values=None if row else [],
            data_first=min(dates) if dates else None,
            data_last=max(dates) if dates else None)
        if status in ("new", "unowned"):
            loud.append(name)
    return loud


# ⚰ 26.09: run_check (скан iCloud-каталога HAE → «новые метрики») и _find_new_metrics сняты.
# Каталог пуст с 06.07 (переход на REST), и задача 2,5 месяца отвечала «новых метрик нет».
# Вход один — judge_payload на REST-приёме; доставка — ночной check_hae_arrivals_have_owner.
# _scan_hae_file/_parse_hae остаются: их читает разовый backfill_hae_coverage.py по истории.
