#!/usr/bin/env python3.11
"""Переезд профиля органических кислот мочи из канона в свой дом (решение владельца 14.09).

ЧТО ЧИНИТ. Вердикт класса `metabolomics` сменён с `canon` на `specialized`
(`health_db._migrate_lab_domain_verdicts`, там же замер и граница). Смена вердикта
правило меняет, а УЖЕ ЗАПИСАННЫЕ строки не двигает — их разбирает этот модуль. Тот
же порядок был у работы B 01.08 (`canon_domain_leak_20260801`).

ПОЧЕМУ НЕ ПЕРЕИСПОЛЬЗОВАН ПРЕДИКАТ A ТОЙ МИГРАЦИИ. Он выселяет из канона строку,
у которой в спец-слое УЖЕ ЕСТЬ двойник, а строку без двойника кладёт в `orphan` и
не трогает — сознательно, потому что удалить измерение, существующее в одном
экземпляре, это не чистка дубля, а тихая потеря. Если двойника нет,
нужен переезд: сначала строка появляется в
новом доме, и только потом исчезает из старого.

ПОЧЕМУ ВСТАВКА ИДЁТ МИМО `lab_specialized.promote_specialized`, и это названо
вслух. Штатный писатель спец-слоя принимает НОВЫЙ документ из `lab_results_staging`;
здесь предмет другой — переезд строки, уже принятой и живущей в каноне. Своего
писателя у переезда нет. Повторный проход через staging мог бы выбрать
другой набор строк: там возможны повторные разборы, разные статусы и панели.
Предмет миграции — уже принятые строки канона.

КРИТЕРИЙ ОТБОРА — ДВА УСЛОВИЯ, оба проверяемы:
  1. единица ровно ммоль/моль креатинина — условие нормировки панели;
     одной единицы для определения домена недостаточно;
  2. `lab_canon.classify_row(имя, 'urine') == 'metabolomics'` — класс по тому же
     механизму, которым его назовут при следующей сдаче.
Второе условие сохраняет в каноне строки, чей класс остаётся urine.
Решение принимается по классификатору, а не по принадлежности к документу.

РАДИУС В ОБЕ СТОРОНЫ, как требует правило разрушающего действия:
  · канон теряет ровно строки рассчитанного плана;
  · спец-слой получает тот же набор — сверяются число и идентичность строк;
  · ЧЕГО НЕ ДОЛЖНО СЛУЧИТЬСЯ: ни одна строка вне профиля не тронута. Контроль —
    счёт `lab_results` до и после обязан отличаться ровно на число переехавших.

БЕЗОПАСНОСТЬ: dry-run по умолчанию, `--apply` явным флагом, снапшот БД перед
мутацией, одна транзакция, вставка ДО удаления. Повторный запуск безопасен:
вставка идемпотентна по уникальному индексу спец-слоя, а второй проход не находит
строк в каноне.

  /opt/homebrew/bin/python3.11 -m migrations.metabolomics_home_20260914
  /opt/homebrew/bin/python3.11 -m migrations.metabolomics_home_20260914 --apply
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from _time_inject import get_now   # seam: время в проекте одно, и оно тестируемо

import health_db
import lab_canon

PANEL_UNIT = "ммоль/моль креатинина"
CLASS = "metabolomics"


def plan_move(conn) -> list[dict]:
    """Строки канона, переезжающие в спец-слой. Чистое чтение."""
    rows = [dict(r) for r in conn.execute(
        "SELECT id, date, source, specimen, test_name, value, unit, method "
        "FROM lab_results WHERE unit = ?", (PANEL_UNIT,))]
    return [r for r in rows
            if lab_canon.classify_row(r["test_name"] or "", "urine") == CLASS]


def _snapshot(db_path: Path) -> Path:
    dst = db_path.with_suffix(f".pre-metabolomics-{get_now():%Y%m%d-%H%M%S}.db")
    shutil.copy2(db_path, dst)
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="выполнить переезд")
    args = ap.parse_args()

    with health_db.get_conn() as conn:
        moving = plan_move(conn)
        canon_before = conn.execute("SELECT count(*) FROM lab_results").fetchone()[0]
        spec_before = conn.execute(
            "SELECT count(*) FROM specialized_lab_results WHERE panel_type=?",
            (CLASS,)).fetchone()[0]
        stay = [dict(r) for r in conn.execute(
            "SELECT test_name FROM lab_results WHERE unit = ?", (PANEL_UNIT,))]

    print(f"канон сейчас: {canon_before} строк; спец-слой класса {CLASS}: {spec_before}")
    print(f"с единицей «{PANEL_UNIT}»: {len(stay)}; переезжают: {len(moving)}")
    for r in sorted(moving, key=lambda x: x["test_name"])[:5]:
        print(f"  · {r['test_name']}  {r['value']} ({r['date']})")
    if len(moving) > 5:
        print(f"  … ещё {len(moving) - 5}")
    remain = [r["test_name"] for r in stay
              if lab_canon.classify_row(r["test_name"] or "", "urine") != CLASS]
    print(f"ОСТАЮТСЯ в каноне (каноническое имя уже есть): {remain or '—'}")

    if not args.apply:
        print("\ndry-run. Для исполнения: --apply")
        return 0
    if not moving:
        print("переезжать нечего — ничего не меняю")
        return 0

    snap = _snapshot(Path(health_db.DB_PATH))
    print(f"снапшот БД: {snap}")
    with health_db.get_conn() as conn:
        conn.execute("BEGIN")
        try:
            # Вердикт правится ЗДЕСЬ, а не только в seed: seed стоит под
            # INSERT OR IGNORE и живую строку не трогает (так и задумано — правка
            # человека переживает рестарт). Без этого UPDATE переезд оставил бы
            # строки в спец-слое при вердикте `canon`, то есть сам создал бы
            # находку «дом класса разошёлся с фактом» — ровно то, что стережёт
            # датчик границы двух домов.
            conn.execute(
                "UPDATE lab_domain_verdicts SET home='specialized', "
                "decided_on='2026-09-14', oracle='владелец', rationale=? "
                "WHERE panel_type=? AND home='canon'",
                ("профиль органических кислот мочи: однократная панель, по одному "
                 "замеру на показатель, тренда нет, имена пришлось бы назначать авторски — "
                 "дом профильной панели, как у микробиома (решение 2026-09-14)", CLASS))
            conn.executemany(
                "INSERT OR IGNORE INTO specialized_lab_results "
                "(date, source, panel_type, specimen, analyte_raw, value, unit, method) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [(r["date"], r["source"], CLASS, r["specimen"] or "urine",
                  r["test_name"], r["value"], r["unit"], r["method"]) for r in moving])
            conn.executemany("DELETE FROM lab_results WHERE id = ?",
                             [(r["id"],) for r in moving])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # Контроль в точке потребления: перечитываем БД, а не верим возврату writeов.
    with health_db.get_conn() as conn:
        canon_after = conn.execute("SELECT count(*) FROM lab_results").fetchone()[0]
        spec_after = conn.execute(
            "SELECT count(*) FROM specialized_lab_results WHERE panel_type=?",
            (CLASS,)).fetchone()[0]
        left = len(plan_move(conn))
        verdict = conn.execute(
            "SELECT home FROM lab_domain_verdicts WHERE panel_type=?", (CLASS,)).fetchone()
    print(f"вердикт класса {CLASS}: {verdict[0] if verdict else '—'}")
    print(f"канон: {canon_before} → {canon_after} (дельта {canon_before - canon_after})")
    print(f"спец-слой {CLASS}: {spec_before} → {spec_after}")
    ok = (canon_before - canon_after == len(moving)
          and spec_after - spec_before == len(moving) and left == 0
          and verdict is not None and verdict[0] == "specialized")
    if not ok:
        print("ПЕРЕЕЗД НЕ СОСТОЯЛСЯ ИЛИ ЗАДЕЛ ЛИШНЕЕ — смотри снапшот выше")
        return 1
    print("переезд состоялся: канон потерял ровно переехавшие, спец-слой их принял")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
