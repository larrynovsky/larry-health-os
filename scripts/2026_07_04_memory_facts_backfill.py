#!/usr/bin/env python3.11
"""
Миграция Фазы 1 (2026-07-04): бэкофилл разговорных строк memory → memory_facts.

БЕЗОПАСНОСТЬ:
  - По умолчанию DRY-RUN: только считает, НИ ОДНОЙ записи.
  - --apply пишет; идемпотентно (guard по флагу config _memory_facts_backfilled).
  - Оригиналы в `memory` НЕ трогаются (копия, не move). Подсистема гипотез не задета.
  - Таблицу создаёт health_db._ensure_memory_facts_table (канонический DDL).

Маппинг category → mem_class (копируются ТОЛЬКО разговорные категории):
  profile_update → fact | observation → state | open_question → question
  recommendation → recommendation | experiment_candidate → experiment
Остальное (hypothesis, literature_note, constitution_conflict, import_status,
dedup_skipped) остаётся в `memory` и НЕ мигрирует.

Запуск:
  python3.11 scripts/2026_07_04_memory_facts_backfill.py            # dry-run
  python3.11 scripts/2026_07_04_memory_facts_backfill.py --apply    # запись
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import health_db as db  # noqa: E402

CATEGORY_MAP = {
    "profile_update":       "fact",
    "observation":          "state",
    "open_question":        "question",
    "recommendation":       "recommendation",
    "experiment_candidate": "experiment",
}
_FLAG = "_memory_facts_backfilled"


def _counts(conn) -> dict:
    rows = conn.execute(
        "SELECT category, COUNT(*) FROM memory GROUP BY category"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def main(apply: bool) -> int:
    db._ensure_memory_facts_table()
    with db.get_conn() as conn:
        counts = _counts(conn)
        migratable = {c: counts.get(c, 0) for c in CATEGORY_MAP}
        skipped = {c: n for c, n in counts.items() if c not in CATEGORY_MAP}
        total_mig = sum(migratable.values())

        print("=== Бэкофилл memory → memory_facts ===")
        print("МИГРИРУЮТ (category → mem_class → строк):")
        for c, mc in CATEGORY_MAP.items():
            print(f"  {c:22s} → {mc:14s} {migratable[c]:>4d}")
        print(f"  ИТОГО к миграции: {total_mig}")
        print("НЕ ТРОГАЮТСЯ (остаются в memory):")
        for c, n in sorted(skipped.items(), key=lambda x: -x[1]):
            print(f"  {c:22s} {n:>4d}")

        already = conn.execute(
            "SELECT COUNT(*) FROM memory_facts WHERE source='backfill'"
        ).fetchone()[0]
        _fr = conn.execute("SELECT value_text FROM system_config WHERE key=?", (_FLAG,)).fetchone()
        flag = _fr[0] if _fr else None
        if already or flag:
            print(f"\n⚠️  Уже мигрировано (memory_facts backfill-строк={already}, flag={flag}). "
                  "Повторный --apply пропущен (идемпотентность).")
            return 0

        if not apply:
            print("\nDRY-RUN: ничего не записано. Для записи — флаг --apply.")
            return 0

        # ── APPLY ──
        migrated = 0
        for cat, mem_class in CATEGORY_MAP.items():
            src = conn.execute(
                "SELECT key, value, confidence, active, created_at, date "
                "FROM memory WHERE category = ?", (cat,)
            ).fetchall()
            for row in src:
                conn.execute(
                    """INSERT INTO memory_facts
                       (mem_class, key, value, confidence, valid_from, source,
                        subject, active, created_at)
                       VALUES (?, ?, ?, ?, ?, 'backfill', 'self', ?, ?)""",
                    (mem_class, row["key"], row["value"], row["confidence"],
                     row["date"], row["active"], row["created_at"]),
                )
                migrated += 1
        # флаг идемпотентности пишем той же связью (без вложенного соединения → без lock)
        conn.execute(
            """INSERT INTO system_config (key, value_text, category, updated_at, source)
               VALUES (?, ?, '_migration', datetime('now'), 'backfill')
               ON CONFLICT(key) DO UPDATE SET
                   value_text = excluded.value_text, updated_at = excluded.updated_at""",
            (_FLAG, str(migrated)),
        )
        print(f"\n✅ ЗАПИСАНО: {migrated} строк в memory_facts. Оригиналы в memory целы.")
    return 0


if __name__ == "__main__":
    sys.exit(main(apply="--apply" in sys.argv))
