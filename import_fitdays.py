#!/usr/bin/env python3.11
"""
import_fitdays.py — Fitdays smart-scale weight → daily_metrics.weight (ФАЗА 1: только вес).

Идемпотентный АДДИТИВНЫЙ бэкфилл:
  парс CSV → агрегация к дню (УТРО = самое раннее взвешивание за день) →
  UPSERT в daily_metrics.weight по COALESCE-семантике: заполняем только NULL,
  существующее значение НИКОГДА не перезаписываем: один прибор может отдавать
  тот же замер и через CSV, и через Apple Health/HealthKit.

Медицинская дисциплина:
  (по умолчанию) --dry-run: план INSERT/FILL/SKIP, диапазон дат, непустой weight
                 до/после, годовые средние, позитивный контроль. БЕЗ бэкапа, БЕЗ записи.
  --write       : снапшот health.db ПЕРЕД записью (pre-op backup), затем upsert.
                  Запись разрешена только на Studio (health_db.get_conn raise off-Studio).

ФАЗА 2 (состав тела): те же строки CSV → 8 колонок daily_metrics (body_fat_pct,
skeletal_muscle_pct, muscle_mass_kg, visceral_fat, body_water_pct, bone_mass_kg,
protein_pct, bmr_kcal). Требует колонок из health_db._migrate_body_composition.
Тот же аддитивный COALESCE-паттерн: доливает только NULL, существующее не трогает.
"""
from _time_inject import get_now  # seam
import argparse
import csv
import logging
import re
import shutil
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import health_db as db

# ── Пути ─────────────────────────────────────────────────────────────────────
import infra_config   # дом облачного пути (BL-PUB-12)
# АБСОЛЮТНЫЙ iCloud-путь (помечен явно, не «голым»): сырой экспорт Fitdays живёт
# в iCloud-дереве health, НЕ в каноническом HEALTH_DATA_DIR. Переопределяется --csv.
# Имя файла экспорта содержит имя профиля в приложении весов — берём любой Fitdays*.csv
# (самый свежий), а не литерал с именем владельца (pub-prep 2026-09-23).
_CSV_DIR = infra_config.cloud_dir("data")
_found = sorted(_CSV_DIR.glob("Fitdays*.csv"), key=lambda p: p.stat().st_mtime) if _CSV_DIR.exists() else []
DEFAULT_CSV = _found[-1] if _found else _CSV_DIR / "Fitdays.csv"

# ── QC-константы ─────────────────────────────────────────────────────────────
SANE_MIN, SANE_MAX = 40.0, 140.0  # кг, порог правдоподобия человека
MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
          "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
DATE_RE = re.compile(r"^(\d{2}):(\d{2})\s+([A-Za-z]{3})\.(\d{2})\s+(\d{4})$")
WEIGHT_RE = re.compile(r"^([\d.]+)\s*kg$", re.IGNORECASE)

log = logging.getLogger("import_fitdays")


class QCReject(Exception):
    """Строка не прошла контроль качества (дата/единица/диапазон)."""


def parse_row(date_str, weight_str):
    """('07:00 Mar.15 2026', '80.0kg') → (date_iso, hh, mm, weight_kg) или QCReject."""
    m = DATE_RE.match((date_str or "").strip())
    if not m:
        raise QCReject(f"bad date: {date_str!r}")
    hh, mm, mon, dd, yyyy = m.groups()
    if mon not in MONTHS:
        raise QCReject(f"bad month: {mon!r}")
    date_iso = f"{yyyy}-{MONTHS[mon]:02d}-{int(dd):02d}"
    try:
        datetime.strptime(date_iso, "%Y-%m-%d")  # ловит 30 фев и т.п.
    except ValueError:
        raise QCReject(f"impossible date: {date_iso}")
    wm = WEIGHT_RE.match((weight_str or "").strip())
    if not wm:
        raise QCReject(f"bad weight/unit (expect kg): {weight_str!r}")
    w = float(wm.group(1))
    if not (SANE_MIN <= w <= SANE_MAX):
        raise QCReject(f"weight out of sane range [{SANE_MIN},{SANE_MAX}]: {w}")
    return date_iso, int(hh), int(mm), w


# ── Состав тела (Фаза 2): col в daily_metrics → (CSV-поле, ед-суффикс, sane_min, sane_max) ──
BODY_COMP = [
    ("bmi",                 "BMI",             "",     10.0,   60.0),
    ("body_fat_pct",        "Body Fat",        "%",     3.0,   60.0),
    ("skeletal_muscle_pct", "Skeletal Muscle", "%",    20.0,   75.0),
    ("muscle_mass_kg",      "Muscle mass",     "kg",   20.0,  120.0),
    ("visceral_fat",        "Visceral Fat",    "",      1.0,   30.0),
    ("body_water_pct",      "Body Water",      "%",    30.0,   80.0),
    ("bone_mass_kg",        "Bone Mass",       "kg",    1.0,   10.0),
    ("protein_pct",         "Protein",         "%",    10.0,   30.0),
    ("bmr_kcal",            "BMR",             "kcal", 800.0, 3500.0),
]
_NUM_RE = re.compile(r"^([\d.]+)")


def parse_body_comp(row):
    """Строка CSV → {col: float|None}. '--'/пусто/несовпадение единицы/вне sane → None
    (поле теряем молча, строку НЕ роняем — вес проходит свой QC отдельно)."""
    out = {}
    for col, field, unit, lo, hi in BODY_COMP:
        raw = (row.get(field) or "").strip()
        if raw in ("", "--") or (unit and not raw.endswith(unit)):
            out[col] = None
            continue
        m = _NUM_RE.match(raw)
        if not m:
            out[col] = None
            continue
        v = float(m.group(1))
        out[col] = v if (lo <= v <= hi) else None
    return out


def positive_control():
    """Посаженные битые строки ОБЯЗАНЫ быть отбиты; хорошая — принята. Иначе AssertionError."""
    bad = [
        ("07:00 Mar.15 2026", "999kg"),    # выше диапазона
        ("07:00 Mar.15 2026", "80.0lb"),   # чужая единица
        ("07:00 Xxx.15 2026", "80.0kg"),   # битый месяц
        ("garbage", "80.0kg"),             # битая дата
        ("07:00 Feb.30 2026", "80.0kg"),   # несуществующая дата
        ("07:00 Mar.15 2026", "12.0kg"),   # ниже диапазона
    ]
    for ds, ws in bad:
        try:
            parse_row(ds, ws)
        except QCReject:
            continue
        raise AssertionError(f"POSITIVE CONTROL FAILED: битая строка прошла QC: {ds!r},{ws!r}")
    parse_row("07:00 Mar.15 2026", "80.0kg")  # контроль-негатив: хорошая строка проходит
    # состав тела: битые значения → None (поле теряется, строка не падает); хорошие → число
    bad_bc = parse_body_comp({"Body Fat": "999%", "Muscle mass": "5kg", "BMR": "1800"})
    assert bad_bc["body_fat_pct"] is None, "жир 999% должен отбиться"
    assert bad_bc["muscle_mass_kg"] is None, "мышцы 5kg (ниже sane) должны отбиться"
    assert bad_bc["bmr_kcal"] is None, "BMR без единицы kcal должен отбиться"
    good_bc = parse_body_comp({"Body Fat": "20.0%", "Muscle mass": "60.0kg",
                               "BMR": "1800kcal", "Visceral Fat": "5.0"})
    assert (good_bc["body_fat_pct"] == 20.0 and good_bc["muscle_mass_kg"] == 60.0
            and good_bc["bmr_kcal"] == 1800.0 and good_bc["visceral_fat"] == 5.0), \
        "хороший состав тела должен пройти"
    return True


def aggregate_morning(rows):
    """rows → ({date_iso: {"weight": kg, <body_comp cols>...}} по УТРЕННЕМУ (самому
    раннему) замеру дня), qc. Состав тела берём из ТОЙ ЖЕ утренней строки, что и вес
    (один замер биоимпеданса)."""
    byday = defaultdict(list)  # date -> [(hh, mm, weight, raw_row)]
    parsed = rejected = 0
    reject_samples = []
    for r in rows:
        try:
            d, hh, mm, w = parse_row(r.get("Date"), r.get("Weight"))
        except QCReject as e:
            rejected += 1
            if len(reject_samples) < 10:
                reject_samples.append(str(e))
            continue
        parsed += 1
        byday[d].append((hh, mm, w, r))
    daily = {}
    for d, lst in byday.items():
        lst.sort(key=lambda t: (t[0], t[1]))   # самое раннее время = утро
        _, _, w, r = lst[0]
        rec = {"weight": round(w, 1)}
        rec.update(parse_body_comp(r))
        daily[d] = rec
    qc = dict(rows_parsed=parsed, rows_rejected=rejected,
              reject_samples=reject_samples, unique_days=len(daily))
    return daily, qc


def _db_path():
    return db.DB_PATH  # канонический путь (R1/R2: не реконструировать iCloud-путь руками)


def run(csv_path, do_write):
    log.info(f"CSV: {csv_path}")
    positive_control()
    log.info("positive control: PASS (битые строки отбиты, хорошая принята)")

    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    daily, qc = aggregate_morning(rows)
    log.info(f"raw rows: {len(rows)} | parsed: {qc['rows_parsed']} | "
             f"rejected: {qc['rows_rejected']} | unique days: {qc['unique_days']}")
    if qc["rows_rejected"]:
        log.warning(f"rejected samples: {qc['reject_samples']}")

    dates = sorted(daily)
    byyear = defaultdict(list)
    for d in dates:
        byyear[d[:4]].append(daily[d]["weight"])
    for y in sorted(byyear):
        log.info(f"  {y}: n_days={len(byyear[y])} mean_weight={statistics.mean(byyear[y]):.1f}")
    log.info(f"date range: {dates[0]} .. {dates[-1]}")

    cols = ["weight"] + [c for c, *_ in BODY_COMP]

    # ── Классификация против БД (чтение): по каждой колонке ──────────────────
    plan = {c: {"ins": 0, "fill": 0, "skip": 0} for c in cols}
    with db.get_conn() as conn:
        before = {c: conn.execute(f"SELECT COUNT({c}) FROM daily_metrics").fetchone()[0]
                  for c in cols}
        collist = ", ".join(cols)
        for d in dates:
            rec = daily[d]
            row = conn.execute(f"SELECT {collist} FROM daily_metrics WHERE date=?", (d,)).fetchone()
            for i, c in enumerate(cols):
                if rec.get(c) is None:
                    continue
                if row is None:
                    plan[c]["ins"] += 1
                elif row[i] is None:
                    plan[c]["fill"] += 1
                else:
                    plan[c]["skip"] += 1

    for c in cols:
        p = plan[c]
        after = before[c] + p["ins"] + p["fill"]
        log.info(f"[plan] {c:<20} до={before[c]:<5} +ins={p['ins']:<4} +fill={p['fill']:<4} "
                 f"skip={p['skip']:<4} -> после={after}")

    if not do_write:
        log.info("DRY-RUN: бэкапа нет, записи нет.")
        return

    # ── Запись: бэкап + аддитивный COALESCE по всем колонкам (только Studio) ──
    src = _db_path()
    ts = get_now().strftime("%Y%m%d_%H%M%S")
    backup = src.with_name(f"health.db.preop_fitdays2_{ts}")
    shutil.copy2(src, backup)
    log.info(f"backup: {backup}")

    wrote_ins = wrote_upd = 0
    with db.get_conn() as conn:
        for d in dates:
            rec = daily[d]
            present = [(c, rec[c]) for c in cols if rec.get(c) is not None]
            if not present:
                continue
            row = conn.execute("SELECT date FROM daily_metrics WHERE date=?", (d,)).fetchone()
            if row is None:
                names = ", ".join(c for c, _ in present)
                ph = ", ".join("?" for _ in present)
                conn.execute(f"INSERT INTO daily_metrics (date, {names}) VALUES (?, {ph})",
                             (d, *[v for _, v in present]))
                wrote_ins += 1
            else:
                sets = ", ".join(f"{c} = COALESCE({c}, ?)" for c, _ in present)
                conn.execute(f"UPDATE daily_metrics SET {sets} WHERE date=?",
                             (*[v for _, v in present], d))
                wrote_upd += 1
        conn.commit()
    log.info(f"WROTE. inserted={wrote_ins} updated={wrote_upd} (backup: {backup})")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Импорт Fitdays: вес (Фаза 1) + состав тела (Фаза 2).")
    ap.add_argument("--csv", default=str(DEFAULT_CSV), help="путь к экспорту Fitdays*.csv")
    ap.add_argument("--write", action="store_true",
                    help="выполнить бэкап+запись (по умолчанию — dry-run без записи)")
    ap.add_argument("--self-test", action="store_true", help="только позитивный контроль и выход")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])

    if args.self_test:
        positive_control()
        log.info("self-test: PASS")
        return
    run(Path(args.csv), do_write=args.write)


if __name__ == "__main__":
    main()
