"""Карантин мерцающих пар — СОСТОЯНИЕ, а не diff последней недели.

До 2026-07-26 карантин вычислялся из `flicker_vs_prev.entered` последнего снимка pass-set:
пара, впервые вошедшая в состав, помечалась `pending_adjudication` «до вердикта онлайн-
контроллера». На следующей неделе та же пара уже не была `entered`, diff пустел — и метка
исчезала сама, без всякого вердикта (внешний аудит validation_gate, P1-03: воспроизведено).
То есть предохранитель снимал себя ровно через один стабильный прогон.

Здесь состояние живёт в БД и снимается ТОЛЬКО явным решением человека
(`scripts/adjudicate_quarantine.py`, решение владельца 2026-07-26 — CLI, чтобы вердикт выносился
глядя на числа, а не с телефона). Онлайн-контроллер §5 появится по триггеру мерцания и станет
вторым источником вердиктов; форма записи для него та же.

`method_epoch` в ключе намеренно: смена метода — это новый вопрос о той же паре, старый
вердикт к нему не относится.
"""
from __future__ import annotations

_STATUSES = ("pending", "admitted", "rejected")
_FAMILIES = ("D", "A", "q_lag")


def method_epoch() -> str:
    """Эпоха метода = версия замороженной семьи. ЕДИНЫЙ ИСТОЧНИК для писателя и для CLI.

    Найдено мной 2026-07-26 при доводке R4-08 и воспроизведено: CLI вердиктов был МЁРТВ.
    Гейт ставит пару под `signal_family_v7`, а `scripts/adjudicate_quarantine.py` звал
    `resolve_quarantine` без `method_epoch` — с дефолтом `''`. SQL `WHERE method_epoch=?`
    не совпадал никогда: rowcount=0, CLI печатал «пара не найдена среди pending текущей
    эпохи метода» и выходил с кодом 1. При этом СПИСОК пару показывал (там фильтра по эпохе
    нет). То есть единственный механизм снятия карантина — тот самый, который владелец выбрал
    осознанно («CLI с обоснованием») — не мог снять ни одной пары, а выглядел работающим.
    Это ровно класс «claim сильнее механизма»: карантин был бы вечным, и предохранитель
    превратился бы в глухую стену. Формула живёт здесь, а не в двух модулях, чтобы
    разъехаться было нечему.

    Импорт ленивый: signal_family лёгкий (pathlib+yaml), но CLI не должен падать из-за него,
    если YAML сломан — пусть падает громко в своём месте, а не на импорте модуля БД."""
    import signal_family as _sf
    # С 23.09 — отдельное поле эпохи метода (не VERSION): ре-объявление семьи без смены
    # метода карантина не должно делать pending-строки «чужими» (нить family-epoch).
    return f"signal_family_v{_sf.QUARANTINE_EPOCH}"


def corrupt_quarantine_rows(conn=None) -> list[dict]:
    """Строки, которых по контракту существовать не может. ПУСТОЙ СПИСОК ≠ «всё хорошо молча».

    VG-R4-05: порча колонок была НЕВИДИМА. `pending_quarantine_pairs` фильтрует по
    `status='pending'` и точному `method_epoch`; строка со статусом 'PENDING', пустой семьёй
    или чужой эпохой просто не попадала в выборку. Датчик застоя считал по тем же строкам и
    рапортовал `{pending: 0, stuck: 0}` — то есть «карантин пуст». Так «карантина нет» и
    «карантин нечитаем» снова стали неотличимы: ровно тот класс ошибки, ради которого весь
    этот подсистемный ремонт и затевался.

    Читает ВСЮ таблицу без фильтра по контракту — иначе датчик слепнет ровно там, где нужен.
    Возвращает [{"id","pair","family","status","method_epoch","why"}]."""
    sql = ("SELECT id, pair, family, method_epoch, status, resolution, entered_at "
           "FROM passset_quarantine")

    try:
        _current = method_epoch()          # действующая эпоха: единый источник, не литерал
    except Exception as _exc:              # noqa: BLE001 — сломанный YAML семьи кричит в своём месте
        _current = None
        print(f"  ⚠️ действующая эпоха не определена ({_exc!r}) — проверка чужой эпохи пропущена")

    def _run(c):
        out = []
        for _id, pair, family, epoch, status, resolution, entered in c.execute(sql).fetchall():
            why = []
            if status not in _STATUSES:
                why.append(f"status={status!r} вне {_STATUSES}")
            if family not in _FAMILIES:
                why.append(f"family={family!r} вне {_FAMILIES}")
            if not (pair or "").strip():
                why.append("pair пуст")
            if epoch is None:
                why.append("method_epoch = NULL (не '' — это разные вещи для сравнения)")
            elif status == "pending" and _current is not None and epoch != _current:
                # VG-R5-05/06 (ревью R5, воспроизведено): проверялся только NULL. Строка с ЧУЖОЙ
                # эпохой (`signal_family_v999`, опечатка, ручная правка) невидима читателю веры —
                # он фильтрует точным совпадением, — но датчик считал её валидной. Пара при этом
                # НЕ защищена: карантин как бы есть, а публикацию он не держит. Разрешённая
                # история чужих эпох законна (метод сменился, вопрос закрыт), pending — нет:
                # незакрытый вопрос обязан относиться к действующему методу.
                why.append(f"pending под чужой эпохой {epoch!r} (действующая {_current!r}): "
                           f"строка есть, но публикацию она не держит")
            if not (entered or "").strip():
                why.append("entered_at пуст → возраст не считается, датчик застоя слеп")
            if status in ("admitted", "rejected") and not (resolution or "").strip():
                why.append("вердикт без обоснования")
            if why:
                out.append({"id": _id, "pair": pair, "family": family, "status": status,
                            "method_epoch": epoch, "why": "; ".join(why)})
        return out

    if conn is not None:
        return _run(conn)
    with _conn() as c:
        return _run(c)


def quarantine_schema_guarded(conn=None) -> bool:
    """Есть ли на ЖИВОЙ таблице CHECK-ограничения, или она создана до их появления.

    Нужно потому, что `CREATE TABLE IF NOT EXISTS` не достраивает ограничения к существующей
    таблице, а SQLite не умеет `ALTER TABLE ADD CONSTRAINT`. Боевая таблица создана 2026-07-26
    ДО ревью R4, то есть без CHECK'ов. Молчать об этом было бы ровно тем «claim сильнее
    механизма», который ревью и нашло: контракт объявлен в DDL, а живая таблица не защищена.
    False → защита держится только на `corrupt_quarantine_rows`, и это надо знать."""
    sql = ("SELECT sql FROM sqlite_master WHERE type='table' AND name='passset_quarantine'")

    def _run(c):
        row = c.execute(sql).fetchone()
        return bool(row and row[0] and "CHECK" in row[0].upper())

    if conn is not None:
        return _run(conn)
    with _conn() as c:
        return _run(c)


def _conn():
    """health_db импортируется ЛЕНИВО: он же ре-экспортирует функции этого модуля, и импорт
    на уровне модуля даёт цикл, если quarantine_db импортировали первым (известная ловушка
    проекта — «import health_db ПЕРВЫМ»). Ленивый импорт снимает порядок как условие."""
    import health_db as _hdb
    return _hdb.get_conn()


def queue_quarantine(entries: list[dict], method_epoch: str = "", conn=None) -> int:
    """Ставит впервые вошедшие пары в карантин. Уже стоящие (любой статус) НЕ трогает.

    entries: [{"pair": "a×b", "family": "D"}]. Возвращает число НОВЫХ записей.
    Идемпотентна: повторный прогон той же недели не воскрешает уже разрешённую пару.
    """
    if not entries:
        return 0
    sql_ins = ("INSERT OR IGNORE INTO passset_quarantine "
               "(pair, family, method_epoch, entered_at, status) "
               "VALUES (?,?,?,datetime('now'),'pending')")
    rows = [(e["pair"], e["family"], method_epoch or "") for e in entries]

    def _run(c):
        n = 0
        for r in rows:
            n += c.execute(sql_ins, r).rowcount
        return n

    if conn is not None:
        return _run(conn)
    with _conn() as c:
        n = _run(c)
        c.commit()
    return n


def pending_quarantine_pairs(method_epoch: str = "", conn=None) -> set:
    """Множество пар в карантине СЕЙЧАС (формат «a×b» и «pred→tgt+Nд») — то, что читает
    build_ai_summary. Пара остаётся здесь, пока её не разрешил человек или контроллер."""
    sql = ("SELECT pair FROM passset_quarantine "
           "WHERE status='pending' AND method_epoch = ?")
    if conn is not None:
        return {r[0] for r in conn.execute(sql, (method_epoch or "",)).fetchall()}
    with _conn() as c:
        return {r[0] for r in c.execute(sql, (method_epoch or "",)).fetchall()}


def quarantine_decisions(method_epoch: str = "", conn=None) -> dict:
    """РЕШЕНИЕ по каждой паре текущей эпохи: `{pair: "pending"|"admitted"|"rejected"}`.

    Зачем отдельно от `pending_quarantine_pairs` (ревью R5, VG-R5-01 — воспроизведено):
    читатель веры знал только множество pending, поэтому `admitted` и `rejected` были для него
    НЕРАЗЛИЧИМЫ — оба исчезали из выборки. Человек отклонял связь как артефакт, а конституции и
    GP-контекст получали её обратно как обычную подтверждённую находку. Это обход последнего
    человеческого гейта: отказ читался как разрешение.

    Множество pending — не решение, а его отсутствие. Три состояния обязаны ехать к читателю
    типом, а не вычитанием: `pending` — не находка (ждёт вердикта), `rejected` — не находка
    НИКОГДА, `admitted` — находка с провенансом.
    """
    sql = ("SELECT pair, status FROM passset_quarantine "
           "WHERE method_epoch = ? ORDER BY entered_at")

    def _run(c):
        return {p: s for p, s in c.execute(sql, (method_epoch or "",)).fetchall()}

    if conn is not None:
        return _run(conn)
    with _conn() as c:
        return _run(c)


def resolve_quarantine(pair: str, verdict: str, reason: str,
                       resolved_by: str = "human", method_epoch: str = "", conn=None) -> int:
    """Явный вердикт по паре. Возвращает число изменённых строк (0 = такой pending не было).

    Пустое обоснование не принимается: вердикт без причины — это не решение, а щелчок.
    """
    _allowed = tuple(s for s in _STATUSES if s != "pending")   # единый источник статусов
    if verdict not in _allowed:
        raise ValueError(f"verdict='{verdict}' — допустимы {'|'.join(_allowed)}")
    if not (reason or "").strip():
        raise ValueError("вердикт без обоснования не принимается (кто читает — тому и объяснять)")
    sql = ("UPDATE passset_quarantine SET status=?, resolution=?, resolved_by=?, "
           "resolved_at=datetime('now') "
           "WHERE pair=? AND method_epoch=? AND status='pending'")
    args = (verdict, reason.strip(), resolved_by, pair, method_epoch or "")
    if conn is not None:
        return conn.execute(sql, args).rowcount
    with _conn() as c:
        n = c.execute(sql, args).rowcount
        c.commit()
    return n


def quarantine_rows(status: "str | None" = "pending", conn=None) -> list[dict]:
    """Строки карантина для CLI и датчика застоя. status=None → все."""
    sql = ("SELECT pair, family, method_epoch, entered_at, status, resolved_at, "
           "resolution, resolved_by, "
           "CAST(julianday('now') - julianday(entered_at) AS INTEGER) AS age_days "
           "FROM passset_quarantine")
    args: tuple = ()
    if status:
        sql += " WHERE status=?"
        args = (status,)
    sql += " ORDER BY entered_at"
    cols = ("pair", "family", "method_epoch", "entered_at", "status",
            "resolved_at", "resolution", "resolved_by", "age_days")

    def _run(c):
        return [dict(zip(cols, r)) for r in c.execute(sql, args).fetchall()]

    if conn is not None:
        return _run(conn)
    with _conn() as c:
        return _run(c)
