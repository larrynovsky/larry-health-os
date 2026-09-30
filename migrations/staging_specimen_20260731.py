"""Бэкфилл материала пробы в lab_results_staging по САМОМУ бланку; 2026-07-31.

Нить loinc-name-home, шаг 1. Материал нужно хранить вместе с происхождением.
Если многостраничный документ содержит разные заявки, наследование шапки
с соседней страницы приписывает строкам чужой материал.
Подвал своей страницы может дать более точное свидетельство.

ЧТО ДЕЛАЕТ: для каждой строки staging, чей документ резолвится в PDF, проставляет
`specimen` (сырая строка бланка), `specimen_source` (ступень лестницы) и
`page_role`. Значения читает `lab_specimen.page_facts` — единственный дом правила.
Строки, чей документ не найден или не PDF, получают `specimen_source='unknown'`:
неизвестность записывается явно, а не остаётся NULL, неотличимым от «не дошли руки».

ЧЕГО НЕ ДЕЛАЕТ ОСОЗНАННО: не трогает `lab_results`, `specialized_lab_results` и
ключи уникальности. Перепромоут — шаг 2 нити, и порядок здесь не косметика:
Перепромоут до включения материала в ключ может молча слить разные измерения.

ПРОТОКОЛ (тот же, что у двух миграций 29.07): снапшот → транзакция → проверки
ВНУТРИ транзакции → commit. Предикат считается ДО мутации, а не после: 30.07
предикат, написанный после, оказался отчётом о вскрытии.

ИМЯ ФАЙЛА без ведущей даты СОЗНАТЕЛЬНО: соседние миграции названы `2026_05_18_*`,
и такое имя не является идентификатором Python — `import migrations.2026_…` это
синтаксическая ошибка. Гейт §15 требует теста, который ИМПОРТИРУЕТ модуль и зовёт
его публичное имя, то есть прежняя конвенция имён структурно несовместима с K2.
Дата уехала в хвост.

Запуск на Studio:
    cd ~/health_scripts && /opt/homebrew/bin/python3.11 migrations/staging_specimen_20260731.py
    … то же с `--execute` — записывает.
"""
from __future__ import annotations

import collections
import shutil

import health_db as _hdb
import lab_backfill as _bf
import lab_specimen as _ls

UNKNOWN = _ls.UNKNOWN


def plan_updates(rows, facts_by_doc: dict) -> tuple[list, dict]:
    """Чистая часть: (строки staging, {документ: {страница: факты}}) → (правки, счёт).

    Отдельная функция ровно для того, чтобы предикат можно было посчитать ДО
    мутации и сравнить с результатом ПОСЛЕ неё, не открывая PDF второй раз.
    `rows` — словари с id/source_file/page. Правка = (specimen, source, role, id).
    """
    updates, tally = [], collections.Counter()
    for r in rows:
        facts = facts_by_doc.get(r["source_file"]) or {}
        f = facts.get(r["page"]) or {}
        specimen = f.get("specimen")
        source = f.get("specimen_source") or UNKNOWN
        role = f.get("role") or "data"
        # сцепка, которую стережёт check_staging_specimen_provenance: материал
        # без ступени чтения не записываем вовсе — иначе он неотличим от назначенного
        if source not in ("read_header", "read_footer", "continuation"):
            specimen, source = None, UNKNOWN
        tally[source] += 1
        tally["роль:" + role] += 1
        updates.append((specimen, source, role, r["id"]))
    return updates, dict(tally)


def _facts_by_doc(docs) -> dict:
    out = {}
    for sf in docs:
        p = _bf.resolve_document(sf)
        if p is None or p.suffix.lower() != ".pdf":
            continue
        try:
            out[sf] = _ls.page_facts(p)
        except Exception as e:  # silent-ok: документ нечитаем → строки уйдут в unknown
            print(f"  ! {sf}: {e}")
    return out


def run(execute: bool = False) -> dict:
    """Публичный вход. dry-run по умолчанию: считает и печатает, не пишет."""
    _hdb.init_db()
    with _hdb.get_conn() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(lab_results_staging)")}
        if "specimen_source" not in cols:
            raise RuntimeError("нет колонки specimen_source — сначала миграция health_db")
        rows = [dict(r) for r in conn.execute(
            "SELECT id, source_file, page FROM lab_results_staging").fetchall()]
        before = dict(conn.execute(
            "SELECT COALESCE(specimen_source,'NULL'), COUNT(*) FROM lab_results_staging "
            "GROUP BY 1").fetchall())

    updates, tally = plan_updates(rows, _facts_by_doc({r["source_file"] for r in rows}))
    res = {"строк": len(rows), "было": before, "станет": tally, "executed": False}
    if not execute:
        return res

    snap = _hdb.DB_PATH.with_suffix(f".db.specimen_{_now_tag()}.bak")
    shutil.copy2(_hdb.DB_PATH, snap)
    res["снапшот"] = str(snap)

    with _hdb.get_conn() as conn:
        conn.execute("BEGIN")
        conn.executemany(
            "UPDATE lab_results_staging SET specimen=?, specimen_source=?, page_role=? "
            "WHERE id=?", updates)
        # ПРОВЕРКИ ВНУТРИ ТРАНЗАКЦИИ: разошлось — откат, а не «посмотрим потом»
        got = dict(conn.execute(
            "SELECT specimen_source, COUNT(*) FROM lab_results_staging GROUP BY 1").fetchall())
        expected = {k: v for k, v in tally.items() if not k.startswith("роль:")}
        orphan = conn.execute(
            "SELECT COUNT(*) FROM lab_results_staging WHERE specimen IS NOT NULL "
            "AND TRIM(specimen)<>'' AND specimen_source NOT IN "
            "('read_header','read_footer','continuation')").fetchone()[0]
        lost = conn.execute("SELECT COUNT(*) FROM lab_results_staging").fetchone()[0]
        if got != expected or orphan or lost != len(rows):
            conn.execute("ROLLBACK")
            raise AssertionError(
                f"откат: ожидали {expected}, получили {got}; материал без ступени "
                f"{orphan}; строк {lost} против {len(rows)}")
        conn.execute("COMMIT")
    res.update({"executed": True, "стало": got})
    return res


def _now_tag() -> str:
    from _time_inject import get_now
    return get_now().strftime("%Y%m%d_%H%M%S")


if __name__ == "__main__":
    import sys
    out = run(execute="--execute" in sys.argv)
    for k, v in out.items():
        print(f"{k}: {v}")
