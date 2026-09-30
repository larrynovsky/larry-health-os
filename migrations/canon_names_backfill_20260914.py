#!/usr/bin/env python3.11
"""Строки канона, чьё имя СТАЛО сводиться к канону, переименовываются (14.09).

ЧТО ЧИНИТ. Заведение синонима меняет будущее: следующая сдача приедет под
каноническим именем. Уже лежащая строка при этом остаётся под старым — и это не
косметика. Читатели канона (тренд, консилиум, свежесть, датчик семей) ищут аналит
по КАНОНИЧЕСКОМУ имени, поэтому аналит под старым именем (условно «Кальпротектин») не попадал ни в один
из них: аналит существует, а для всех, кто его спрашивает, его нет.

ЧТО ДЕЛАЕТ. Одно правило, без списка имён: строка, у которой
`lab_canon.normalize(имя) != имя` И результат лежит в `CANONICALS`, получает
каноническое имя. Список здесь был бы вторым домом словаря и разъехался бы с ним.

ЧЕГО НЕ ДЕЛАЕТ ОСОЗНАННО:
  · не заводит имён — это словарь, отдельное решение и отдельный коммит;
  · не трогает строку, чьё имя уже каноническое или не сводится никуда;
  · НЕ СЛИВАЕТ строки: если после переименования в тот же день/источник/материал
    окажется строка с тем же каноническим именем, переименование для неё
    отменяется и она печатается как КОНФЛИКТ. Слияние двух измерений — не
    переименование, и решать его молча нельзя (тот же довод, по которому
    предикат A работы B не удаляет строку без двойника).

РАДИУС В ОБЕ СТОРОНЫ: число строк канона не меняется вовсе — меняется только
имя. Контроль после акта: счёт `lab_results` до == после, и ни одной строки,
подпадающей под правило, не осталось.

БЕЗОПАСНОСТЬ: dry-run по умолчанию, `--apply` явным флагом, снапшот БД перед
мутацией, одна транзакция.

  /opt/homebrew/bin/python3.11 -m migrations.canon_names_backfill_20260914
  /opt/homebrew/bin/python3.11 -m migrations.canon_names_backfill_20260914 --apply
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from _time_inject import get_now

import health_db
import lab_canon


def plan_rename(conn) -> tuple[list[dict], list[dict]]:
    """(переименования, конфликты). Чистое чтение."""
    rows = [dict(r) for r in conn.execute(
        "SELECT id, date, source, specimen, test_name FROM lab_results")]
    taken = {(r["date"], r["source"], r["specimen"], r["test_name"]) for r in rows}
    renames, conflicts = [], []
    for r in rows:
        name = r["test_name"] or ""
        canon = lab_canon.normalize(name)
        if canon == name or canon not in lab_canon.CANONICALS:
            continue
        r = {**r, "canon": canon}
        key = (r["date"], r["source"], r["specimen"], canon)
        (conflicts if key in taken else renames).append(r)
    return renames, conflicts


def _snapshot(db_path: Path) -> Path:
    dst = db_path.with_suffix(f".pre-namebackfill-{get_now():%Y%m%d-%H%M%S}.db")
    shutil.copy2(db_path, dst)
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    with health_db.get_conn() as conn:
        renames, conflicts = plan_rename(conn)
        before = conn.execute("SELECT count(*) FROM lab_results").fetchone()[0]

    print(f"канон: {before} строк; переименовываются: {len(renames)}; конфликтов: {len(conflicts)}")
    for r in renames:
        print(f"  · {r['test_name']} → {r['canon']}  ({r['date']})")
    for r in conflicts:
        print(f"  ⚠ КОНФЛИКТ (в тот же день уже есть {r['canon']}): {r['test_name']}")
    if not args.apply:
        print("\ndry-run. Для исполнения: --apply")
        return 0
    if not renames:
        print("переименовывать нечего")
        return 0

    print(f"снапшот БД: {_snapshot(Path(health_db.DB_PATH))}")
    with health_db.get_conn() as conn:
        conn.execute("BEGIN")
        try:
            conn.executemany("UPDATE lab_results SET test_name = ? WHERE id = ?",
                             [(r["canon"], r["id"]) for r in renames])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    with health_db.get_conn() as conn:
        after = conn.execute("SELECT count(*) FROM lab_results").fetchone()[0]
        left, _ = plan_rename(conn)
    print(f"канон: {before} → {after} (обязано совпасть); осталось под правилом: {len(left)}")
    if before != after or left:
        print("АКТ НЕ СОСТОЯЛСЯ ИЛИ ЗАДЕЛ ЛИШНЕЕ — смотри снапшот выше")
        return 1
    print("переименование состоялось: строк столько же, имена канонические")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
