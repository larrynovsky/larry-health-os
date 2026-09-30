#!/usr/bin/env python3.11
"""Разовый бэкфилл ПОКРЫТИЯ ДАННЫХ (вариант A) в hae_metric_registry.

Сканирует ВСЕ HAE-файлы (необработанные HAE_DIR + архив ARCHIVE_DIR), считает
истинные min/max даты по каждой метрике и МОНОТОННО доливает покрытие в реестр
через upsert_hae_metric (first только раньше, last только позже — не сужает;
B-загрязнённый run-date first_seen merge двигает к истинной ранней дате).

Трогает ТОЛЬКО метрики, уже присутствующие в реестре; метрики из скана, которых
в реестре нет, оставляет alert-потоку (не вставляет молча).

По умолчанию dry-run (без записи). --write: бэкап health.db → merge.
Запуск на Studio (get_conn Studio-guard). Идемпотентно (merge устойчив к повтору).
"""
from _time_inject import get_now  # seam
import argparse
import logging
import shutil
import sys
from datetime import datetime

import health_db as db
import hae_checker as hc

log = logging.getLogger("backfill_hae_coverage")


def scan_all():
    """Пройти все HAE-файлы, вернуть {name: [first, last, files, entries]}."""
    files = (sorted(hc.HAE_DIR.glob("HealthAutoExport-*.json"))
             + sorted(hc.ARCHIVE_DIR.glob("*.json"))
              # Годовые экспорты в timestamped-папках могут предшествовать
              # суточному архиву. Их тоже учитываем, иначе first_seen будет
              # слишком поздним даже при наличии более старых исходных данных.
             + sorted(hc.ARCHIVE_DIR.parent.parent.glob("HealthAutoExport_*/*.json")))
    cov = {}
    unreadable = 0
    for f in files:
        scanned = hc._scan_hae_file(f)
        if scanned is None:
            unreadable += 1
            continue
        for name, info in scanned.items():
            if info.first_date is None:
                continue
            if name not in cov:
                cov[name] = [info.first_date, info.last_date, 0, 0]
            cov[name][0] = min(cov[name][0], info.first_date)
            cov[name][1] = max(cov[name][1], info.last_date)
            cov[name][2] += 1
            cov[name][3] += info.count
    return cov, len(files), unreadable


def run(do_write):
    cov, nfiles, unreadable = scan_all()
    log.info(f"файлов: {nfiles} (нечитаемых: {unreadable}) | метрик с датами: {len(cov)}")

    registry = db.get_hae_registry()
    in_reg = [n for n in sorted(cov) if n in registry]
    not_in_reg = [n for n in sorted(cov) if n not in registry]
    log.info(f"в реестре: {len(in_reg)} | в скане, но НЕ в реестре "
             f"(оставляем alert-потоку): {len(not_in_reg)}")
    if not_in_reg:
        log.info(f"  не в реестре: {not_in_reg}")

    changes = []
    for name in in_reg:
        sf, sl = cov[name][0], cov[name][1]
        cf = registry[name].get("first_seen") or None
        cl = registry[name].get("last_seen") or None
        new_first = sf if (cf is None or sf < cf) else cf
        new_last = sl if (cl is None or sl > cl) else cl
        if (new_first, new_last) != (cf, cl):
            changes.append((name, cf, cl, new_first, new_last, cov[name][2], cov[name][3]))

    log.info(f"[plan] изменится строк: {len(changes)} из {len(in_reg)} | "
             f"без изменений: {len(in_reg) - len(changes)}")
    for name, cf, cl, nf, nl, files_, entries_ in changes:
        log.info(f"  {name}: [{cf}..{cl}] -> [{nf}..{nl}]  (файлов {files_}, записей {entries_})")

    if not do_write:
        log.info("DRY-RUN: бэкапа нет, записи нет.")
        return

    src = db.DB_PATH  # канонический путь (R1/R2: не реконструировать iCloud-путь руками)
    ts = get_now().strftime("%Y%m%d_%H%M%S")
    bak = src.with_name(f"health.db.preop_haecov_{ts}")
    shutil.copy2(src, bak)
    log.info(f"backup: {bak}")

    for name, *_ in changes:
        db.upsert_hae_metric(name, data_first=cov[name][0], data_last=cov[name][1])
    log.info(f"WROTE. обновлено строк: {len(changes)} (backup: {bak})")


def main():
    ap = argparse.ArgumentParser(description="Бэкфилл покрытия данных в hae_metric_registry.")
    ap.add_argument("--write", action="store_true", help="бэкап + запись (по умолчанию dry-run)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    run(do_write=args.write)


if __name__ == "__main__":
    main()
