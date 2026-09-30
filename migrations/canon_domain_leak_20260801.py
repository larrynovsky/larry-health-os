#!/usr/bin/env python3.11
"""Разбор утечки границы двух домов лабораторных данных (работа B, Ш3–Ш4).

Домен строки должен определять её единственный дом. Обещание только
в docstring писателя не выявляет уже записанные дубли между таблицами.
Датчик сверяет дома и даты по ключу измерения.
Правило починено коммитами Ш1–Ш2, но УЖЕ ЗАПИСАННЫЕ строки правило не двигает:
их разбирает этот модуль.

ЧТО ДЕЛАЕТ, тремя раздельными предикатами (каждый считается и печатается сам
по себе — сводное число скрыло бы, какой именно случай сработал):

  A. `plan_evict_from_canon` — строки канона, чей класс по вердикту человека
     живёт в спец-слое. Двойник в спец-слое ОБЯЗАН существовать: строка,
     у которой его нет, не удаляется, а попадает в отдельный список `orphan`.
     Удалять измерение, существующее в одном экземпляре, — это не чистка
     дубля, а тихая потеря (R1).

  B. `plan_evict_from_specialized` — зеркально: строки спец-слоя, чей дом
     канон И у которых двойник в каноне уже есть. С 2026-08-12 двойник
     узнаётся и по идентичности (`lab_canon.identity_name`: нормализация +
     суффикс размерности + гард голого `%`) — канон принимает строку под
     сведённым именем (`Immature_granulocytes_abs`), и буквальное сравнение
     оставляло дубль висеть вечно.

  B2. `plan_evict_cross_source_value_equal` — двойник под ДРУГИМ источником:
     та же дата, тот же материал, та же идентичность И ТОЧНО то же значение.
     Исходный документ и его перевод могут быть разобраны независимо:
     совпадение идентичности ещё не доказывает совпадение измерений.
     Сверять нужно и само значение. Равенство значения
     обязательно: идентичность без него — «канон видел такой аналит в тот
     день», а это НЕ доказательство того же измерения (R3). Расхождение
     значения при совпавшей идентичности печатается отдельно и НЕ трогается.

  C. `plan_move_to_canon` — ОТЧЁТ, не действие. Строки спец-слоя, чей дом
     канон, а двойника в каноне нет. Прямое добавление таких строк может
     обойти отказы штатного писателя: неизвестное имя или повествовательную
     страницу. Вставка была бы
     вторым путём записи мимо правил владельца таблицы. Теперь выход этого
     предиката — worklist сведения имён к канону, а строка попадает в канон
     единственной дверью, через `lab_promote`.

ЧЕГО НЕ ДЕЛАЕТ ОСОЗНАННО. Не решает, чей дом какой (это данные с оракулом-
человеком, таблица `lab_domain_verdicts`). Не трогает классы БЕЗ вердикта.
Не перепромоутит спец-слой. Не чинит расхождение дат — оно чинится
перепромоутом, а стережётся датчиком.

БЕЗОПАСНОСТЬ: dry-run по умолчанию, `--apply` явным флагом, снапшот БД перед
мутацией, одна транзакция.

  /opt/homebrew/bin/python3.11 -m migrations.canon_domain_leak_20260801
  /opt/homebrew/bin/python3.11 -m migrations.canon_domain_leak_20260801 --apply
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from _time_inject import get_now  # seam: время в проекте одно, и оно тестируемо

import health_db
import labs_db
import lab_canon


def _key(date, source, specimen, name):
    """Ключ измерения включает материал: совпавшие дата и аналит
    у разных материалов не означают одно измерение.
    Без материала сравнение значений даёт ложные расхождения."""
    return ((date or ""), (source or ""), (specimen or ""), (name or "").strip())


def _canon_index(rows):
    """Индекс канона: буквальное имя + идентичность (2026-08-12). Канон,
    принявший строку под сведённым именем с суффиксом размерности, обязан
    узнаваться тем же правилом, каким судят guard промоута и датчик."""
    idx = {}
    for r in rows:
        keys = {_key(r["date"], r["source"], r["specimen"], r["test_name"])}
        ident = lab_canon.identity_name(r["test_name"] or "", r.get("unit") or "")
        if ident is not None:
            keys.add(_key(r["date"], r["source"], r["specimen"], ident))
        for k in keys:
            idx.setdefault(k, []).append(r)
    return idx


def _spec_names(r):
    """Имя спец-строки в сырой и канонической формах.
    Канонизация слоя может быть неполной; ключ по одной форме
    пропустил бы совпадение, записанное в другой форме."""
    return [n for n in ((r["analyte_raw"] or "").strip(),
                        (r["analyte_canonical"] or "").strip()) if n]


def _spec_forms(r):
    """Формы имени + идентичность — тем же правилом, что guard промоута и
    датчик комнаты ожидания (`lab_canon.identity_name`, один дом, §17)."""
    names = _spec_names(r)
    idents = {lab_canon.identity_name(n, r.get("unit") or "") for n in names}
    return sorted({*names, *(idents - {None})})


def _load(conn):
    canon = [dict(r) for r in conn.execute(
        "SELECT id, date, source, specimen, test_name, value, unit, method "
        "FROM lab_results")]
    spec = [dict(r) for r in conn.execute(
        "SELECT id, date, source, specimen, panel_type, analyte_raw, "
        "analyte_canonical, value, unit, method FROM specialized_lab_results")]
    stg = [dict(r) for r in conn.execute(
        "SELECT date, source_file, raw_name, canonical_name, panel, specimen, "
        "specimen_source, value, unit, ref_low, ref_high, doc_flag, method, "
        "method_source, page_role FROM lab_results_staging")]
    return canon, spec, stg


def _class_by_key_from_staging(stg) -> dict:
    """Класс строки по ПРОВЕНАНСУ, а не по догадке об имени.

    У канона нет колонки `panel`, а класс без панели из имени не выводится:
    имя и единица сами по себе не гарантируют домен строки.
    Поэтому классификация опирается на исходную панель и материал,
    а не на эвристику по числовым границам.

    Поэтому класс берётся у РОДИТЕЛЯ строки в staging. Брать его у двойника
    в спец-слое было бы соблазнительно и неверно: тогда «класс известен»
    означало бы «двойник есть», и строка чужого дома БЕЗ двойника — то есть
    ровно тот случай, ради которого существует список сирот, — не смогла бы
    попасть в него по построению.
    """
    out = {}
    for r in stg:
        if r.get("page_role") == "derived_chart":
            continue
        cls = lab_canon.classify_row(r["raw_name"], r["panel"])
        hint, _m = lab_canon.class_hint(cls, r["panel"])
        read = (lab_canon.specimen_class(r["specimen"])
                if (r["specimen_source"] or "") in
                ("read_header", "read_footer", "continuation") else None)
        src = r["source_file"] or ""
        src = f"doc:{src}" if src and not src.startswith("doc:") else (src or "")
        for name in {(r["raw_name"] or "").strip(),
                     (r["canonical_name"] or "").strip()} - {""}:
            out[_key(r["date"], src, read or hint, name)] = cls
    return out


def plan_evict_from_canon(canon, spec, stg, verdicts) -> tuple[list, list]:
    """(на удаление из канона, сироты). Сирота = двойника в спец-слое нет.

    ОСОЗНАННО буквальные формы (`_spec_names`, без идентичности): расширение
    двигало бы строки из `orphan` в `evict` — то есть УДАЛЯЛО ИЗ КАНОНА больше,
    а радиус этого требует отдельной проверки. Направление ошибки
    безопасное: не-расширенный предикат не удаляет лишнего. Расширять — только
    после замера списка глазами (R1).
    """
    spec_keys = set()
    for r in spec:
        for n in _spec_names(r):
            spec_keys.add(_key(r["date"], r["source"], r["specimen"], n))
    cls_by_key = _class_by_key_from_staging(stg)
    evict, orphan = [], []
    for r in canon:
        k = _key(r["date"], r["source"], r["specimen"], r["test_name"])
        cls = cls_by_key.get(k)
        if cls is None or verdicts.get(cls) != "specialized":
            continue
        (evict if k in spec_keys else orphan).append(r)
    return evict, orphan


def plan_evict_from_specialized(canon, spec, verdicts) -> list:
    """Строки спец-слоя, чей дом канон И двойник в каноне уже есть.

    Формы — `_spec_forms` (буквальные + идентичность): предикат обязан судить
    тем же правилом, что guard `lab_specialized._in_canon`, иначе комната
    ожидания и её чистильщик разъезжаются молча (две копии правила, §17).
    Ключ несёт ИСТОЧНИК: это same-source случай; кросс-источник — B2.
    """
    idx = _canon_index(canon)
    out = []
    for r in spec:
        if verdicts.get(r["panel_type"]) != "canon":
            continue
        hits = [c for n in _spec_forms(r)
                for c in idx.get(_key(r["date"], r["source"], r["specimen"], n), [])]
        if not hits:
            continue
        # ГАРД ЛОЖНОГО ДРОПА: одинаковое имя при разных единицах
        # ещё не доказывает дублирование. Литеральное совпадение
        # имени слепо к ЕДИНИЦЕ; если идентичности обеих сторон определены и
        # РАСХОДЯТСЯ — это разные величины под одним именем, не дубль.
        sid = lab_canon.identity_name(r["analyte_raw"] or "", r.get("unit") or "")
        if sid is not None and not any(
                (lab_canon.identity_name(c["test_name"] or "", c.get("unit") or "")
                 or sid) == sid for c in hits):
            continue
        out.append(r)
    return out


def plan_evict_cross_source_value_equal(canon, spec, verdicts) -> tuple[list, list]:
    """(на выселение, расхождения). Двойник под ДРУГИМ источником: дата +
    материал + идентичность совпали И значение равно ТОЧНО.

    Документ и его перевод могут дать две записи одного измерения.
    При разных источниках совпадение идентичности само по себе
    недостаточно. Равенство значения обязательно: идентичность
    без него доказывает «канон видел такой аналит в тот день», а не «это то
    же измерение» — пересдача тем же днём в другой лаборатории законна (R3).

    Совпавшая идентичность с РАСХОДЯЩИМСЯ значением — находка, не действие:
    возвращается вторым списком, печатается, не удаляется.
    """
    idx = {}
    for r in canon:
        ident = lab_canon.identity_name(r["test_name"] or "", r.get("unit") or "")
        if ident is None:
            continue
        idx.setdefault(((r["date"] or ""), (r["specimen"] or ""), ident),
                       []).append(r)
    out, mismatched = [], []
    for r in spec:
        if verdicts.get(r["panel_type"]) != "canon" or r.get("value") is None:
            continue
        hits = []
        for n in _spec_names(r):
            ident = lab_canon.identity_name(n, r.get("unit") or "")
            if ident is None:
                continue
            hits += idx.get(((r["date"] or ""), (r["specimen"] or ""), ident), [])
        cross = [h for h in hits if (h["source"] or "") != (r["source"] or "")]
        if not cross:
            continue
        if any(h.get("value") is not None
               and abs(float(h["value"]) - float(r["value"])) < 1e-9
               for h in cross):
            out.append(r)
        else:
            mismatched.append(r)
    return out, mismatched


def plan_move_to_canon(canon, spec, stg, verdicts) -> tuple[list, list]:
    """(строки staging на вставку в канон, спец-строки без родителя в staging).

    Источник — staging, а не спец-слой: промежуточный слой источником истины
    быть не может по построению: он может сохранять старую дату после
    исправления исходной записи.
    """
    idx = _canon_index(canon)
    # staging индексируется тем же ключом, что канон: имя сырое, материал —
    # прочитанный из бланка, иначе назначенный класса.
    stg_idx = {}
    for r in stg:
        if r.get("page_role") == "derived_chart":
            continue
        cls = lab_canon.classify_row(r["raw_name"], r["panel"])
        hint, _m = lab_canon.class_hint(cls, r["panel"])
        read = (lab_canon.specimen_class(r["specimen"])
                if (r["specimen_source"] or "") in
                ("read_header", "read_footer", "continuation") else None)
        src = r["source_file"] or ""
        src = f"doc:{src}" if src and not src.startswith("doc:") else (src or "")
        stg_idx.setdefault(_key(r["date"], src, read or hint, r["raw_name"]),
                           []).append(dict(r, _src=src, _spec=read or hint, _cls=cls))
    insert, unsourced = [], []
    for r in spec:
        if verdicts.get(r["panel_type"]) != "canon":
            continue
        keys = [_key(r["date"], r["source"], r["specimen"], n) for n in _spec_forms(r)]
        if any(k in idx for k in keys):
            continue                      # уже в каноне — это случай B, не C
        parents = [p for k in keys for p in stg_idx.get(k, [])]
        if not parents:
            unsourced.append(r)
            continue
        insert.append(parents[0])
    return insert, unsourced


def run(apply: bool = False) -> dict:
    health_db.init_db()
    with health_db.get_conn() as conn:
        verdicts = labs_db.domain_verdicts(conn=conn)
        canon, spec, stg = _load(conn)
    evict_canon, orphan = plan_evict_from_canon(canon, spec, stg, verdicts)
    evict_spec = plan_evict_from_specialized(canon, spec, verdicts)
    _b_ids = {r["id"] for r in evict_spec}
    evict_cross, cross_mismatch = plan_evict_cross_source_value_equal(
        canon, spec, verdicts)
    evict_cross = [r for r in evict_cross if r["id"] not in _b_ids]
    insert, unsourced = plan_move_to_canon(canon, spec, stg, verdicts)
    res = {"canon": len(canon), "specialized": len(spec), "staging": len(stg),
           "A_evict_from_canon": len(evict_canon), "A_orphan": len(orphan),
           "B_evict_from_specialized": len(evict_spec),
           "B2_evict_cross_source_value_equal": len(evict_cross),
           # Идентичность совпала, значение НЕТ — находка для глаз, не действие.
           "B2_value_mismatch_untouched": len(cross_mismatch),
           # C — ОТЧЁТ, а не действие: worklist сведения имён к канону.
           "C_worklist_not_in_canon": len(insert), "C_unsourced": len(unsourced),
           "applied": False}
    for r in cross_mismatch:
        print("  B2 РАСХОЖДЕНИЕ (не трогаю): id=%s %s %r val=%r" %
              (r["id"], r["date"], r["analyte_raw"], r["value"]))
    if not apply:
        return res
    snap = Path(str(health_db.DB_PATH) + ".pre_domain_leak_%s" %
                get_now().strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(health_db.DB_PATH, snap)
    with health_db.get_conn() as conn:
        conn.execute("BEGIN")
        conn.executemany("DELETE FROM lab_results WHERE id=?",
                         [(r["id"],) for r in evict_canon])
        conn.executemany("DELETE FROM specialized_lab_results WHERE id=?",
                         [(r["id"],) for r in evict_spec + evict_cross])
        # ВСТАВКИ В КАНОН ЗДЕСЬ НЕТ И НЕ БУДЕТ (правка 2026-08-01, до применения).
        #
        # Прямая вставка обошла бы lab_promote._block_reason:
        # unmapped-nonanalyte и page-role:narrative остаются отказами
        # штатного писателя. Миграция не должна создавать второй путь
        # записи мимо правил владельца таблицы.
        #
        # `plan_move_to_canon` остаётся как ОТЧЁТ: его выход — worklist сведения
        # имён к канону, а не список на вставку. Строка попадает в канон
        # единственной дверью — через `lab_promote`.
        conn.commit()
    res.update({"applied": True, "snapshot": str(snap)})
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="выполнить (по умолчанию dry-run)")
    a = ap.parse_args()
    r = run(apply=a.apply)
    for k, v in r.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    assert _key("2021-06-14", "doc:x", "blood", " Hg ") == \
           ("2021-06-14", "doc:x", "blood", "Hg"), "ключ обязан нести материал и стричь имя"
    assert _key("d", "s", "blood", "Hg") != _key("d", "s", "urine", "Hg"), \
        "кровь и моча одного аналита — ДВА измерения, а не расхождение"
    main()
