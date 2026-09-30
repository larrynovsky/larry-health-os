"""lab_reconcile.py — единый чокпоинт против «лаб-контент в не-lab документе».

Корневой баг: coordinate_import извлекает лабы только если классификатор пометил
документ как lab. Фото/скан-бланк, попавший в general_medical (или пришедший мимо
lab-пути), теряет ВСЕ аналиты. Watcher-путь этого не касается, массовый импорт — да.

Решение — один чокпоинт: пройтись по imported_docs, для НЕ-lab документов прогнать
vision-распознаватель (lab_backfill.run_backfill), найденные лабы поднять в staging
на ревью. Идемпотентно: дедуп по content-hash (recognized_docs) — повторно не гоняет,
поэтому годится и для бэклога (один прогон), и go-forward (launchd, ловит новые).

Читает CR/ через lab_backfill.ICLOUD_ROOT (синкается на Studio). Пишет ТОЛЬКО staging;
промоут в канон — ручной (роль ревьюера). Один публичный вход: reconcile().
"""
from __future__ import annotations
import argparse
import json
import logging
from datetime import date
from _time_inject import get_today  # seam

import health_db
import lab_backfill

log = logging.getLogger("lab_reconcile")

# Типы, которые уже идут через lab-путь (coordinate_import) — их не трогаем.
_LAB_TYPES = {"lab", "lab_panel"}

# Счета содержат количества услуг и цены, которые нельзя читать как результаты.
# Выдуманный пример: SERVICE_DEMO × 4, стоимость 23 условные единицы.
_SKIP_TYPES = {"hospital_bill"}


def _candidates() -> list[str]:
    """source_file импортированных доков, КРОМЕ lab-типа и биллинга (пустой тип = кандидат)."""
    from pathlib import Path as _Path
    import import_all
    con = health_db.get_conn()
    rows = con.execute("SELECT source_file, COALESCE(doc_type, '') FROM imported_docs").fetchall()
    skipped = sum(1 for _, dt in rows if dt in _SKIP_TYPES)
    out, fin = [], 0
    for sf, dt in rows:
        if dt in _LAB_TYPES or dt in _SKIP_TYPES:
            continue
        # Проверка по имени файла дополняет doc_type, в том числе пустой.
        # Каталог оплаты сам по себе не делает вложенный лабораторный отчёт счётом.
        if import_all.is_financial(_Path(sf)):
            fin += 1
            continue
        out.append(sf)
    if skipped or fin:  # датчик: видно, сколько инвойсов не пошло в vision
        log.info(f"lab_reconcile: биллинг-типов {skipped} + финансовых-по-имени {fin} пропущено (не сканируем на лабы)")
    return out


def reconcile(run_id: str | None = None, limit: int | None = None, dry: bool = False) -> dict:
    """Прогон реконсиляции. dry=True считает НОВЫЕ доки (пойдут в vision), API не тратит."""
    run_id = run_id or f"reconcile_{get_today().isoformat()}"
    cands = _candidates()
    if limit:
        cands = cands[:limit]

    s = {"run_id": run_id, "scanned": 0, "new": 0, "skipped_dup": 0,
         "missing": 0, "staged_docs": 0, "rows": 0, "no_labs": 0, "errors": 0,
         "staged_detail": []}
    for sf in cands:
        s["scanned"] += 1
        abs_path = lab_backfill.resolve_document(sf)
        if abs_path is None:
            s["missing"] += 1
            continue
        try:
            sha = lab_backfill._file_sha256(abs_path)
        except Exception as e:
            s["errors"] += 1
            log.warning(f"sha failed {sf}: {e}")  # датчик, не тихо
            continue
        if lab_backfill.already_recognized(sha):
            s["skipped_dup"] += 1
            continue
        s["new"] += 1
        if dry:
            continue  # dry: только оценка стоимости (сколько новых пойдёт в vision)
        try:
            r = lab_backfill.run_backfill(run_id, doc_path=sf, date=None, force=False)
            rows = r.get("rows", 0)
            s["rows"] += rows
            if rows > 0:
                s["staged_docs"] += 1
                s["staged_detail"].append({"source_file": sf, "rows": rows})
            else:
                s["no_labs"] += 1
        except Exception as e:
            s["errors"] += 1
            log.error(f"reconcile {sf}: {e}")
    return s


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry", action="store_true", help="посчитать новые доки без vision/API")
    a = ap.parse_args()
    print(json.dumps(reconcile(a.run_id, a.limit, a.dry), ensure_ascii=False, indent=2))
