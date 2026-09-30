#!/usr/bin/env python3.11
"""
scripts/brief_shadow.py — Ф4 shadow-реплей анти-повтора (диагностика, dormant).

Реплеит диапазон дат через реальные провайдеры (brief_pipeline.assemble_cards) →
gate/FSM/slot (brief_state.advance) на SCRATCH-БД (прод context_cards НЕ трогает).
Печатает «до (всего вхождений) / после (реальных показов под гейтом)».

ВАЖНО: это карточно-/контекстный уровень. Финальный текст LLM может ввести
подавленный факт обратно (промпт связывает «глубокий низкий»→ген сна) — это ловит
claim-validator + правка промпта на этапе render/флипа, не здесь.

Запуск на Studio: /opt/homebrew/bin/python3.11 scripts/brief_shadow.py [start end]
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sqlite3
import tempfile
from datetime import date, timedelta

import health_db as hdb
import brief_pipeline as bp
import brief_state as bs


def run(start: date, end: date):
    scratch = tempfile.mktemp(suffix="_shadow.db")
    conn = sqlite3.connect(scratch)
    conn.row_factory = sqlite3.Row
    prod = sqlite3.connect(f"file:{hdb.DB_PATH}?mode=ro", uri=True)
    for (sql,) in prod.execute(
            "SELECT sql FROM sqlite_master WHERE tbl_name='context_cards' AND sql IS NOT NULL").fetchall():
        conn.execute(sql)
    conn.commit()

    tally, prov, days = {}, {}, 0
    d = start
    while d <= end:
        days += 1
        try:
            decisions = bs.advance(bp.assemble_cards(d), d, conn)
        except Exception as e:
            print(f"  {d}: ERR {type(e).__name__}: {e}")
            d += timedelta(days=1)
            continue
        for dec in decisions:
            k = dec["semantic_key"]
            t = tally.setdefault(k, [0, 0]); t[1] += 1; t[0] += (dec["status"] == "shown")
            q = prov.setdefault(k.split(":")[0], [0, 0]); q[1] += 1; q[0] += (dec["status"] == "shown")
        d += timedelta(days=1)

    before = sum(t for _, t in tally.values())
    after = sum(s for s, _ in tally.values())
    cut = 100 - 100 * after // max(before, 1)
    print(f"\nSHADOW {start}..{end} = {days} дней (scratch FSM, реальные провайдеры)")
    print(f"ВСЕГО карточко-показов: было {before} → стало {after} (срез повтора {cut}%)")
    print("\nПо провайдеру (показов/всего):")
    for p, (s, t) in sorted(prov.items(), key=lambda x: -x[1][1]):
        print(f"  {p:12} {s:3}/{t:3}")
    print("\nТоп по частоте вхождения:")
    for k, (s, t) in sorted(tally.items(), key=lambda x: -x[1][1])[:15]:
        print(f"  {k:36} {s:2}/{t:2}")
    os.remove(scratch)


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        run(date.fromisoformat(sys.argv[1]), date.fromisoformat(sys.argv[2]))
    else:
        run(date.today() - timedelta(days=41), date.today() - timedelta(days=1))
