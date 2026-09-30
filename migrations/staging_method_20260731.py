"""Метод исследования в staging: читается из бланка, а не выводится вниз по течению.

Нить `loinc-name-home`, шаг 5. Решение владельца 31.07 — вариант «строить носитель»,
а не «оставить конфликты человеку навсегда».

ЗАЧЕМ. Один аналит может быть измерен разными методами при одинаковых
дате и материале. Эти результаты и их референсы нельзя сливать.
Колонка panel несёт грубый класс распознавателя, а не заголовок
раздела документа. Метод нужно сохранить отдельно до промоута.

ЛЕСТНИЦА (в `lab_specimen.method_facts`, единственный дом правила):
  read_name → read_section → read_study → unknown.
Имя имеет приоритет над секцией: соседние строки одного раздела могут
называть разные методы. Секция имеет приоритет над названием исследования:
одна страница может объединять несколько методов.

ЧЕГО НЕ ДЕЛАЕТ ОСОЗНАННО: не трогает `lab_results`, не меняет ключ уникальности и
не запускает перепромоут. Ключ — следующий шаг, и порядок здесь не косметика:
перепромоут до включения метода в ключ может молча слить разные измерения.

ПРОТОКОЛ: снапшот → транзакция → проверки ВНУТРИ транзакции → commit. Предикат
считается ДО мутации; предикат, написанный после, — отчёт о вскрытии.

ИМЯ ФАЙЛА без ведущей даты — по той же причине, что у соседа
`staging_specimen_20260731`: `2026_05_18_*` не идентификатор Python, и гейт §15,
требующий теста с импортом модуля, такой файл выполнить не может в принципе.

Запуск на Studio:
    cd ~/health_scripts && /opt/homebrew/bin/python3.11 migrations/staging_method_20260731.py
    … то же с `--execute` — записывает.
"""
from __future__ import annotations

import collections
import shutil

import health_db as _hdb
import lab_backfill as _bf
import lab_specimen as _ls

UNKNOWN = _ls.UNKNOWN
_READ_STEPS = ("read_name", "read_section", "read_study")


def plan_updates(rows, facts_by_doc: dict) -> tuple[list, dict]:
    """Чистая часть: (строки staging, {документ: {страница: {id: факты}}}) → (правки, счёт).

    Отдельная функция ровно затем, чтобы предикат считался ДО мутации и сравнивался
    с результатом ПОСЛЕ, не открывая PDF второй раз. Правка = (method, source, id).

    Сцепка, которую потом стережёт датчик: метод БЕЗ ступени чтения не записывается
    вовсе. Иначе прочитанный с бланка метод неотличим от назначенного, а вся нить
    началась с того, что материал был назначен, а не измерен.
    """
    updates, tally = [], collections.Counter()
    for r in rows:
        f = ((facts_by_doc.get(r["source_file"]) or {}).get(r["page"]) or {}).get(r["id"]) or {}
        method = f.get("method")
        source = f.get("method_source") or UNKNOWN
        if source not in _READ_STEPS or not (method or "").strip():
            method, source = None, UNKNOWN
        tally[source] += 1
        updates.append((method, source, r["id"]))
    return updates, dict(tally)


def _facts_by_doc(rows) -> dict:
    """{документ: {страница: {id строки: факты}}}. PDF открывается ОДИН раз на документ."""
    by_doc: dict = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        by_doc[r["source_file"]][r["page"]].append(r)
    out: dict = {}
    for sf, pages in by_doc.items():
        p = _bf.resolve_document(sf)
        if p is None or p.suffix.lower() != ".pdf":
            continue
        try:
            texts = _ls.page_texts(p)
            facts = _ls.page_facts(p, texts=texts)
        except Exception as e:  # silent-ok: документ нечитаем → строки уйдут в unknown
            print(f"  ! {sf}: {e}")
            continue
        out[sf] = {}
        for page, prows in pages.items():
            if page is None or not (1 <= page <= len(texts)):
                continue
            out[sf][page] = _ls.method_facts(
                texts[page - 1], prows, (facts.get(page) or {}).get("study"))
    return out


def run(execute: bool = False) -> dict:
    """Публичный вход. dry-run по умолчанию: считает и печатает, не пишет."""
    _hdb.init_db()
    with _hdb.get_conn() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(lab_results_staging)")}
        if "method_source" not in cols:
            raise RuntimeError("нет колонки method_source — сначала миграция health_db")
        rows = [dict(r) for r in conn.execute(
            "SELECT id, source_file, page, raw_name, unit, value_text FROM lab_results_staging").fetchall()]
        before = dict(conn.execute(
            "SELECT COALESCE(method_source,'NULL'), COUNT(*) FROM lab_results_staging "
            "GROUP BY 1").fetchall())

    updates, tally = plan_updates(rows, _facts_by_doc(rows))
    res = {"строк": len(rows), "было": before, "станет": tally, "executed": False}
    if not execute:
        return res

    snap = _hdb.DB_PATH.with_suffix(f".db.method_{_now_tag()}.bak")
    shutil.copy2(_hdb.DB_PATH, snap)
    res["снапшот"] = str(snap)

    with _hdb.get_conn() as conn:
        conn.execute("BEGIN")
        conn.executemany(
            "UPDATE lab_results_staging SET method=?, method_source=? WHERE id=?", updates)
        # ПРОВЕРКИ ВНУТРИ ТРАНЗАКЦИИ: разошлось — откат, а не «посмотрим потом»
        got = dict(conn.execute(
            "SELECT method_source, COUNT(*) FROM lab_results_staging GROUP BY 1").fetchall())
        orphan = conn.execute(
            "SELECT COUNT(*) FROM lab_results_staging WHERE method IS NOT NULL "
            "AND TRIM(method)<>'' AND method_source NOT IN "
            "('read_name','read_section','read_study')").fetchone()[0]
        lost = conn.execute("SELECT COUNT(*) FROM lab_results_staging").fetchone()[0]
        if got != tally or orphan or lost != len(rows):
            conn.execute("ROLLBACK")
            raise AssertionError(
                f"откат: ожидали {tally}, получили {got}; метод без ступени {orphan}; "
                f"строк {lost} против {len(rows)}")
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
