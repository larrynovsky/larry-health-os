#!/usr/bin/env python3.11
"""
lab_staging_summary.py — сводка прогона: даты × типы панелей + дедуп.

Только чтение. Показывает:
  • матрицу дата × panel (сколько строк),
  • дедуп-группы (одна панель/аналит повторён на разных страницах):
    согласные значения = можно схлопнуть, конфликты = на ревью.

  /opt/homebrew/bin/python3.11 lab_staging_summary.py --run-id full2 [--doc "полная панель"]
"""
from __future__ import annotations
import argparse
from collections import defaultdict

import health_db
import labs_db
import lab_canon

# Третья копия словаря «панель → дом» жила здесь и удалена 2026-08-01 (работа B,
# критерий готовности 1). Копий было три: мёртвая в `lab_promote`, живая
# в `lab_specialized` и эта — и первые две уже разошлись по `immunology`.
# Сводка теперь спрашивает тот же дом, что и оба писателя: класс даёт
# `lab_canon.classify_row`, вердикт о доме — таблица через `labs_db`.


def _name(r):
    return r["canonical_name"] or r["raw_name"] or "?"


def summary(run_id: str, doc_filter: str | None = None):
    health_db.init_db()
    with health_db.get_conn() as conn:
        q = "SELECT * FROM lab_results_staging WHERE run_id=?"
        args = [run_id]
        if doc_filter:
            q += " AND source_file LIKE ?"
            args.append(f"%{doc_filter}%")
        rows = [dict(r) for r in conn.execute(q, args)]
    if not rows:
        print("нет строк")
        return

    print(f"=== Прогон {run_id}" + (f" / {doc_filter}" if doc_filter else "") + f" — строк: {len(rows)} ===\n")

    # дата × panel
    mat = defaultdict(lambda: defaultdict(int))
    dates, panels = set(), set()
    for r in rows:
        d = r["date"] or "?"
        p = r["panel"] or "?"
        mat[d][p] += 1
        dates.add(d); panels.add(p)
    panels = sorted(panels)
    print("Дата × тип панели:")
    print("  " + "дата".ljust(12) + " | " + " | ".join(p[:12].rjust(12) for p in panels))
    for d in sorted(dates):
        print("  " + d.ljust(12) + " | " + " | ".join(str(mat[d].get(p, 0)).rjust(12) for p in panels))

    # классификация по назначению — по вердикту человека, не по литералу
    verdicts = labs_db.domain_verdicts()
    route = defaultdict(int)
    unjudged = defaultdict(int)
    for r in rows:
        cls = lab_canon.classify_row(r.get("raw_name"), r.get("panel"))
        home = verdicts.get(cls)
        route[home or "БЕЗ ВЕРДИКТА"] += 1
        if home is None:
            unjudged[cls] += 1
    print(f"\nМаршрут: канон(→lab_results)={route['canon']} · "
          f"спец-слой={route['specialized']} · без вердикта={route['БЕЗ ВЕРДИКТА']}")
    if unjudged:
        print("  ⚠️ классы без вердикта человека: " +
              ", ".join(f"{k}={v}" for k, v in sorted(unjudged.items())))

    # B1: происхождение даты
    ds = defaultdict(int)
    for r in rows:
        ds[r.get("date_source") or "?"] += 1
    fb = ds.get("fallback", 0)
    print(f"Дата: read={ds.get('read',0)} · inherited={ds.get('inherited',0)} · "
          f"fallback={fb} ({100*fb//max(len(rows),1)}% — недостоверный ключ замены)")

    # дедуп по (date, name)
    groups = defaultdict(list)
    for r in rows:
        groups[(r["date"], _name(r))].append(r)
    dup_consistent = dup_conflict = 0
    conflicts = []
    for (date, name), g in groups.items():
        if len(g) < 2:
            continue
        vals = [r["value"] for r in g if r["value"] is not None]
        if not vals:
            continue
        spread = (max(vals) - min(vals)) / max(abs(max(vals)), abs(min(vals)), 1e-9)
        if spread <= 0.01:
            dup_consistent += 1
        else:
            dup_conflict += 1
            conflicts.append((date, name, sorted(set(vals))))
    uniq = len(groups)
    print(f"\nДедуп: уникальных (date+аналит) = {uniq} из {len(rows)} строк")
    print(f"  повторы согласные (схлопнуть) = {dup_consistent} · повторы-конфликты (на ревью) = {dup_conflict}")
    if conflicts:
        print("\n  Конфликты значений (одна дата+аналит, разные числа):")
        for date, name, vals in conflicts[:20]:
            print(f"    {date} {name[:34]}: {vals}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--doc", default=None)
    a = ap.parse_args()
    summary(a.run_id, a.doc)
