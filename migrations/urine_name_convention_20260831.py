"""Одна конвенция имён мочевого домена: `Urine_*` (решение владельца 2026-08-31).

ЗАЧЕМ. Замер 2026-08-31: мочевые строки канона жили в ДВУХ конвенциях разом —
`Urine_Calcium` <X> мг/л И `Calcium` <X/10> mg/dL со specimen='urine', одна
ИСП-МС панель мочи, промоутнутая двумя путями. Шесть
веществ лежали парами (значения тождественны с точностью конверсии единиц),
микроэлементы — парами «Алюминий, Al» / `Urine_Aluminium`. Владелец выбрал
префикс; правка предиката «имя обязано подходить материалу» без этого выбора
была построена и ОТКАЧЕНА в тот же день — по одной строке-поводу она выбирала
конвенцию молча.

ОТНОШЕНИЕ К ADR analyte-identity-lives-in-name (2026-08-12): НЕ отменяет.
Колонка `specimen` остаётся обязательной и авторитетной для материала; префикс —
часть ИМЕНИ мочевого аналита (моча вещества — другое измерение, не пересчёт
сывороточного), как `Urine_WBC` против `WBC` с 2026-08-08. Снятый
`Methylmalonic_acid_serum` не возвращается: у КРОВИ префикса нет.

ЧТО ДЕЛАЕТ. По urine-строкам канона:
  1. `plan_duplicates` — пара plain/Urine_* одной даты и одного вещества, чьи
     значения сходятся через `lab_canon.to_conventional` (plain-строка и есть
     конвертированный дубль) → plain удаляется, `Urine_*` (сырые единицы бланка,
     ближе к источнику) остаётся. Вещество сверяется КАРТОЙ (имя/символ), не
     только значением: одинаковое число может встречаться у разных веществ.
  2. `plan_renames` — plain-строка БЕЗ двойника → переименование в `Urine_<имя>`
     по той же карте (Methylmalonic_acid мочи «ммоль/моль креатинина»).
  3. Органические кислоты и прочие сырые имена ВНЕ карты не трогаются: их
     сведение — этап имён, не конвенция.

ЧЕГО НЕ ДЕЛАЕТ ОСОЗНАННО: не трогает кровь/кал/слюну; не меняет значения и
единицы; не перепромоутит; не сводит сырые имена к канону.

БЕЗОПАСНОСТЬ: dry-run по умолчанию, `--apply` явно, снапшот БД, одна транзакция,
лог удалённого/переименованного в backups/ (как dedup_removed_20260730).

  /opt/homebrew/bin/python3.11 -m migrations.urine_name_convention_20260831
  … то же с `--apply` — записывает.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from _time_inject import get_now

import health_db
import lab_canon

# Карты соответствий для одноразовой миграции: plain-имя или символ → Urine_*.
# Членство в исполняемых картах требует отдельного решения об обезличивании.
# Будущие строки именует распознаватель/промоут; это не общий справочник.
PLAIN_TO_URINE = {
    "Calcium": "Urine_Calcium", "Iron": "Urine_Iron", "Magnesium": "Urine_Magnesium",
    "Phosphorus": "Urine_Phosphorus", "Potassium": "Urine_Potassium",
    "Sodium": "Urine_Sodium", "Methylmalonic_acid": "Urine_Methylmalonic_acid",
}
SYMBOL_TO_URINE = {
    "Al": "Urine_Aluminium", "Sb": "Urine_Antimony", "As": "Urine_Arsenic",
    "B": "Urine_Boron", "Cd": "Urine_Cadmium", "Ca": "Urine_Calcium",
    "Cr": "Urine_Chromium", "Co": "Urine_Cobalt", "Cu": "Urine_Copper",
    "Fe": "Urine_Iron", "Pb": "Urine_Lead", "Li": "Urine_Lithium",
    "Mg": "Urine_Magnesium", "Mn": "Urine_Manganese", "Hg": "Urine_Mercury",
    "Mo": "Urine_Molybdenum", "Ni": "Urine_Nickel", "P": "Urine_Phosphorus",
    "K": "Urine_Potassium", "Se": "Urine_Selenium", "Na": "Urine_Sodium",
    "Ti": "Urine_Titanium", "Zn": "Urine_Zinc",
}


def urine_target(test_name: str) -> str | None:
    """`Urine_*`-имя для строки по карте: plain-канон либо «Имя, Символ»."""
    n = (test_name or "").strip()
    if n in PLAIN_TO_URINE:
        return PLAIN_TO_URINE[n]
    if "," in n:
        sym = n.rsplit(",", 1)[1].strip().rstrip("*")
        return SYMBOL_TO_URINE.get(sym)
    return None


def _same_measurement(plain_row, urine_row) -> bool:
    """Тождество значений с точностью конверсии: plain-строка была конвертирована
    в conventional тем же `to_conventional`, каким сверяем (один дом правила)."""
    pv, uv = plain_row["value"], urine_row["value"]
    if pv is None or uv is None:
        return pv is None and uv is None
    conv, _u = lab_canon.to_conventional(
        plain_row["test_name"], uv, urine_row.get("unit") or "")
    for cand in (uv, conv):
        try:
            scale = max(abs(float(pv)), abs(float(cand)), 1e-9)
            if abs(float(pv) - float(cand)) / scale <= 1e-3:
                return True
        except (TypeError, ValueError):
            continue
    return False


def plan_duplicates(rows: list[dict]) -> list[tuple[int, int]]:
    """[(id plain-дубля на удаление, id остающейся Urine_*-строки), …]."""
    by_key: dict = {}
    for r in rows:
        if (r.get("specimen") or "") != "urine":
            continue
        if (r["test_name"] or "").startswith("Urine_"):
            by_key[(r["date"], r["test_name"])] = r
    out = []
    for r in rows:
        if (r.get("specimen") or "") != "urine" or (r["test_name"] or "").startswith("Urine_"):
            continue
        tgt = urine_target(r["test_name"])
        twin = by_key.get((r["date"], tgt)) if tgt else None
        if twin is not None and _same_measurement(r, twin):
            out.append((r["id"], twin["id"]))
    return out


def plan_renames(rows: list[dict]) -> list[tuple[int, str, str]]:
    """[(id, старое имя, новое Urine_*-имя), …] — plain-строки БЕЗ двойника."""
    dup_ids = {i for i, _ in plan_duplicates(rows)}
    out = []
    for r in rows:
        if (r.get("specimen") or "") != "urine" or (r["test_name"] or "").startswith("Urine_"):
            continue
        if r["id"] in dup_ids:
            continue
        tgt = urine_target(r["test_name"])
        if tgt:
            out.append((r["id"], r["test_name"], tgt))
    return out


def run(apply: bool = False) -> dict:
    health_db.init_db()
    with health_db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, date, test_name, value, unit, specimen, source FROM lab_results")]
        dups = plan_duplicates(rows)
        rens = plan_renames(rows)
        res = {"urine_rows": sum(1 for r in rows if (r.get("specimen") or "") == "urine"),
               "duplicates": len(dups), "renames": len(rens), "applied": False}
        print(json.dumps(res | {"dup_pairs": dups, "renames_list": rens},
                         ensure_ascii=False, indent=1))
        if not apply:
            return res
        stamp = get_now().strftime("%Y%m%d_%H%M%S")
        db_path = Path(str(health_db.DB_PATH))
        snap = db_path.with_name(f"health.pre_urine_convention_{stamp}.db")
        shutil.copy2(db_path, snap)
        log = {"snapshot": str(snap),
               "deleted": [dict(r) for r in rows if r["id"] in {i for i, _ in dups}],
               "renamed": rens}
        for rid, _keep in dups:
            conn.execute("DELETE FROM lab_results WHERE id=?", (rid,))
        for rid, _old, new in rens:
            conn.execute("UPDATE lab_results SET test_name=? WHERE id=?", (new, rid))
        conn.commit()
        logp = db_path.parent.parent / "backups" / f"urine_convention_{stamp}.json"
        logp.parent.mkdir(exist_ok=True)
        logp.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
        res.update({"applied": True, "snapshot": str(snap), "log": str(logp)})
        print(json.dumps({k: res[k] for k in ("applied", "snapshot", "log")},
                         ensure_ascii=False))
        return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    run(apply=ap.parse_args().apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
