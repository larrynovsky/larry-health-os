#!/usr/bin/env python3.11
"""
Многолетний анализ конституции здоровья по 5 доменам:
  1. Сон           — sleep_total (весь ряд), sleep_deep (с появления кольца), циркадный геном
  2. Движение      — active_kcal, resting_hr (cardiovascular fitness)
  3. Стресс/HRV    — resting_hr как долгосрочный прокси, HRV (с появления кольца), геном
  4. Метаболизм    — глюкоза, липиды, амилаза, MCV/RDW из lab_results
  5. Онкология     — CEA/CA19-9/Hgb, хронология лечения, геном
"""
import sqlite3, json, statistics, logging
from datetime import datetime, date
from _time_inject import get_today  # seam
from pathlib import Path
import health_db as _hdb

DB_PATH = _hdb.DB_PATH  # единственный источник пути к БД

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Периоды лечения — из DB (W3-4, Rule #9) ──────────────────────────────────
# Загружается при старте модуля; при пустой таблице — log.warning + пустой список.

def _load_treatment_periods() -> list:
    """Читает не-soft-deleted non-travel/baseline периоды из DB. Fallback: [] + warning.

    PERIODS-SEMANTICS (2026-06-19): мигрировано на historical_periods()
    (D++ hybrid). include_deleted=False — soft-deleted (#9, #17) исключены.
    """
    try:
        rows = _hdb.historical_periods(exclude_types=['travel', 'baseline'])
        if not rows:
            log.warning("constitution_analysis: periods пустая, get_period вернёт 'baseline' для всех дат")
        return [{"name": r["name"], "start": r["start_date"], "end": r["end_date"] or "2099-12-31"}
                for r in rows]
    except Exception as e:
        log.error(f"constitution_analysis: не удалось загрузить periods: {e}")
        return []


TREATMENT_PERIODS: list = _load_treatment_periods()


def get_period(date_str: str) -> str:
    for p in TREATMENT_PERIODS:
        if p["start"] <= date_str <= p["end"]:
            return p["name"]
    return "baseline"

def connect():
    return _hdb.get_conn()

# ── Зиготность (inline, без импорта genome_context) ───────────────────────────
def zygosity(genotype: str, effect_allele: str) -> str:
    if not effect_allele or len(effect_allele) > 2:
        return "unknown"
    gt = (genotype or "").upper().replace("/", "").replace(" ", "")
    ea = effect_allele.upper()
    if not gt or gt in ("--", "II", "DI", "DD"):
        return "unknown"
    count = gt.count(ea)
    if count == 0:   return "wildtype"
    elif count == 1: return "hetero"
    else:            return "homo_risk"

# ── Очистка lab_results: убираем мусорные значения ──────────────────────────
LAB_RANGES = {
    "CEA":         (0, 50),
    "CA19-9":      (0, 200),
    "CA19.9":      (0, 200),
    "CA19_9":      (0, 200),
    "Glucose":     (50, 500),
    "Amylase":     (10, 1000),
    "RDW":         (10, 40),
    "Hemoglobin":  (5, 25),
    "WBC":         (0.5, 30),
    "MCV":         (60, 130),
    "LDL":         (20, 300),
    "HDL":         (10, 150),
    "Cholesterol": (80, 400),
    "Triglycerides": (20, 1000),
    "ALT":         (1, 500),
    "AST":         (1, 500),
    "Creatinine":  (0.3, 10),
}

def clean_labs(rows):
    """Дедупликация по (date, test_name): берём значение в физиологическом диапазоне."""
    seen = {}
    for r in rows:
        key = (r["date"], r["test_name"])
        val = r["value"]
        bounds = LAB_RANGES.get(r["test_name"])
        if bounds and not (bounds[0] <= val <= bounds[1]):
            continue
        if key not in seen:
            seen[key] = dict(r)
    return list(seen.values())

# ─────────────────────────────────────────────────────────────────────────────
# ДОМЕН 1: СОН
# ─────────────────────────────────────────────────────────────────────────────
def analyze_sleep(db) -> dict:
    rows = db.execute("""
        SELECT strftime('%Y', date) as yr, date,
               sleep_total, sleep_deep, sleep_score
        FROM daily_metrics
        WHERE sleep_total IS NOT NULL AND sleep_total > 0
        ORDER BY date
    """).fetchall()

    yearly = {}
    for r in rows:
        yr = r["yr"]
        if yr not in yearly:
            yearly[yr] = {"total": [], "deep": []}
        yearly[yr]["total"].append(r["sleep_total"])
        if r["sleep_deep"] and r["sleep_deep"] > 0:
            yearly[yr]["deep"].append(r["sleep_deep"])

    trend = {}
    for yr, vals in sorted(yearly.items()):
        avg_total = round(statistics.mean(vals["total"]), 2)
        deficit_h = round(max(0, 7.5 - avg_total), 2)   # vs оптимум 7.5ч
        trend[yr] = {
            "avg_sleep_h":  avg_total,
            "n_days":       len(vals["total"]),
            "deficit_h":    deficit_h,
            "avg_deep_min": round(statistics.mean(vals["deep"]), 1) if vals["deep"] else None,
        }

    # Циркадный геном (sleep_circadian) — только реальные носители
    sleep_genome = db.execute("""
        SELECT rsid, gene, genotype, significance, effect_allele, clinical_summary
        FROM genetic_variants
        WHERE domain_tags LIKE '%sleep%'
          AND significance NOT IN ('Benign','Likely benign','Benign/Likely benign','not provided','')
        ORDER BY significance
    """).fetchall()

    genome_risk = []
    genome_unverified = []   # NULL effect_allele: «неизвестно» ≠ «чисто» — сюрфейсим отдельно
    for g in sleep_genome:
        zyg = zygosity(g["genotype"], g["effect_allele"])
        if zyg == "unknown":
            # NULL effect_allele: strand не разрешён → «неизвестно», НЕ «чисто».
            # Не сливать с verified wildtype (инвариант null_is_unknown_not_clean).
            genome_unverified.append({"rsid": g["rsid"], "gene": g["gene"],
                                      "significance": g["significance"]})
            continue
        if zyg not in ("wildtype", "unknown"):
            genome_risk.append({
                "rsid": g["rsid"], "gene": g["gene"],
                "genotype": g["genotype"], "significance": g["significance"],
                "zygosity": zyg, "summary": (g["clinical_summary"] or "")[:120],
            })

    # Период деградации сна
    total_by_yr = {yr: v["avg_sleep_h"] for yr, v in trend.items()}
    min_yr = min(total_by_yr, key=total_by_yr.get)
    max_yr = max(total_by_yr, key=total_by_yr.get)

    return {
        "domain": "sleep",
        "yearly_trend": trend,
        "worst_year": {"year": min_yr, "avg_h": total_by_yr[min_yr]},
        "best_year":  {"year": max_yr, "avg_h": total_by_yr[max_yr]},
        "genome_risk_count": len(genome_risk),
        "genome_unverified_count": len(genome_unverified),
        "genome_unverified": genome_unverified[:10],
        "genome_risk": genome_risk[:10],
        # NEUTRAL: заметка строится только из данных текущего тенанта.
        # Удалённые персональные умолчания не должны попадать в общий шаблон.
        "note": f"2026: средний сон {trend.get('2026',{}).get('avg_sleep_h','?')}ч/ночь",
    }

# ─────────────────────────────────────────────────────────────────────────────
# ДОМЕН 2: ДВИЖЕНИЕ / КАРДИО
# ─────────────────────────────────────────────────────────────────────────────
def analyze_movement(db) -> dict:
    rows = db.execute("""
        SELECT strftime('%Y', date) as yr, date,
               resting_hr, active_kcal, steps, vo2max
        FROM daily_metrics
        ORDER BY date
    """).fetchall()

    yearly = {}
    for r in rows:
        yr = r["yr"]
        if yr not in yearly:
            yearly[yr] = {"hr": [], "kcal": [], "steps": [], "vo2": []}
        if r["resting_hr"] and 35 < r["resting_hr"] < 120:
            yearly[yr]["hr"].append(r["resting_hr"])
        if r["active_kcal"] and r["active_kcal"] > 0:
            yearly[yr]["kcal"].append(r["active_kcal"])
        if r["steps"] and r["steps"] > 0:
            yearly[yr]["steps"].append(r["steps"])
        if r["vo2max"] and r["vo2max"] > 0:
            yearly[yr]["vo2"].append(r["vo2max"])

    trend = {}
    for yr, vals in sorted(yearly.items()):
        entry = {"n_days": max(len(vals["hr"]), len(vals["kcal"]))}
        if vals["hr"]:
            entry["avg_rhr"] = round(statistics.mean(vals["hr"]), 1)
        if vals["kcal"]:
            entry["avg_kcal"] = round(statistics.mean(vals["kcal"]), 0)
        if vals["steps"]:
            entry["avg_steps"] = round(statistics.mean(vals["steps"]), 0)
        if vals["vo2"]:
            entry["avg_vo2max"] = round(statistics.mean(vals["vo2"]), 1)
        trend[yr] = entry

    # Геном: кардио + мышцы
    move_genome = db.execute("""
        SELECT rsid, gene, genotype, significance, effect_allele, domain_tags, clinical_summary
        FROM genetic_variants
        WHERE (domain_tags LIKE '%musculoskeletal%' OR domain_tags LIKE '%cardiology%')
          AND significance IN ('Pathogenic','Likely pathogenic','risk factor')
          AND domain_tags NOT LIKE '%oncology%'
        ORDER BY significance
        LIMIT 50
    """).fetchall()

    genome_risk = []
    genome_unverified = []   # NULL effect_allele: «неизвестно» ≠ «чисто» — сюрфейсим отдельно
    for g in move_genome:
        zyg = zygosity(g["genotype"], g["effect_allele"])
        if zyg == "unknown":
            # NULL effect_allele: strand не разрешён → «неизвестно», НЕ «чисто».
            # Не сливать с verified wildtype (инвариант null_is_unknown_not_clean).
            genome_unverified.append({"rsid": g["rsid"], "gene": g["gene"],
                                      "significance": g["significance"]})
            continue
        if zyg not in ("wildtype", "unknown"):
            genome_risk.append({
                "rsid": g["rsid"], "gene": g["gene"],
                "genotype": g["genotype"], "significance": g["significance"],
                "zygosity": zyg, "domains": g["domain_tags"],
                "summary": (g["clinical_summary"] or "")[:120],
            })

    # Кардио-динамика: снижение ЧСС = улучшение
    hrs = {yr: v["avg_rhr"] for yr, v in trend.items() if "avg_rhr" in v}
    hr_change = None
    if hrs:
        yrs_sorted = sorted(hrs.keys())
        hr_change = round(hrs[yrs_sorted[-1]] - hrs[yrs_sorted[0]], 1)

    return {
        "domain": "movement",
        "yearly_trend": trend,
        "rhr_change_total": hr_change,
        "rhr_interpretation": "кардио улучшилось" if (hr_change and hr_change < -2) else "без значимых изменений",
        "peak_activity_year": max(
            {yr: v.get("avg_kcal", 0) for yr, v in trend.items()},
            key=lambda y: trend[y].get("avg_kcal", 0)
        ) if trend else None,
        "genome_risk_count": len(genome_risk),
        "genome_unverified_count": len(genome_unverified),
        "genome_unverified": genome_unverified[:10],
        "genome_risk": genome_risk[:8],
    }

# ─────────────────────────────────────────────────────────────────────────────
# ДОМЕН 3: СТРЕСС / АВТОНОМНАЯ НС
# ─────────────────────────────────────────────────────────────────────────────
def analyze_stress(db) -> dict:
    # HRV — с первой записи прибора у ЭТОГО тенанта (даты рядов из данных, BL-PUB-16 г)
    hrv_rows = db.execute("""
        SELECT strftime('%Y-%m', date) as ym, date,
               hrv, readiness, resting_hr
        FROM daily_metrics
        WHERE hrv IS NOT NULL AND hrv > 0
        ORDER BY date
    """).fetchall()

    monthly_hrv = {}
    for r in hrv_rows:
        ym = r["ym"]
        if ym not in monthly_hrv:
            monthly_hrv[ym] = {"hrv": [], "readiness": [], "period": get_period(r["date"])}
        monthly_hrv[ym]["hrv"].append(r["hrv"])
        if r["readiness"]:
            monthly_hrv[ym]["readiness"].append(r["readiness"])

    hrv_trend = {}
    for ym, vals in sorted(monthly_hrv.items()):
        hrv_trend[ym] = {
            "avg_hrv": round(statistics.mean(vals["hrv"]), 1),
            "period":  vals["period"],
            "avg_readiness": round(statistics.mean(vals["readiness"]), 1) if vals["readiness"] else None,
        }

    # COMT rs4680 — ключевой ген стресса
    stress_genome = db.execute("""
        SELECT rsid, gene, genotype, significance, effect_allele, clinical_summary
        FROM genetic_variants
        WHERE (gene IN ('COMT','MAOA','MAOB','SLC6A4','HTR2A','BDNF','NR3C1','CRH')
               OR domain_tags LIKE '%neurology%')
          AND significance NOT IN ('Benign','Likely benign','Benign/Likely benign','not provided','')
        ORDER BY significance
        LIMIT 50
    """).fetchall()

    genome_risk = []
    genome_unverified = []   # NULL effect_allele: «неизвестно» ≠ «чисто» — сюрфейсим отдельно
    for g in stress_genome:
        zyg = zygosity(g["genotype"], g["effect_allele"])
        if zyg == "unknown":
            # NULL effect_allele: strand не разрешён → «неизвестно», НЕ «чисто».
            # Не сливать с verified wildtype (инвариант null_is_unknown_not_clean).
            genome_unverified.append({"rsid": g["rsid"], "gene": g["gene"],
                                      "significance": g["significance"]})
            continue
        if zyg not in ("wildtype", "unknown"):
            genome_risk.append({
                "rsid": g["rsid"], "gene": g["gene"],
                "genotype": g["genotype"], "significance": g["significance"],
                "zygosity": zyg, "summary": (g["clinical_summary"] or "")[:120],
            })

    # RHR как долгосрочный прокси стресса/автономной нс
    rhr_by_period = {}
    rhr_rows = db.execute("""
        SELECT date, resting_hr FROM daily_metrics
        WHERE resting_hr IS NOT NULL AND resting_hr BETWEEN 35 AND 120
    """).fetchall()
    for r in rhr_rows:
        p = get_period(r["date"])
        if p not in rhr_by_period:
            rhr_by_period[p] = []
        rhr_by_period[p].append(r["resting_hr"])

    rhr_period_avg = {p: round(statistics.mean(v), 1) for p, v in rhr_by_period.items() if v}

    return {
        "domain": "stress",
        "hrv_monthly": hrv_trend,
        "rhr_by_treatment_period": rhr_period_avg,
        "genome_risk_count": len(genome_risk),
        "genome_unverified_count": len(genome_unverified),
        "genome_unverified": genome_unverified[:10],
        "genome_risk": genome_risk[:8],
        "hrv_since": hrv_rows[0]["date"] if hrv_rows else None,
        "span": tuple(db.execute("SELECT MIN(date), MAX(date) FROM daily_metrics").fetchone() or (None, None)),
        "note": (f"HRV — с {hrv_rows[0]['date']} (прибор). " if hrv_rows else "HRV нет. ")
                + "ЧСС покоя — прокси автономной НС за весь ряд.",
    }

# ─────────────────────────────────────────────────────────────────────────────
# ДОМЕН 4: МЕТАБОЛИЗМ / ПИТАНИЕ
# ─────────────────────────────────────────────────────────────────────────────
def analyze_metabolism(db) -> dict:
    raw = db.execute("""
        SELECT date, test_name, value, unit, status
        FROM lab_results
        WHERE test_name IN ('Glucose','Amylase','RDW','MCV','Cholesterol',
                            'LDL','HDL','Triglycerides','ALT','AST','Creatinine')
        ORDER BY test_name, date
    """).fetchall()

    labs = clean_labs([dict(r) for r in raw])

    # По маркерам: хронология
    markers = {}
    for row in labs:
        tn = row["test_name"]
        if tn not in markers:
            markers[tn] = []
        markers[tn].append({
            "date": row["date"],
            "value": row["value"],
            "status": row["status"],
            "period": get_period(row["date"]),
        })

    # Суммарная статистика по маркерам
    summary = {}
    for tn, vals in markers.items():
        values = [v["value"] for v in vals]
        summary[tn] = {
            "n": len(values),
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "last": vals[-1],
            "trend": "↑" if len(values) >= 2 and values[-1] > values[-2] else
                     "↓" if len(values) >= 2 and values[-1] < values[-2] else "→",
        }

    # Ключевые наблюдения.
    # NEUTRAL (brief-neutralization Фаза 0): флаги — ТОЛЬКО дата-факты (счётчик точек, значения,
    # даты из реальных записей тенанта). Убраны личные интерпретации лечения/хронологии
    # владельца — они утекали всем тенантам как код-константы (движок нейтрален; клиническая
    # трактовка — не в коде).
    flags = []
    rdw = [v for v in markers.get("RDW", []) if v["value"] > 15]
    if rdw:
        flags.append(f"RDW >15% в {len(rdw)} точках; пик {max(v['value'] for v in rdw):.1f}% в {max(rdw, key=lambda x: x['value'])['date']}")

    mcv = [v for v in markers.get("MCV", []) if v["value"] > 95]
    if mcv:
        flags.append(f"MCV >95 fL в {len(mcv)} точках; последнее {mcv[-1]['value']} ({mcv[-1]['date']})")

    glu = [v for v in markers.get("Glucose", []) if v["value"] > 100]
    if glu:
        flags.append(f"Глюкоза >100 в {len(glu)} точках; последнее {glu[-1]['value']} ({glu[-1]['date']})")

    amy = [v for v in markers.get("Amylase", []) if v["value"] > 100]
    if amy:
        flags.append(f"Амилаза >100 U/L в {len(amy)} точках; последнее {amy[-1]['value']} ({amy[-1]['date']})")

    # Геном метаболизма
    meta_genome = db.execute("""
        SELECT rsid, gene, genotype, significance, effect_allele, domain_tags, clinical_summary
        FROM genetic_variants
        WHERE domain_tags LIKE '%metabolism%' OR domain_tags LIKE '%endocrinology%'
          AND significance NOT IN ('Benign','Likely benign','Benign/Likely benign','not provided','')
        ORDER BY significance
        LIMIT 50
    """).fetchall()

    genome_risk = []
    genome_unverified = []   # NULL effect_allele: «неизвестно» ≠ «чисто» — сюрфейсим отдельно
    for g in meta_genome:
        zyg = zygosity(g["genotype"], g["effect_allele"])
        if zyg == "unknown":
            # NULL effect_allele: strand не разрешён → «неизвестно», НЕ «чисто».
            # Не сливать с verified wildtype (инвариант null_is_unknown_not_clean).
            genome_unverified.append({"rsid": g["rsid"], "gene": g["gene"],
                                      "significance": g["significance"]})
            continue
        if zyg not in ("wildtype", "unknown"):
            genome_risk.append({
                "rsid": g["rsid"], "gene": g["gene"],
                "genotype": g["genotype"], "significance": g["significance"],
                "zygosity": zyg, "summary": (g["clinical_summary"] or "")[:120],
            })

    return {
        "domain": "metabolism",
        "marker_summary": summary,
        "key_flags": flags,
        "genome_risk_count": len(genome_risk),
        "genome_unverified_count": len(genome_unverified),
        "genome_unverified": genome_unverified[:10],
        "genome_risk": genome_risk[:8],
    }

# ─────────────────────────────────────────────────────────────────────────────
# ДОМЕН 5: ОНКОЛОГИЯ / ИММУНИТЕТ
# ─────────────────────────────────────────────────────────────────────────────
def analyze_oncology(db) -> dict:
    raw = db.execute("""
        SELECT date, test_name, value, unit, status
        FROM lab_results
        WHERE test_name IN ('CEA','CA19-9','CA19.9','Hemoglobin','WBC',
                            'Neutrophils_abs','Lymphocytes_abs')
        ORDER BY test_name, date
    """).fetchall()

    labs = clean_labs([dict(r) for r in raw])

    # Консолидируем CA19-9 / CA19.9 → CA19-9
    for row in labs:
        if row["test_name"] in ("CA19.9", "CA19_9"):
            row["test_name"] = "CA19-9"

    markers = {}
    for row in labs:
        tn = row["test_name"]
        if tn not in markers:
            markers[tn] = []
        markers[tn].append({
            "date": row["date"], "value": row["value"],
            "status": row["status"], "period": get_period(row["date"]),
        })

    # CEA хронология
    cea_timeline = markers.get("CEA", [])
    ca_timeline  = markers.get("CA19-9", [])

    # Иммунологический геном
    onco_genome = db.execute("""
        SELECT rsid, gene, genotype, significance, effect_allele, clinical_summary
        FROM genetic_variants
        WHERE domain_tags LIKE '%oncology%'
          AND significance IN ('Pathogenic','Likely pathogenic','risk factor')
        ORDER BY significance
        LIMIT 100
    """).fetchall()

    genome_risk = []
    genome_unverified = []   # NULL effect_allele: «неизвестно» ≠ «чисто» — сюрфейсим отдельно
    for g in onco_genome:
        zyg = zygosity(g["genotype"], g["effect_allele"])
        if zyg == "unknown":
            # NULL effect_allele: strand не разрешён → «неизвестно», НЕ «чисто».
            # Не сливать с verified wildtype (инвариант null_is_unknown_not_clean).
            genome_unverified.append({"rsid": g["rsid"], "gene": g["gene"],
                                      "significance": g["significance"]})
            continue
        if zyg not in ("wildtype", "unknown"):
            genome_risk.append({
                "rsid": g["rsid"], "gene": g["gene"],
                "genotype": g["genotype"], "significance": g["significance"],
                "zygosity": zyg, "summary": (g["clinical_summary"] or "")[:120],
            })

    # Хронология лечения + маркеры
    timeline = []
    for p in TREATMENT_PERIODS:
        cea_in_period = [v for v in cea_timeline if p["start"] <= v["date"] <= p["end"]]
        ca_in_period  = [v for v in ca_timeline  if p["start"] <= v["date"] <= p["end"]]
        timeline.append({
            "period": p["name"],
            "start": p["start"], "end": p["end"],
            "cea_range": [round(min(v["value"] for v in cea_in_period), 2),
                          round(max(v["value"] for v in cea_in_period), 2)] if cea_in_period else None,
            "ca199_range": [round(min(v["value"] for v in ca_in_period), 2),
                            round(max(v["value"] for v in ca_in_period), 2)] if ca_in_period else None,
        })

    # NEUTRAL (brief-neutralization Фаза 0): заметка выводится из ДАННЫХ тенанта, а не из
    # личного результата. Раньше здесь был вшитый личный результат обследования владельца →
    # он утекал в agent_reports любого тенанта, на котором запускался скрипт (движок нейтрален).
    if cea_timeline:
        note = f"CEA: {len(cea_timeline)} точек, последнее {cea_timeline[-1]['value']} ({cea_timeline[-1]['date']})"
    else:
        note = "CEA: данных нет"

    # has_data — нейтральный гейт рендера онко-секции: секция показывается, только если у
    # тенанта ЕСТЬ онко-данные (лабы/геном). Без хардкода имени болезни — по факту данных.
    has_data = bool(cea_timeline or ca_timeline or genome_risk or genome_unverified)

    return {
        "domain": "oncology",
        "has_data": has_data,
        "cea_timeline": cea_timeline,
        "ca199_timeline": ca_timeline,
        "treatment_timeline": timeline,
        "genome_oncology_risk_count": len(genome_risk),
        "genome_unverified_count": len(genome_unverified),
        "genome_unverified": genome_unverified[:10],
        "genome_oncology_risk": genome_risk[:10],
        "note": note,
    }

# ─────────────────────────────────────────────────────────────────────────────
# СВОДНЫЙ ОТЧЁТ
# ─────────────────────────────────────────────────────────────────────────────
def _span_label(span) -> str:
    """«N ЛЕТ (ГГГГ–ГГГГ)» по данным тенанта; до 27.09 стоял литерал ряда владельца."""
    lo, hi = (span or (None, None))
    if not lo or not hi:
        return "ДАННЫХ НЕТ"
    y0, y1 = int(str(lo)[:4]), int(str(hi)[:4])
    return f"{y1 - y0} ЛЕТ ({y0}–{y1})"


def build_summary(domains: dict) -> str:
    s = domains["sleep"]
    m = domains["movement"]
    st = domains["stress"]
    me = domains["metabolism"]
    o = domains["oncology"]

    lines = [
        "═══════════════════════════════════════════════════════════",
        "   КОНСТИТУЦИОННЫЙ АНАЛИЗ ЗДОРОВЬЯ — " + _span_label(st.get("span")),
        "═══════════════════════════════════════════════════════════",
        "",
        "─── 1. СОН ─────────────────────────────────────────────",
    ]

    for yr, v in sorted(s["yearly_trend"].items()):
        deficit = f" (дефицит {v['deficit_h']}ч)" if v["deficit_h"] > 0.3 else ""
        deep = f" | deep {v['avg_deep_min']}мин" if v.get("avg_deep_min") else ""
        lines.append(f"  {yr}: {v['avg_sleep_h']}ч/ночь{deficit}{deep}  [{v['n_days']} дней]")

    lines += [
        f"  Худший год: {s['worst_year']['year']} ({s['worst_year']['avg_h']}ч)",
        f"  Геном circadian-риска (носители): {s['genome_risk_count']} вариантов",
        f"  Геном circadian: не верифицировано (strand не разрешён, ≠ «нет риска»): {s['genome_unverified_count']}",
        f"  Примечание: {s['note']}",
        "",
        "─── 2. ДВИЖЕНИЕ / КАРДИО ───────────────────────────────",
    ]

    for yr, v in sorted(m["yearly_trend"].items()):
        parts = []
        if "avg_rhr" in v: parts.append(f"ЧСС {v['avg_rhr']}")
        if "avg_kcal" in v: parts.append(f"kcal {int(v['avg_kcal'])}")
        if "avg_steps" in v: parts.append(f"шаги {int(v['avg_steps'])}")
        if parts:
            lines.append(f"  {yr}: " + " | ".join(parts) + f"  [{v['n_days']} дней]")

    lines += [
        f"  Изменение ЧСС покоя за период: {m['rhr_change_total']:+.1f} уд/мин → {m['rhr_interpretation']}",
        f"  Пиковый год активности: {m['peak_activity_year']}",
        f"  Геном кардио/мышечного риска (носители): {m['genome_risk_count']} вариантов",
        f"  Геном кардио/мышечн.: не верифицировано (strand не разрешён, ≠ «нет риска»): {m['genome_unverified_count']}",
        "",
        "─── 3. СТРЕСС / АВТОНОМНАЯ НС ─────────────────────────",
    ]

    lines.append("  ЧСС покоя по периодам лечения:")
    for period, rhr in st["rhr_by_treatment_period"].items():
        lines.append(f"    {period}: {rhr} уд/мин")

    if st["hrv_monthly"]:
        lines.append(f"  HRV (с {st.get('hrv_since') or '?'}):")
        for ym, v in sorted(st["hrv_monthly"].items()):
            rdns = f" | readiness {v['avg_readiness']}" if v.get("avg_readiness") else ""
            lines.append(f"    {ym}: HRV {v['avg_hrv']}  [{v['period']}]{rdns}")

    lines += [
        f"  Геном нейро/стресс-риска (носители): {st['genome_risk_count']} вариантов",
        f"  Геном нейро/стресс: не верифицировано (strand не разрешён, ≠ «нет риска»): {st['genome_unverified_count']}",
        "",
        "─── 4. МЕТАБОЛИЗМ ──────────────────────────────────────",
    ]

    for flag in me["key_flags"]:
        lines.append(f"  ⚠ {flag}")

    lines.append("  Маркеры (последнее значение):")
    for tn, v in sorted(me["marker_summary"].items()):
        last = v["last"]
        lines.append(f"    {tn}: {last['value']} ({last['date']}) {v['trend']}  [мин {v['min']} / макс {v['max']}]")

    lines += [
        f"  Геном метаболизм-риска (носители): {me['genome_risk_count']} вариантов",
        f"  Геном метаболизм: не верифицировано (strand не разрешён, ≠ «нет риска»): {me['genome_unverified_count']}",
    ]

    # NEUTRAL (brief-neutralization Фаза 0): онко-секция рендерится ТОЛЬКО если у тенанта есть
    # онко-данные. Раньше заголовок «5. ОНКОЛОГИЯ» + каркас печатались всем безусловно —
    # это диагноз-рамка в коде. Гейт нейтрален (по факту данных), без имени болезни.
    if o.get("has_data"):
        lines += [
            "",
            "─── 5. ОНКОЛОГИЯ / ИММУНИТЕТ ───────────────────────────",
        ]

        lines.append("  CEA хронология:")
        for pt in o["cea_timeline"]:
            lines.append(f"    {pt['date']}: CEA {pt['value']}  [{pt['period']}]")

        lines.append("  CEA/CA19-9 по периодам:")
        for t in o["treatment_timeline"]:
            cea_s = f"CEA {t['cea_range']}" if t["cea_range"] else "CEA нет данных"
            ca_s  = f"CA19-9 {t['ca199_range']}" if t["ca199_range"] else ""
            lines.append(f"    {t['period']}: {cea_s}  {ca_s}")

        lines += [
            f"  Геном онко-риска (носители): {o['genome_oncology_risk_count']} вариантов",
            f"  ⚠ Онко-геном: НЕ ВЕРИФИЦИРОВАНО (strand не разрешён — статус носительства НЕИЗВЕСТЕН, ≠ «чисто»): {o['genome_unverified_count']}",
            f"  Статус: {o['note']}",
        ]

    lines += [
        "",
        "═══════════════════════════════════════════════════════════",
    ]

    return "\n".join(lines)

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def run():
    db = connect()
    log.info("Анализируем конституцию здоровья...")

    sleep    = analyze_sleep(db)
    movement = analyze_movement(db)
    stress   = analyze_stress(db)
    meta     = analyze_metabolism(db)
    onco     = analyze_oncology(db)

    db.close()

    domains = {
        "sleep": sleep, "movement": movement,
        "stress": stress, "metabolism": meta, "oncology": onco,
    }

    report_text = build_summary(domains)

    # Promethease геномная конституция — добавляем к отчёту (Tier 3, разово)
    try:
        import promethease_context as pc
        CONSTITUTION_DOMAINS = ["sleep", "stress", "metabolism", "cardio", "nutrition", "inflammation"]
        prom_sections = []
        for dom in CONSTITUTION_DOMAINS:
            block = pc.build_constitution_block(dom)
            if block:
                prom_sections.append(block)
        if prom_sections:
            report_text += (
                "\n\n═══════════════════════════════════════════════════════════\n"
                "   ГЕНОМНАЯ КОНСТИТУЦИЯ (Promethease SNP-анализ)\n"
                "═══════════════════════════════════════════════════════════"
                + "\n".join(prom_sections)
            )
    except Exception as _pe:
        log.warning(f"promethease_context constitution: {_pe}")

    print(report_text)

    # Сохраняем в agent_reports
    db2 = connect()
    payload = json.dumps(domains, ensure_ascii=False, default=str, indent=2)
    today = get_today().isoformat()
    db2.execute("""
        INSERT OR REPLACE INTO agent_reports
          (date, agent_type, agent_name, period_days, findings)
        VALUES (?, 'constitution', 'constitution_10yr', 3650, ?)
    """, (today, report_text + "\n\nJSON:\n" + payload))
    db2.commit()
    db2.close()

    log.info("Отчёт сохранён в agent_reports.")

if __name__ == "__main__":
    run()
