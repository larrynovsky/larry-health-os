#!/usr/bin/env python3.11
"""assessment_scheduler — ежедневный планировщик опросниковых задач.

Один контракт: для каждого применимого инструмента проверяет, не пора ли
заполнить (по cadence_days), и создаёт задачу `assessment:<id>` если просрочка.

Идемпотентность: fingerprint='assessment:<id>' — если активная задача
с таким fingerprint уже есть, новая не создаётся.

Каталог: load_instruments — bundle + tenant overlay, ошибка вместо пустоты.
Публичные чтения: load_instruments, load_instrument, schedule_policy; без записей.
ensure_task — общий писатель задач для run и извлечения просьб из отчёта GP.
Владелец интервала — JSON опросника; дефолт и запас — assessment_schedule.json.
Рабочие читатели не зависят от integrity_tests. run пишет tasks через health_db.
Расписание: бот jobs/scheduled (C-191, 07.11.2026); legacy launchd для native.
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


BUNDLED_INSTRUMENTS_DIR = Path(__file__).resolve().parent / "methodology/instruments/recurring"
SCHEDULE_POLICY_PATH = Path(__file__).resolve().parent / "methodology/assessment_schedule.json"


def load_instruments(tenant_dir=None, *, applicable_only=False):
    """Read templates; applicable_only selects the tenant's automatic programme.

    applies_when is an ALL-of list of clinical_kb condition ids; absent/[] is universal.
    Raw reads remain available for importing historical answers and explicit dialogs.
    Missing/empty/broken bundle is an error, unlike a valid but inapplicable programme.
    """
    bundled = sorted(BUNDLED_INSTRUMENTS_DIR.glob("*.json"))
    if not bundled:
        raise FileNotFoundError(f"Missing bundled instruments: {BUNDLED_INSTRUMENTS_DIR}")
    paths = {p.stem: p for p in bundled}
    # Tenant files override bundled templates by filename; omitted files use the bundle.
    overrides = {p.stem: p for p in (tenant_dir or INSTRUMENTS_DIR).glob("*.json")}
    paths.update(overrides)
    out = []
    active = None
    for path in paths.values():
        ins = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(ins, dict) or not isinstance(ins.get("id"), str) or not ins["id"]:
            raise ValueError(f"Invalid instrument: {path.name}")
        # A tenant overlay is an explicit assignment: applies_when never gates it.
        if applicable_only and path.stem not in overrides:
            required = ins.get("applies_when", [])
            if not isinstance(required, list) or any(not isinstance(c, str) or not c for c in required):
                raise ValueError(f"Invalid applies_when: {path.name}")
            if required:
                if active is None:
                    import clinical_kb
                    with db.get_conn() as conn:
                        active = clinical_kb.active_conditions(conn)
                if not set(required).issubset(active):
                    continue
        out.append(ins)
    return out


def load_instrument(instrument_id, tenant_dir=None):
    """Resolve both canonical and stored source ids through the same catalogue."""
    from assessment_importer import instrument_source_id
    for ins in load_instruments(tenant_dir):
        if instrument_source_id(ins["id"]) == instrument_source_id(instrument_id):
            return ins
    raise FileNotFoundError(f"Instrument not found: {instrument_id}")


def schedule_policy(instrument):
    """One versioned home for cadence defaults and freshness grace, in days."""
    policy = json.loads(SCHEDULE_POLICY_PATH.read_text(encoding="utf-8"))
    cadence = int(instrument.get("cadence_days", policy["cadence_days"]))
    grace = int(policy["grace_days"])
    if cadence <= 0 or grace < 0:
        raise ValueError("Invalid assessment schedule policy")
    return cadence, grace


def _load_instruments():
    return load_instruments(INSTRUMENTS_DIR, applicable_only=True)


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


def ensure_task(ins, *, source="assessment_scheduler", source_date=None,
                deadline=None, reason="", priority="medium"):
    """Create a catalogue assessment through the scheduler's single write path.

    Return the new task, or None when open/snoozed fingerprint already exists.
    Caller supplies an applicable catalogue entry; failures propagate to caller.
    """
    from assessment_importer import instrument_source_id
    source_id = instrument_source_id(ins["id"])
    fingerprint = f"assessment:{source_id}"
    if _has_open_task(fingerprint):
        return None
    content = i18n.t(
        "assessment.task.intro", name=fmt_instrument_name(ins),
        questions=fmt_count(len(ins.get("items") or []), "questions"),
        period=fmt_label(ins.get("recall_period", "past_week"), "assessment.period"),
    )
    task = dict(source=source, source_date=source_date, content=content,
                priority=priority, deadline=deadline, reason=reason,
                fingerprint=fingerprint, resolution_type="self_managed")
    tid = db.save_task(type_="assessment", **task)
    if tid is None:
        return None
    return dict(task, id=tid, type="assessment", instrument=source_id, origin="system")


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

        cadence, _ = schedule_policy(ins)
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

        try:
            task = ensure_task(ins, deadline=deadline, reason=reason)
            if task is not None:
                created.append({"id": task["id"], "instrument": source_id, "reason": reason})
                log.info(f"  + created task #{task['id']} for {source_id}")
        except Exception as e:
            log.error(f"save_task failed for {source_id}: {e}")

    log.info(f"done: {len(created)} tasks created")
    return created


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
