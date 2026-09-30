#!/usr/bin/env python3.11
"""
backfill_critical_flag.py — разовый бэкофилл Ф4.5-A (сетка decay).

Помечает `critical_flag=1` существующим активным строкам memory_facts, чьё содержимое
`is_durable_fact` (устойчивый медфакт: генетика/онкостатус/план лечения, ошибочно
попавший в state). Без этого наивный decay states снёс бы устойчивые факты (статус, генетика, план).

Безопасность (пре-мортем):
  - только SET, никогда UNSET (не сносит ручной флаг);
  - dry-run по умолчанию — считает + показывает превью, НИЧЕГО не пишет (blast-radius);
  - --apply пишет; перед этим сделай снапшот (§3);
  - per-tenant через HEALTH_DATA_DIR; пустая выборка — no-op.

Запуск (Studio, canonical-only §8):
  HEALTH_DATA_DIR=~/health python3.11 backfill_critical_flag.py            # dry-run
  HEALTH_DATA_DIR=~/health python3.11 backfill_critical_flag.py --apply    # запись
"""
from __future__ import annotations

import sys

import health_db as db
import memory_salience as ms


def find_candidates(conn) -> list[tuple[int, str, str]]:
    rows = conn.execute(
        "SELECT id, key, value FROM memory_facts "
        "WHERE valid_to IS NULL AND active=1 AND COALESCE(critical_flag,0)=0 "
        "AND mem_class IN ('state','fact')"  # never-decay durable только для фактов, не вопросов
    ).fetchall()
    out = []
    for r in rows:
        text = f"{r['key'] or ''} {r['value'] or ''}"
        if ms.is_durable_fact(text):
            out.append((r["id"], r["key"], r["value"]))
    return out


def main() -> int:
    apply = "--apply" in sys.argv
    with db.get_conn() as conn:
        cands = find_candidates(conn)
        print(f"Кандидатов на critical_flag=1 (устойчивые факты в active): {len(cands)}")
        for cid, key, val in cands[:30]:
            print(f"  id={cid} [{key}] {(val or '')[:70]}")
        if len(cands) > 30:
            print(f"  … и ещё {len(cands) - 30}")
        if not apply:
            print("\nDRY-RUN. Проверь список. Запись: добавь --apply (сделай снапшот перед!).")
            return 0
        n = 0
        for cid, _k, _v in cands:
            cur = conn.execute(
                "UPDATE memory_facts SET critical_flag=1, updated_at=datetime('now') "
                "WHERE id=? AND COALESCE(critical_flag,0)=0",
                (cid,),
            )
            n += cur.rowcount
        print(f"\n✅ Помечено critical_flag=1: {n} строк.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
