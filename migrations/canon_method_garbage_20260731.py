#!/usr/bin/env python3.11
"""Убрать из канона «методы», которые методами не являются.

ЧТО ЧИНИМ. Ступень read_section может принять строку результата
или референсный диапазон за заголовок раздела. Тогда содержимое
результата ошибочно попадает в поле method.
lab_specimen.sections требует заголовка с прописной буквы;
миграция пересчитывает ранее записанные методы по исправленному правилу.

ПОЧЕМУ ОТДЕЛЬНАЯ МИГРАЦИЯ, А НЕ ПЕРЕПРОМОУТ. Перепромоут переписал бы весь
канон ради локальной правки — радиус несопоставим с задачей, и он же вернул бы вопрос
о материале и дате, уже решённый. Здесь предмет ровно один: колонка `method`.

ПРЕДИКАТ ПО ДАННЫМ, А НЕ ПО СПИСКУ. Значение считается мусорным, если (1) новое
правило заголовка его отвергает И (2) в staging оно порождалось ТОЛЬКО ступенью
`read_section`. Второе условие обязательно: настоящие методы «по Вестергрену» и
«подсчёт по Фонио» тоже начинаются со строчной, но приходят из ИМЕНИ строки, и
список-исключение из двух литералов был бы клиническим знанием в коде (§9).

ОБРАТИМОСТЬ. Значение не теряется: оно остаётся в `lab_results_staging.method`.
Плюс снапшот БД рядом перед запуском. Это §13 ступень 1 (авто-ремонт), не 3.

Запуск: dry-run по умолчанию; живой прогон — `--apply`.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import health_db
import lab_specimen

_READ_SECTION = "read_section"


def is_heading_shaped(value: str) -> bool:
    """Тот же предикат, что у `lab_specimen.sections`: заголовок с прописной.

    Публично и вынесено сюда НАМЕРЕННО одной строкой-делегатом: правило живёт у
    соседа, здесь только его применение к уже записанному значению. Скопировать
    условие означало бы завести второй дом — миграция чистила бы по одному
    правилу, а конвейер писал бы по другому.
    """
    return lab_specimen.heading_shaped(value or "")


def plan_updates(conn) -> tuple[list[str], dict]:
    """(мусорные значения method, диагностика) — ЧИСТЫЙ расчёт, без мутации.

    Публично, потому что предикат обязан считаться ДО мутации и сравниваться с
    состоянием ПОСЛЕ; приватная функция не дала бы контролю в это заглянуть.
    """
    src = defaultdict(set)
    for m, s in conn.execute(
            "SELECT method, method_source FROM lab_results_staging "
            "WHERE method IS NOT NULL"):
        src[m].add(s)
    in_canon = [r[0] for r in conn.execute(
        "SELECT DISTINCT method FROM lab_results WHERE method IS NOT NULL")]
    garbage, kept_lowercase, unknown_origin = [], [], []
    for m in in_canon:
        if is_heading_shaped(m):
            continue
        origins = src.get(m, set())
        if origins == {_READ_SECTION}:
            garbage.append(m)
        elif origins:
            kept_lowercase.append((m, sorted(origins)))
        else:
            unknown_origin.append(m)
    return sorted(garbage), {
        "методов в каноне": len(in_canon),
        "мусорных (только read_section)": len(garbage),
        "строчных, но из другой ступени — НЕ трогаем": kept_lowercase,
        "нет происхождения в staging — НЕ трогаем": unknown_origin,
    }


def collisions_after_null(conn, garbage: list[str]) -> list[tuple]:
    """Строки, которые СЛИПНУТСЯ по ключу канона, если обнулить метод.

    Ключ уникальности — (date, test_name, source, specimen, method). Обнуление
    метода схлопывает две строки в одну; без этой проверки миграция уронила бы
    UNIQUE и мы узнали бы о радиусе из traceback, а не заранее.
    """
    if not garbage:
        return []
    q = ",".join("?" * len(garbage))
    return list(conn.execute(
        f"""SELECT date, test_name, source, COALESCE(specimen,''), COUNT(*)
            FROM lab_results
            WHERE method IS NULL OR method IN ({q})
            GROUP BY 1,2,3,4 HAVING COUNT(*) > 1""", garbage))


def run(apply: bool = False) -> dict:
    health_db.init_db()
    with health_db.get_conn() as conn:
        garbage, diag = plan_updates(conn)
        before = conn.execute(
            "SELECT COUNT(*) FROM lab_results WHERE method IS NOT NULL").fetchone()[0]
        affected = 0
        if garbage:
            q = ",".join("?" * len(garbage))
            affected = conn.execute(
                f"SELECT COUNT(*) FROM lab_results WHERE method IN ({q})",
                garbage).fetchone()[0]
        clash = collisions_after_null(conn, garbage)

        print("== ПЛАН ==")
        for k, v in diag.items():
            print(f"  {k}: {v}")
        print(f"  строк канона с методом: {before}")
        print(f"  строк под обнуление: {affected}")
        print(f"  СЛИПАНИЙ по ключу после обнуления: {len(clash)}")
        for c in clash[:10]:
            print(f"    ⚠ {c}")
        for m in garbage:
            print(f"    мусор: {m[:70]!r}")

        if not apply:
            print("\n(dry-run; для записи — --apply)")
            return {"garbage": garbage, "affected": affected, "applied": False,
                    "collisions": len(clash)}
        if clash:
            print("\n⛔ ОТКАЗ: обнуление схлопнуло бы строки по ключу канона. "
                  "Радиус больше предмета — решает человек.")
            return {"garbage": garbage, "affected": affected, "applied": False,
                    "collisions": len(clash)}
        if not garbage:
            print("\nнечего делать")
            return {"garbage": [], "affected": 0, "applied": False, "collisions": 0}

        snap = Path(health_db.DB_PATH).parent.parent / "backups" / (
            # штамп имени файла снапшота в одноразовом CLI: время здесь не
            # участвует в суждении, подменять его нечем и незачем.
            f"health_preop_method_garbage_{datetime.now():%Y%m%d_%H%M}.db")  # time-inject: ok
        snap.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(health_db.DB_PATH, snap)
        print(f"\nснапшот: {snap.name}")

        q = ",".join("?" * len(garbage))
        conn.execute("BEGIN")
        cur = conn.execute(
            f"UPDATE lab_results SET method=NULL WHERE method IN ({q})", garbage)
        changed = cur.rowcount
        after = conn.execute(
            "SELECT COUNT(*) FROM lab_results WHERE method IS NOT NULL").fetchone()[0]
        rows_total = conn.execute("SELECT COUNT(*) FROM lab_results").fetchone()[0]
        left = plan_updates(conn)[0]
        ok = (changed == affected and after == before - affected and not left)
        if not ok:
            conn.execute("ROLLBACK")
            print(f"⛔ ОТКАТ: changed={changed} (ждали {affected}), "
                  f"after={after} (ждали {before - affected}), остаток={left}")
            return {"applied": False, "rolled_back": True}
        conn.execute("COMMIT")
        print(f"✅ обнулено {changed}; методов осталось {after}; "
              f"строк канона {rows_total} (не менялось)")
        return {"garbage": garbage, "affected": affected, "applied": True,
                "changed": changed, "collisions": 0}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    sys.exit(0 if run(apply=ap.parse_args().apply) is not None else 1)
