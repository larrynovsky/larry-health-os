#!/usr/bin/env python3.11
"""
Импортирует heart_rate и active_energy из HealthAutoExport JSON в daily_metrics.
Не перезаписывает уже существующие значения (только NULL → значение).
"""
import json, sys, logging
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).parent))
import health_db as db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# один дом правила «какая выгрузка полная» (BL-PUB-16 д)
from import_apple_health import historical_export  # noqa: E402

def load_metric(metrics, name):
    for m in metrics:
        if m.get("name") == name:
            return m.get("data", [])
    return []

def aggregate_by_day(data_points, value_keys):
    """Агрегирует точки по дате (среднее), пробует разные ключи для значения."""
    by_day = defaultdict(list)
    for pt in data_points:
        date = (pt.get("date") or "")[:10]
        if not date:
            continue
        val = None
        for k in value_keys:
            v = pt.get(k)
            if v is not None:
                try:
                    val = float(v)
                    break
                except:
                    pass
        if val is not None:
            by_day[date].append(val)
    return {d: sum(vals)/len(vals) for d, vals in by_day.items()}

def run():
    db.init_db()
    HAE_PATH = historical_export()
    if HAE_PATH is None:
        log.warning("полная выгрузка HealthAutoExport не найдена")
        return
    log.info(f"Загружаем {HAE_PATH.name}")
    with open(HAE_PATH) as f:
        data = json.load(f)
    metrics = data["data"]["metrics"]

    # Агрегируем по дням
    hr_by_day  = aggregate_by_day(load_metric(metrics, "heart_rate"),  ["Avg", "avg", "value", "qty"])
    ae_by_day  = aggregate_by_day(load_metric(metrics, "active_energy"), ["qty", "value"])
    rhr_by_day = aggregate_by_day(load_metric(metrics, "resting_heart_rate"), ["qty", "value"])
    rr_by_day  = aggregate_by_day(load_metric(metrics, "respiratory_rate"), ["Avg", "avg", "value", "qty"])

    log.info(f"HR: {len(hr_by_day)} дней | ActiveEnergy: {len(ae_by_day)} | RHR: {len(rhr_by_day)} | RespRate: {len(rr_by_day)}")

    all_dates = set(hr_by_day) | set(ae_by_day) | set(rhr_by_day) | set(rr_by_day)
    log.info(f"Всего уникальных дат: {len(all_dates)}")

    updated = inserted = 0
    with db.get_conn() as conn:
        for date in sorted(all_dates):
            existing = conn.execute(
                "SELECT date, resting_hr, active_kcal FROM daily_metrics WHERE date=?", (date,)
            ).fetchone()

            hr  = round(hr_by_day.get(date), 1)  if date in hr_by_day  else None
            ae  = round(ae_by_day.get(date), 0)  if date in ae_by_day  else None
            rhr = round(rhr_by_day.get(date), 1) if date in rhr_by_day else None
            rr  = round(rr_by_day.get(date), 1)  if date in rr_by_day  else None

            row = conn.execute("SELECT date FROM daily_metrics WHERE date=?", (date,)).fetchone()
            if row:
                # Обновляем только NULL поля
                conn.execute("""
                    UPDATE daily_metrics SET
                        resting_hr  = COALESCE(resting_hr,  ?),
                        active_kcal = COALESCE(active_kcal, ?)
                    WHERE date=?
                """, (rhr or hr, ae, date))
                updated += 1
            else:
                conn.execute("""
                    INSERT INTO daily_metrics (date, resting_hr, active_kcal)
                    VALUES (?, ?, ?)
                """, (date, rhr or hr, ae))
                inserted += 1
        conn.commit()

    log.info(f"Готово. Обновлено: {updated}, вставлено: {inserted}")

if __name__ == "__main__":
    run()
