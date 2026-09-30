"""Репарация: стадии сна от Sleep Cycle — не измерения, а нули вместо пропусков.

Что чиним. Если источник не измеряет стадии сна, подставленный импортёром
ноль означает отсутствие поля, а не физиологическое наблюдение.
COUNT() не должен считать такую подстановку данными.

Предикат опирается на происхождение: raw.sleep.source == 'Sleep Cycle'
и все четыре стадии равны нулю. Другие источники этим правилом не чинятся;
суждение о физиологии для такой правки не требуется.

Домов ДВА. `~/health/data/daily_metrics/*.json` первичен (туда пишет импортёр, оттуда
читает `morning_report.load_day`); таблица `daily_metrics` вторична, но чистится ОТДЕЛЬНО:
`upsert_metrics_from_json` не умеет ставить NULL, поэтому чистка JSON её не вылечит.

Каждое изменение проходит через `health_db.log_repair` в той же транзакции, поэтому
операция обратима одним `revert_repairs(run_id)`. Обратимость — не оговорка, а условие,
на котором эта репарация вообще законна без человека на каждую запись (§13, ступень 1).

Запуск:
    python3.11 -m migrations.repair_sleep_cycle_stages_20260731            # dry-run
    python3.11 -m migrations.repair_sleep_cycle_stages_20260731 --apply    # запись
    python3.11 -m migrations.repair_sleep_cycle_stages_20260731 --revert <run_id>
"""
from __future__ import annotations

import json
import sys

import health_db as hdb

REASON = "sleep_cycle_no_stages"
SOURCE = "Sleep Cycle"
JSON_STAGES = ("deep", "rem", "core", "awake")
HOME_JSON = "json:daily_metrics"
HOME_SQLITE = "sqlite:daily_metrics"
HOME_RAW = "sqlite:daily_metrics.raw"


def stage_columns(conn) -> dict:
    """Соответствие «стадия → колонка» берётся из ЖИВОЙ схемы, а не из литерала.

    Литерал `{"deep": "sleep_deep", "rem": "sleep_rem"}` здесь стоял в первой редакции
    (2026-07-31) и был написан по моей ПАМЯТИ о схеме. В схеме колонок четыре, поэтому
    репарация прошла по половине полей, а контроль этого не заметил: он проверял ровно то
    поле, которое чинил. Список, выведенный из схемы, не может разойтись со схемой.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_metrics)")}
    return {k: f"sleep_{k}" for k in JSON_STAGES if f"sleep_{k}" in cols}


def is_fake_stage_record(sleep: dict) -> bool:
    """Единственное место, где живёт предикат. Публичен, потому что его зовёт тест:
    если предикат разъедется с тем, что проверяют, репарация станет непроверяемой."""
    if not isinstance(sleep, dict) or sleep.get("source") != SOURCE:
        return False
    return all(sleep.get(k) == 0 for k in JSON_STAGES)


def plan_json(metrics_dir) -> list[tuple[str, str, float]]:
    """(дата, поле, старое значение) для дома-файлов. Ничего не пишет."""
    out = []
    for path in sorted(metrics_dir.glob("*.json")):
        try:
            sleep = (json.loads(path.read_text()) or {}).get("sleep") or {}
        except (json.JSONDecodeError, OSError):
            continue
        if not is_fake_stage_record(sleep):
            continue
        out.extend((path.stem, k, sleep[k]) for k in JSON_STAGES if sleep.get(k) == 0)
    return out


def plan_sqlite(conn) -> list[tuple[str, str, float]]:
    """(дата, колонка, старое значение) для плоских колонок таблицы. Ничего не пишет."""
    cols = stage_columns(conn)
    if not cols:
        return []
    sel = ", ".join(cols.values())
    out = []
    for row in conn.execute(f"SELECT date, raw, {sel} FROM daily_metrics WHERE raw IS NOT NULL"):
        date, raw = row[0], row[1]
        try:
            sleep = (json.loads(raw) or {}).get("sleep") or {}
        except json.JSONDecodeError:
            continue
        if not is_fake_stage_record(sleep):
            continue
        for i, col in enumerate(cols.values(), start=2):
            if row[i] == 0:
                out.append((date, col, row[i]))
    return out


def plan_raw(conn) -> list[tuple[str, str, float]]:
    """(дата, ключ стадии, старое значение) для ТРЕТЬЕГО дома — JSON внутри колонки `raw`.

    Дом найден 31.07 после того, как репарация была объявлена исполненной. Он не копия
    файлов на диске: `longitudinal_analysis` читает именно `raw` (строка 117), поэтому
    нули отсюда попадают прямо в корреляции, на которых стоит вера.
    """
    out = []
    for date, raw in conn.execute(
            "SELECT date, raw FROM daily_metrics WHERE raw IS NOT NULL"):
        try:
            sleep = (json.loads(raw) or {}).get("sleep") or {}
        except json.JSONDecodeError:
            continue
        if not is_fake_stage_record(sleep):
            continue
        out.extend((date, k, sleep[k]) for k in JSON_STAGES if sleep.get(k) == 0)
    return out


def apply(run_id: str, *, write: bool) -> dict:
    """JSON первым, таблица второй: чинить производное раньше источника — та же
    ошибка в миниатюре. Возвращает счётчики по домам."""
    metrics_dir = hdb.METRICS_DIR
    j_plan = plan_json(metrics_dir)
    with hdb.get_conn() as conn:
        s_plan = plan_sqlite(conn)
        r_plan = plan_raw(conn)

    if not write:
        return {"run_id": run_id, "json": len(j_plan), "sqlite": len(s_plan),
                "raw": len(r_plan), "written": False}

    with hdb.get_conn() as conn:
        by_day: dict[str, list] = {}
        for day, field, old in j_plan:
            by_day.setdefault(day, []).append((field, old))
        for day, items in by_day.items():
            path = metrics_dir / f"{day}.json"
            doc = json.loads(path.read_text())
            for field, old in items:
                hdb.log_repair(conn, run_id=run_id, home=HOME_JSON, entity=day,
                               field=field, old_value=old, new_value=None, reason=REASON)
                doc["sleep"][field] = None
            path.write_text(json.dumps(doc, ensure_ascii=False, indent=2))

        for day, col, old in s_plan:
            hdb.log_repair(conn, run_id=run_id, home=HOME_SQLITE, entity=day,
                           field=col, old_value=old, new_value=None, reason=REASON)
            conn.execute(f"UPDATE daily_metrics SET {col} = NULL WHERE date = ?", (day,))

        raw_by_day: dict[str, list] = {}
        for day, field, old in r_plan:
            raw_by_day.setdefault(day, []).append((field, old))
        for day, items in raw_by_day.items():
            row = conn.execute("SELECT raw FROM daily_metrics WHERE date = ?", (day,)).fetchone()
            doc = json.loads(row[0])
            for field, old in items:
                hdb.log_repair(conn, run_id=run_id, home=HOME_RAW, entity=day,
                               field=field, old_value=old, new_value=None, reason=REASON)
                doc["sleep"][field] = None
            conn.execute("UPDATE daily_metrics SET raw = ? WHERE date = ?",
                         (json.dumps(doc, ensure_ascii=False), day))

    return {"run_id": run_id, "json": len(j_plan), "sqlite": len(s_plan),
            "raw": len(r_plan), "written": True}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--revert":
        if len(argv) < 2:
            print("нужен run_id: --revert <run_id>")
            return 2
        print(f"откачено записей: {hdb.revert_repairs(argv[1])}")
        return 0

    write = "--apply" in argv
    run_id = next((a.split("=", 1)[1] for a in argv if a.startswith("--run-id=")),
                  "sleep-cycle-stages-2026-07-31")
    res = apply(run_id, write=write)
    # Квитанция печатает ВСЕ дома. Дом, которого нет в отчёте, — дом, о котором забудут:
    # первая редакция не печатала `raw`, и его недочинку заметили только по данным.
    homes = "  ".join(f"{k}: {res[k]} полей" for k in ("json", "sqlite", "raw"))
    print(f"run_id={res['run_id']}  {homes}  записано={res['written']}")
    if not write:
        print("это dry-run; для записи добавь --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
