#!/usr/bin/env python3.11
"""
lab_freshness_twotier.py — двухуровневые сроки валидности анализов.

Дизайн (2026-07-01, по требованию пользователя):
  interval_stable_days  — базовый срок для СТАБИЛЬНОГО состояния (для ВСЕХ аналитов,
                          из биовариабельности + гайдлайн-паттернов RCPath G147).
  interval_doctor_days  — рекомендация врача; ДОМИНИРУЕТ, когда заполнена.
  effective = doctor ?? stable.

Существующие записи source∈{manual,encounter} = рекомендации врача → их interval
переносится в interval_doctor_days. Снапшот перед записью.

Разовый прогон на Studio. НЕ трогает клинику — только таблицу расписания.
"""
from _time_inject import get_now  # seam
import shutil
import datetime as dt
from pathlib import Path

import health_db
import lab_canon

# Стабильные дефолты (дни) — «сколько результат репрезентативен в стабильном
# амбулаторном мониторинге». Явные значения для ключевых, дальше — по категории.
STABLE = {
    # CBC + индексы (эритроцит/тромбоцит/лейко живут ~цикл эритроцита)
    "HGB": 90, "HCT": 90, "RBC": 90, "MCV": 90, "MCH": 90, "MCHC": 90,
    "RDW": 90, "PLT": 90, "MPV": 90, "WBC": 90,
    "Neutrophils_pct": 90, "Neutrophils_abs": 90, "Lymphocytes_pct": 90,
    "Lymphocytes_abs": 90, "Monocytes_pct": 90, "Monocytes_abs": 90,
    "Basophils_pct": 90, "Basophils_abs": 90, "Eosinophils_pct": 90, "Eosinophils_abs": 90,
    # печень (high — гепатотоксичная терапия)
    "ALT": 90, "AST": 90, "GGT": 90, "ALP": 120, "Bilirubin_total": 120, "LDH": 90,
    # почки/электролиты (волатильны, но для стабильного тренда)
    # BUN — азот мочевины, та же величина, что Urea, в другой форме (÷2.14); разведены
    # 2026-07-29. Каденция наследуется от мочевины, а не берётся из CATEGORY_DEFAULT:
    # иначе аналит, доросший до порога автодобавления, молча получил бы 180 вместо 120.
    # Для редких рядов расписание вручную не заводится: порог
    # `HAVING COUNT(*) >= 4` в main() не допускает недостаточно наблюдений.
    "Creatinine": 120, "Urea": 120, "BUN": 120, "Uric_acid": 180,
    "Sodium": 120, "Potassium": 120, "Chloride": 120,
    "Calcium": 120, "Magnesium": 180, "Phosphorus": 180,
    # метаболизм / липиды
    "Glucose": 90, "Cholesterol_Total": 180, "HDL": 180, "LDL": 180,
    "VLDL": 180, "Triglycerides": 180, "Chol_HDL_ratio": 180,
    # белки
    "Total_Protein": 180, "Albumin": 120, "Globulin": 180,
    # ферменты
    "Amylase": 180, "Amylase_pancreatic": 180, "CPK": 180,
    # железо/нутриенты
    "Iron": 180, "Ferritin": 180, "Vitamin_B12": 180, "Folate": 180, "Vitamin_D": 180,
    # эндокрин / маркеры (обычно врач-driven, но базовый дефолт)
    "TSH": 365, "CRP": 90, "ESR": 90, "HbA1c": 120,
    "CEA": 105, "CA19-9": 105, "CA125": 120, "Troponin": 30, "NT_proBNP": 90,
}
CATEGORY_DEFAULT = 180  # неизвестный аналит — консервативно


def stable_for(canon):
    return STABLE.get(canon, CATEGORY_DEFAULT)


def main():
    dbp = Path(health_db.DB_PATH)
    snap = str(dbp) + ".presnap_twotier_" + get_now().strftime("%Y%m%d_%H%M%S") + ".db"
    shutil.copy2(str(dbp), snap)
    print("snapshot:", snap)

    with health_db.get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(lab_monitoring_schedule)")]
        if "interval_stable_days" not in cols:
            conn.execute("ALTER TABLE lab_monitoring_schedule ADD COLUMN interval_stable_days INTEGER")
        if "interval_doctor_days" not in cols:
            conn.execute("ALTER TABLE lab_monitoring_schedule ADD COLUMN interval_doctor_days INTEGER")

        # существующие: врачебные (encounter/manual) → в doctor; всем — stable-дефолт
        rows = [dict(r) for r in conn.execute("SELECT * FROM lab_monitoring_schedule")]
        for r in rows:
            canon = lab_canon.normalize(r["test_name"])
            stable = stable_for(canon)
            doctor = r["interval_days"] if (r["source"] or "").split(":")[0] in ("manual", "encounter") else None
            conn.execute(
                "UPDATE lab_monitoring_schedule SET interval_stable_days=?, interval_doctor_days=? WHERE test_name=?",
                (stable, doctor, r["test_name"]))

        # дозаполняем ВСЕ наши аналиты канона, которых нет в таблице
        have = {lab_canon.normalize(r["test_name"]) for r in rows}
        our = [r[0] for r in conn.execute(
            "SELECT test_name FROM lab_results WHERE source LIKE ? AND value IS NOT NULL "
            "GROUP BY test_name HAVING COUNT(*) >= 4", ("doc:%",))]
        added = 0
        for tn in our:
            canon = lab_canon.normalize(tn)
            if canon in have:
                continue
            have.add(canon)
            conn.execute(
                "INSERT INTO lab_monitoring_schedule (test_name, interval_days, priority, source, "
                "interval_stable_days, interval_doctor_days) VALUES (?,?,?,?,?,?)",
                (canon, stable_for(canon), "medium", "stable_default", stable_for(canon), None))
            added += 1
        conn.commit()

    print(f"добавлено новых аналитов: {added}")
    with health_db.get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM lab_monitoring_schedule").fetchone()[0]
        docn = conn.execute("SELECT COUNT(*) FROM lab_monitoring_schedule WHERE interval_doctor_days IS NOT NULL").fetchone()[0]
    print(f"всего в расписании: {n} · с врачебным сроком: {docn}")


if __name__ == "__main__":
    main()
