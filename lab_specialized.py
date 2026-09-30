"""Промоут спец-панелей staging → specialized_lab_results (BL-LAB-CANON-2, 2026-07-04).

Классы строк, чей дом по вердикту человека — НЕ канон, едут в одну long-format
таблицу с дискриминатором panel_type. Единственный писатель этой таблицы =
`promote_specialized` (Primary-Based Protocol, как lab_promote для lab_results).

Класс строки определяет общий предок `lab_canon.classify_row`,
дом класса — `labs_db.domain_verdicts`, версионируемое решение человека.
Дублирование правила у писателей создало бы риск одновременного приёма
одной строки в оба дома.

Класс БЕЗ вердикта не пропадает и не едет в канон: он остаётся здесь и виден
счётчиком плана — умолчание безопасное, но объявленное вслух (§13, R5).

Публичный вход — `promote_specialized(run_id, execute)`. dry-run по умолчанию.
"""
from __future__ import annotations

import health_db as _hdb
import labs_db as _ldb
import lab_canon as _lc


def _canon_keys(conn) -> set:
    """Ключи измерений, УЖЕ лежащих в каноне. Ключ несёт материал.

    Читается один раз на прогон: это факт о соседней таблице, а не правило
    её приёма. Правил канона этот модуль не знает и знать не должен.

    Каждая строка канона даёт буквальное имя и идентичность
    (`lab_canon.identity_name`: нормализация, размерность и гард голого `%`).
    Только буквальное имя не узнает строку, принятую под сведённым вариантом.

    Нет таблицы — пустое множество, и это БЕЗОПАСНАЯ сторона ошибки: ни одна
    строка не будет признана «принятой каноном», то есть ничего не уйдёт
    отсюда. Спрашивается вопросом, а не `except`: тишина по неизвестной
    причине здесь означала бы тихую потерю.
    """
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='lab_results'").fetchone():
        return set()
    out = set()
    for r in conn.execute(
            "SELECT date, source, specimen, test_name, unit FROM lab_results"):
        base = ((r[0] or ""), (r[1] or ""), (r[2] or ""))
        out.add(base + ((r[3] or "").strip(),))
        ident = _lc.identity_name(r[3] or "", r[4] or "")
        if ident is not None:
            out.add(base + (ident,))
    return out


def _in_canon(canon_keys: set, r: dict) -> bool:
    """Есть ли эта staging-строка в каноне — под сырым, каноническим или сведённым именем.

    Формы имени берутся у общего предка `_lc._boundary_names`.
    Пустой canonical_name не должен скрывать совпадение сырого имени
    с нормализованным test_name; локальные копии правила разошлись бы.

    Две копии одного правила — та самая болезнь, которую лечит вся эта работа,
    поэтому формы имени теперь ровно одни: если завтра к ключу добавят четвёртую,
    guard и датчик получат её вместе или не получат вовсе.

    Поверх форм — идентичность (`_lc.identity_name`, 2026-08-12): канон
    принимает строку под сведённым именем с суффиксом размерности, и guard
    обязан узнавать её тем же правилом, каким судит датчик комнаты ожидания.
    Дата и ИСТОЧНИК остаются в ключе НАМЕРЕННО: вопрос guard'а — «ЭТО измерение
    принято каноном», не «канон видел такой аналит». Идентичность без даты
    отпустила бы строку по чужому измерению той же величины (R3 плана 12.08).
    """
    src = r.get("source_file") or ""
    src = f"doc:{src}" if src and not src.startswith("doc:") else src
    read = (_lc.specimen_class(r.get("specimen"))
            if (r.get("specimen_source") or "") in
            ("read_header", "read_footer", "continuation") else None)
    cls = _lc.classify_row(r.get("raw_name"), r.get("panel"))
    hint, _m = _lc.class_hint(cls, r.get("panel"))
    # Нет ни прочитанного материала, ни подсказки класса — материал тот, что поставит сам
    # писатель канона (lab_promote.specimen_of: панель → материал). До 27.09 здесь был "",
    # а в каноне материал пустым не бывает (DEFAULT 'blood'): guard не узнавал строку,
    # уже принятую каноном, и держал её дублем (BL-LAB-SPECIMEN-FIXTURE-1; тесты были
    # зелёными только на своей схеме без DEFAULT).
    if not (read or hint):
        import lab_promote as _lp
        spec = _lp.specimen_of(r)
    else:
        spec = read or hint
    names = _lc._boundary_names({"analyte_raw": r.get("raw_name"),
                                 "analyte_canonical": r.get("canonical_name")})
    idents = {_lc.identity_name(n, r.get("unit") or "") for n in names}
    for name in names | (idents - {None}):
        if (r.get("date") or "", src, spec, name) in canon_keys:
            return True
    return False


def promote_specialized(run_id: str, execute: bool = False) -> dict:
    """Промоут спец-панелей из staging в specialized_lab_results.

    Строки, чей дом по вердикту — канон, пропускаются (их владелец — lab_promote).
    Числовое value → value, нечисловое → value_text. Идемпотентно по
    UNIQUE(date, analyte_raw, source, panel_type). dry-run по умолчанию.
    """
    _hdb.init_db()
    import collections
    with _hdb.get_conn() as conn:
        # ОТКЛОНЁННОЕ РЕВЬЮ НЕ ЕДЕТ НИКУДА (2026-08-01). До этой правки фильтр
        # `rejected` стоял только у промоута крови, а здесь строки брались все
        # подряд — то есть решение человека исполнял ОДИН писатель из двух.
        # Третий случай той же формы за нить (первым был page_role, вторым —
        # граница доменов), и он ничего не стоил только по везению: замер
        # 2026-08-01 показал 0 строк спец-слоя, чей единственный родитель
        # отклонён. Латентный, а не действующий — но чинится тем же способом.
        # COALESCE, а не `!=`: в SQLite NULL != 'rejected' даёт NULL, и строки
        # без статуса (разбор до появления ревью) выпали бы молча.
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM lab_results_staging WHERE run_id=? "
            "AND COALESCE(review_status,'pending') != 'rejected'",
            (run_id,)).fetchall()]
        verdicts = _ldb.domain_verdicts(conn=conn)
        canon_keys = _canon_keys(conn)

    plan = collections.Counter()
    no_verdict = collections.Counter()
    to_insert = []
    # Принятые этим слоем строки получают терминальный статус specialized.
    # Строки, которые лишь ожидают приёма каноном, остаются в ожидании.
    accepted_here: list = []
    for r in rows:
        # Производный график не является результатом измерения.
        # Старые строки с неизвестной ролью страницы разбирает миграция.
        if r.get("page_role") == "derived_chart":
            plan["страница-график (пропуск)"] += 1
            continue
        pt = _lc.classify_row(r.get("raw_name"), r.get("panel"))
        home = verdicts.get(pt)
        if home == "canon":
            # СТРОКА НЕ ПОКИДАЕТ ДОМ, ПОКА НОВЫЙ ДОМ ЕЁ НЕ ПРИНЯЛ (2026-08-01).
            #
            # Вердикт назначает дом, но писатель может отказать в приёме.
            # Без проверки фактического приёма строка исчезла бы из обоих домов.
            #
            # Предикат нарочно НЕ повторяет правил канона (иначе завёлся бы
            # второй дом правила приёма — та самая болезнь, которую лечит вся
            # эта работа). Он спрашивает ФАКТ: строка в каноне уже есть?
            # Это тот же инвариант, что Гейт-2 полноты у промоута крови, только
            # поперёк границы. И он самозалечивается: как только имя сведут
            # к канону и строка туда доедет, следующий прогон уберёт её отсюда.
            if _in_canon(canon_keys, r):
                plan["канон принял (пропуск)"] += 1
                continue
            plan["дом канон, но канон НЕ принял — держим здесь"] += 1
        if home is None:
            # Класс, которого человек ещё не судил. Едет СЮДА — направление
            # безопасное (не в биохимию крови), но молча не проходит: счётчик
            # здесь, красное — в ночном датчике.
            no_verdict[pt] += 1
        # Качественный результат законен без числа и хранится в value_text.
        # Отказ принимать его лишил бы строку нового дома при переносе.
        val = r.get("value")
        txt = (r.get("value_text") or "").strip()
        if val is None or (isinstance(val, str) and not val.strip()):
            if not txt:
                plan["пропуск (нет ни числа, ни текста)"] += 1
                continue
            plan["качественный результат (value_text)"] += 1
        plan[f"specialized:{pt}"] += 1
        if home == "specialized":
            accepted_here.append(r["id"])
        try:
            vnum = float(val)
        except (TypeError, ValueError):
            vnum = None
        src = r.get("source_file") or ""
        src = f"doc:{src}" if src and not src.startswith("doc:") else (src or "doc:blcanon1")
        spec_hint, meth = _lc.class_hint(pt, r.get("panel"))
        # 2026-07-31: материал, ПРОЧИТАННЫЙ из бланка, старше литерала правила.
        # Было наоборот, и правило присваивало всем микроэлементам specimen="blood"
        # жёстко — материал не измерялся, а назначался.
        _read = (_lc.specimen_class(r.get("specimen"))
                 if (r.get("specimen_source") or "") in
                 ("read_header", "read_footer", "continuation") else None)
        to_insert.append((
            r.get("date"), src, pt, _read or spec_hint,
            r.get("raw_name"), r.get("canonical_name"),
            vnum, None if vnum is not None else (
                (str(val).strip() if val is not None and str(val).strip() else None) or txt or None),
            r.get("unit"), r.get("ref_low"), r.get("ref_high"),
            r.get("doc_flag"), meth,
        ))

    result = {"run_id": run_id, "total": len(rows), "plan": dict(plan),
              "no_verdict": dict(no_verdict),
              "to_insert": len(to_insert), "executed": False}
    if not execute:
        return result

    # Замена по ключу, а не по документу: частичное перечитывание должно
    # сохранять строки страниц, которые текущий прогон не охватил.
    # Удаление по source перед вставкой безопасно только для полного прогона.
    #
    # Докстринг обещал «идемпотентно по UNIQUE(date, analyte_raw, source, panel_type)» —
    # теперь код делает именно это. Обещание и механизм разъезжались молча.
    #
    # Сосед по слою (`lab_promote`) переживает частичный прогон именно потому, что
    # удаляет ТОЛЬКО покрытое ключом. Два писателя одного слоя обязаны иметь одну
    # семантику замены; здесь она сведена.
    keys = {(t[0], t[4], t[1], t[2]) for t in to_insert}   # date, analyte_raw, source, panel
    with _hdb.get_conn() as conn:
        before = conn.execute("SELECT COUNT(*) FROM specialized_lab_results").fetchone()[0]
        conn.executemany(
            "DELETE FROM specialized_lab_results "
            "WHERE date=? AND analyte_raw=? AND source=? AND panel_type=?", sorted(keys))
        conn.executemany(
            "INSERT OR IGNORE INTO specialized_lab_results "
            "(date, source, panel_type, specimen, analyte_raw, analyte_canonical, "
            " value, value_text, unit, ref_low, ref_high, flag, method) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", to_insert)
        # Терминальный статус — только поверх машинных статусов (`pending`/NULL/
        # `auto`/`gold`): решение человека (`review`/`rejected`) и след промоута
        # крови (`promoted`) не затираются.
        conn.executemany(
            "UPDATE lab_results_staging SET review_status='specialized', "
            "status_changed_at=datetime('now') "
            "WHERE id=? AND COALESCE(review_status,'pending') IN ('pending','auto','gold')",
            [(i,) for i in accepted_here])
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM specialized_lab_results").fetchone()[0]
    result.update({"executed": True, "before": before, "after": after,
                   "inserted": after - before, "marked_specialized": len(accepted_here)})
    return result
