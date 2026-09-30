"""Дата строки = дата ВЗЯТИЯ своей заявки, а не дата, когда прибор досчитал.

Нить loinc-name-home, шаг 7. Идёт последним: сначала различители измерения
должны попасть в ключ. Иначе сведение дат может молча слить разные строки.

Дата забора и дата выполнения исследования обозначают разные события.
Если в staging попала дата выполнения, тренд отражает расписание
обработки документа. Для продольного сравнения нужна дата забора,
прочитанная из соответствующей заявки.

ЛЕСТНИЦА (в `lab_specimen.page_facts`, тот же дом, что материал и метод):
  read_header → своя строка «Взятие биоматериала»;
  same_order  → страница той же ЗАЯВКИ (забор один, дата у заявки одна);
  unknown     → в бланке даты взятия нет. Дата строки не трогается.

ПОЧЕМУ наследование по номеру заявки, а не по слову «продолжение», как у
материала: одна заявка — один забор и одна дата, но РАЗНЫЕ пробирки, поэтому
материал так наследовать нельзя, а дату можно.

ПРОТОКОЛ: снапшот → транзакция → проверки ВНУТРИ транзакции → commit. Предикат
считается ДО мутации.

Запуск на Studio:
    cd ~/health_scripts && /opt/homebrew/bin/python3.11 migrations/staging_date_taken_20260731.py
    … то же с `--execute` — записывает.
"""
from __future__ import annotations

import collections
import shutil

import health_db as _hdb
import lab_backfill as _bf
import lab_specimen as _ls

UNKNOWN = _ls.UNKNOWN
_READ_STEPS = ("read_header", "same_order")


def plan_updates(rows, facts_by_doc: dict) -> tuple[list, dict]:
    """(строки staging, {документ: {страница: факты}}) → (правки, счёт).

    Правка = (новая дата, ступень, id) и ТОЛЬКО там, где дата реально меняется:
    список правок обязан быть и предикатом «сколько строк поедет», а не отчётом
    обо всех строках подряд.
    """
    updates, tally = [], collections.Counter()
    for r in rows:
        f = (facts_by_doc.get(r["source_file"]) or {}).get(r["page"]) or {}
        taken = f.get("taken")
        src = f.get("taken_source") or UNKNOWN
        if src not in _READ_STEPS or not taken:
            tally["не тронуто (даты нет в бланке)"] += 1
            continue
        if taken == r["date"]:
            tally["совпадает"] += 1
            continue
        tally[f"{r['date']} → {taken}"] += 1
        updates.append((taken, src, r["id"]))
    return updates, dict(tally)


def _facts_by_doc(docs) -> dict:
    out = {}
    for sf in docs:
        p = _bf.resolve_document(sf)
        if p is None or p.suffix.lower() != ".pdf":
            continue
        try:
            out[sf] = _ls.page_facts(p)
        except Exception as e:  # silent-ok: документ нечитаем → строки не трогаем
            print(f"  ! {sf}: {e}")
    return out


def run(execute: bool = False) -> dict:
    """Публичный вход. dry-run по умолчанию."""
    _hdb.init_db()
    with _hdb.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, source_file, page, date FROM lab_results_staging").fetchall()]
        before = dict(conn.execute(
            "SELECT date, COUNT(*) FROM lab_results_staging GROUP BY 1").fetchall())

    updates, tally = plan_updates(rows, _facts_by_doc({r["source_file"] for r in rows}))
    res = {"строк": len(rows), "правок": len(updates), "разбивка": tally,
           "было дат": len(before), "executed": False}
    if not execute:
        return res

    snap = _hdb.DB_PATH.with_suffix(f".db.datetaken_{_now_tag()}.bak")
    shutil.copy2(_hdb.DB_PATH, snap)
    res["снапшот"] = str(snap)

    with _hdb.get_conn() as conn:
        conn.execute("BEGIN")
        conn.executemany(
            "UPDATE lab_results_staging SET date=?, date_source=? WHERE id=?", updates)
        moved = conn.execute(
            "SELECT COUNT(*) FROM lab_results_staging WHERE date_source IN "
            "('read_header','same_order')").fetchone()[0]
        lost = conn.execute("SELECT COUNT(*) FROM lab_results_staging").fetchone()[0]
        # ПРОВЕРКИ ВНУТРИ ТРАНЗАКЦИИ: строк не убыло и правок ровно столько,
        # сколько посчитал предикат ДО мутации.
        if lost != len(rows) or moved < len(updates):
            conn.execute("ROLLBACK")
            raise AssertionError(
                f"откат: строк {lost} против {len(rows)}; со ступенью {moved} "
                f"при {len(updates)} правках")
        conn.execute("COMMIT")
    res.update({"executed": True, "со ступенью": moved})
    return res


def _now_tag() -> str:
    from _time_inject import get_now
    return get_now().strftime("%Y%m%d_%H%M%S")


if __name__ == "__main__":
    import sys
    out = run(execute="--execute" in sys.argv)
    for k, v in out.items():
        print(f"{k}: {v}")
