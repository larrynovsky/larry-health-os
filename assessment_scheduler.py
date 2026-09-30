#!/usr/bin/env python3.11
"""assessment_scheduler — ежедневный планировщик опросниковых задач.

Один контракт: для каждого инструмента в каталоге проверяет, не пора ли
заполнить (по cadence_days), и создаёт задачу `assessment:<id>` если просрочка.

Идемпотентность: fingerprint='assessment:<id>' — если активная задача
с таким fingerprint уже есть, новая не создаётся.

Расписание: launchd com.larry.health.assessment-scheduler (ежедневно 03:30).
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date, timedelta
from _time_inject import get_today  # seam
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import health_db as db
import i18n
from _fmt_helpers import fmt_count, fmt_label, fmt_instrument_name
from secrets_paths import is_owner_data

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def tenant_data_path(name: str) -> Path:
    """Keep owner inputs in their legacy home; other tenants use their data dir."""
    root = Path.home() / "health" if is_owner_data() else Path(db._resolve_health_dir())
    return root / "data" / name


INSTRUMENTS_DIR = tenant_data_path("instruments")


def _load_instruments():
    out = []
    if not INSTRUMENTS_DIR.exists():
        return out
    for f in sorted(INSTRUMENTS_DIR.glob("*.json")):
        try:
            out.append(json.loads(f.read_text()))
        except Exception as e:
            log.warning(f"parse failed {f.name}: {e}")
    return out


def _last_filled(source_id):
    """Возвращает date последнего заполнения или None."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM lab_results WHERE source = ?",
            (f"instrument:{source_id}",),
        ).fetchone()
    return row["d"] if row and row["d"] else None


def _has_open_task(fingerprint):
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks "
            "WHERE fingerprint = ? AND status IN ('open', 'snoozed')",
            (fingerprint,),
        ).fetchone()
    return bool(row and row["n"])


def run():
    today = get_today()
    instruments = _load_instruments()
    log.info(f"scheduler: checking {len(instruments)} instruments")
    created = []
    for ins in instruments:
        # Короткий id для test_name префикса — конвенция assessment_importer.instrument_source_id
        from assessment_importer import instrument_source_id
        canonical = ins.get("id", "?")
        source_id = instrument_source_id(canonical)

        cadence = int(ins.get("cadence_days", 90))
        offset = int(ins.get("cadence_offset_days", 0))
        fingerprint = f"assessment:{source_id}"

        # Если есть активная задача — пропускаем
        if _has_open_task(fingerprint):
            log.info(f"  {source_id}: open task already exists, skip")
            continue

        last = _last_filled(source_id)
        if last is None:
            # ни разу не заполняли — учитываем offset чтобы три опросника
            # не падали в один день при стартовой синхронизации
            today_offset_days = (today - get_today()).days  # always 0
            # Чтобы offset работал на старте: вычисляем сколько дней с начала
            # эпохи и берём по модулю cadence — но это сложно.
            # Простой подход: первое предложение в день с момента деплоя + offset.
            # Здесь — если нет заполнений, предлагаем всегда (с учётом offset
            # через snooze в первой задаче).
            reason = f"первое заполнение {source_id}"
            deadline = (today + timedelta(days=max(1, offset or 7))).isoformat()
        else:
            try:
                days_since = (today - date.fromisoformat(last)).days
            except Exception:
                continue
            if days_since < cadence:
                log.info(f"  {source_id}: last {last} ({days_since}d ago), cadence {cadence}d, skip")
                continue
            reason = f"{source_id}: {days_since}д с последнего ({last}), порог {cadence}д"
            deadline = (today + timedelta(days=7)).isoformat()

        content = i18n.t(
            "assessment.task.intro",
            name=fmt_instrument_name(ins),
            questions=fmt_count(len(ins.get('items') or []), "questions"),
            period=fmt_label(ins.get('recall_period', 'past_week'), "assessment.period"),
        )
        try:
            tid = db.save_task(
                source="assessment_scheduler",
                type_="assessment",
                content=content,
                priority="medium",
            )
            # Также проставим fingerprint и deadline через UPDATE
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE tasks SET fingerprint=?, deadline=?, reason=? WHERE id=?",
                    (fingerprint, deadline, reason, tid),
                )
            created.append({"id": tid, "instrument": source_id, "reason": reason})
            log.info(f"  + created task #{tid} for {source_id}")
        except Exception as e:
            log.error(f"save_task failed for {source_id}: {e}")

    log.info(f"done: {len(created)} tasks created")
    return created


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
