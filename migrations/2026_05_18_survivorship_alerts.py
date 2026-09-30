#!/usr/bin/env python3.11
"""Миграция: пять survivorship-правил в alerts (фаза 3 плана v4).

Источник: ~/health/data/survivorship_config.yaml :: constraints_seed.
Idempotent: проверяет существование по комбинации (source, message[:80]).

CLI:
    --dry-run  показать INSERT statements без выполнения
    (no args)  выполнить миграцию
"""
from __future__ import annotations

import argparse
import sys
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import health_db as db

CONFIG = Path.home() / "health/data/survivorship_config.yaml"

ACTION_MAP = {
    "prohibit":                ("medication_interaction", "high",   "survivorship_literature"),
    "prefer_alternative":      ("medication_interaction", "medium", "survivorship_literature"),
    "monitor_ecg":             ("medication_interaction", "high",   "genetic_constraint"),
    "standing_recommendation": ("other",                  "low",    "survivorship_literature"),
}


def _build_alerts_from_seed(seed):
    out = []
    for c in seed:
        action = c.get("action", "other")
        type_, severity, source_default = ACTION_MAP.get(
            action, ("other", "medium", "survivorship_literature")
        )
        yaml_source = c.get("source", "")
        if yaml_source.startswith("genetic"):
            source = "genetic_constraint"
        else:
            source = source_default
        message = (
            f"[{c.get('id', '?')}] "
            f"condition={c.get('condition', '')}. "
            f"{c.get('reason', '')} "
            f"(ref: {yaml_source})"
        )
        out.append({"type": type_, "severity": severity, "message": message, "source": source})
    return out


def main(dry_run: bool = False) -> int:
    if not CONFIG.exists():
        print(f"ERROR: config not found at {CONFIG}", file=sys.stderr)
        return -1
    cfg = yaml.safe_load(CONFIG.read_text())
    seed = cfg.get("constraints_seed", []) or []
    if not seed:
        print("Nothing to migrate.")
        return 0
    candidates = _build_alerts_from_seed(seed)
    print(f"Built {len(candidates)} alert records from config.\n")
    existing = db.get_active_alerts() or []
    existing_msgs = {(a.get("message") or "")[:80] for a in existing}
    inserted = 0
    skipped = 0
    for a in candidates:
        if a["message"][:80] in existing_msgs:
            print(f"  SKIP (exists): {a['source']} :: {a['message'][:60]}")
            skipped += 1
            continue
        if dry_run:
            print(f"  [DRY-RUN INSERT] type={a['type']} severity={a['severity']} source={a['source']}")
            print(f"    message: {a['message'][:120]}")
        else:
            aid = db.save_alert(
                type_=a["type"], severity=a["severity"],
                message=a["message"], source=a["source"], active=True,
            )
            print(f"  INSERTED #{aid}: {a['source']} :: {a['message'][:60]}")
        inserted += 1
    print(f"\nDone. {'Would insert' if dry_run else 'Inserted'}: {inserted}, skipped: {skipped}.")
    return inserted


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.exit(0 if main(dry_run=args.dry_run) >= 0 else 1)
